"""Thin CLI client for patchright-cli.

Parses arguments, connects to the daemon socket, sends a JSON command,
receives the result, and prints it.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import sys
from pathlib import Path

import click

from patchright_cli import __version__
from patchright_cli.daemon import DEFAULT_PORT, ensure_daemon_running


def _load_config(config_path: str | None) -> dict:
    """Load JSON config file. If config_path is None, try .patchright-cli/config.json in cwd."""
    if config_path:
        p = Path(config_path)
    else:
        p = Path.cwd() / ".patchright-cli" / "config.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def _merge_config_with_options(config: dict, options: dict) -> dict:
    """Merge config dict with CLI options. CLI options take precedence."""
    merged = dict(config)
    for k, v in options.items():
        if v is not None:
            merged[k] = v
    return merged


def _send_command(command: str, args: list, options: dict, port: int = DEFAULT_PORT) -> dict:
    """Connect to daemon, send command, receive response."""
    msg = {
        "command": command,
        "args": args,
        "options": options,
        "cwd": os.getcwd(),
    }
    data = json.dumps(msg).encode("utf-8")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(120)  # generous timeout for slow operations
    try:
        sock.connect(("127.0.0.1", port))
        sock.sendall(struct.pack("!I", len(data)) + data)

        # Read length-prefixed response
        header = _recv_exact(sock, 4)
        length = struct.unpack("!I", header)[0]
        resp_data = _recv_exact(sock, length)
        return json.loads(resp_data.decode("utf-8"))
    finally:
        sock.close()


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    """Receive exactly n bytes from socket."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Connection closed by daemon")
        buf.extend(chunk)
    return bytes(buf)


# ---------------------------------------------------------------------------
# CLI definition
# ---------------------------------------------------------------------------

COMMANDS_HELP = {
    # Core
    "open": "open [url]           Open browser (starts daemon if needed)",
    "goto": "goto <url>           Navigate to URL",
    "click": "click <ref> [button]  Click element [--modifiers=Alt,Shift]",
    "dblclick": "dblclick <ref> [btn] Double-click [--modifiers=Alt,Shift]",
    "fill": "fill <ref> <value>   Fill text into element [--submit]",
    "type": "type <text>          Type text via keyboard [--submit]",
    "hover": "hover <ref>          Hover over element",
    "select": "select <ref> <value> Select dropdown option",
    "check": "check <ref>          Check checkbox/radio",
    "uncheck": "uncheck <ref>        Uncheck checkbox/radio",
    "snapshot": "snapshot [ref]        Take accessibility snapshot [--filename=F] [--depth=N]",
    "eval": "eval <expr> [ref]     Evaluate JavaScript [--file=F or stdin]",
    "text": "text <ref|selector>  Get text content of element",
    "screenshot": "screenshot [ref]     Save screenshot [--full-page] [--filename=F]",
    "drag": "drag <from> <to>     Drag element to target",
    "close": "close                Close browser session",
    # Navigation
    "go-back": "go-back              Go back",
    "go-forward": "go-forward           Go forward",
    "reload": "reload               Reload page",
    "url": "url                  Print current URL",
    "title": "title                Print page title",
    # Keyboard
    "press": "press <key>          Press key",
    "keydown": "keydown <key>        Key down",
    "keyup": "keyup <key>          Key up",
    # Mouse
    "mousemove": "mousemove <x> <y>    Move mouse",
    "mousedown": "mousedown [button]   Mouse button down",
    "mouseup": "mouseup [button]     Mouse button up",
    "mousewheel": "mousewheel <dx> <dy> Scroll mouse wheel",
    # Scroll & Wait
    "scroll": "scroll <dx> <dy>      Scroll by pixel offset",
    "scroll-to": "scroll-to <ref>      Scroll element into view",
    "wait": "wait <ms>            Wait for milliseconds",
    "wait-for": "wait-for <ref>       Wait for element to appear [--state=hidden]",
    # Tabs
    "tab-list": "tab-list             List tabs",
    "tab-new": "tab-new [url]        Open new tab",
    "tab-close": "tab-close [index]    Close tab",
    "tab-select": "tab-select <index>   Switch to tab",
    # Storage
    "cookie-list": "cookie-list          List cookies [--domain=D] [--path=P]",
    "cookie-get": "cookie-get <name>    Get cookie",
    "cookie-set": "cookie-set <n> <v>   Set cookie [--domain --path --expires --httpOnly --secure --sameSite]",
    "cookie-delete": "cookie-delete <name> Delete cookie",
    "cookie-clear": "cookie-clear         Clear all cookies",
    "cookie-import": "cookie-import <file> Import cookies from JSON",
    "cookie-export": "cookie-export [file] Export cookies to JSON",
    "localstorage-list": "localstorage-list    List localStorage",
    "localstorage-get": "localstorage-get <k> Get localStorage item",
    "localstorage-set": "localstorage-set <k> <v>  Set localStorage item",
    "localstorage-delete": "localstorage-delete <k>   Delete localStorage item",
    "localstorage-clear": "localstorage-clear   Clear localStorage",
    # Dialog
    "dialog-accept": "dialog-accept [text] Accept next dialog",
    "dialog-dismiss": "dialog-dismiss       Dismiss next dialog",
    # Permissions
    "grant-permissions": "grant-permissions <perms>  Grant permissions [--origin=url]",
    # Upload / Resize
    "upload": "upload <file> [ref]   Upload file to input",
    "resize": "resize <w> <h>        Resize viewport",
    # State
    "state-save": "state-save [file]    Save cookies+storage to JSON",
    "state-load": "state-load <file>    Load saved state",
    # Session storage
    "sessionstorage-list": "sessionstorage-list  List sessionStorage",
    "sessionstorage-get": "sessionstorage-get <k> Get sessionStorage item",
    "sessionstorage-set": "sessionstorage-set <k> <v> Set sessionStorage item",
    "sessionstorage-delete": "sessionstorage-delete <k> Delete sessionStorage item",
    "sessionstorage-clear": "sessionstorage-clear Clear sessionStorage",
    # Route
    "route": "route <pattern> [--status=N] [--body=S] [--content-type=T] [--header=K:V]  Mock requests",
    "route-list": "route-list           List active routes",
    "unroute": "unroute [pattern]    Remove route(s)",
    "network-state-set": "network-state-set <state>  Set online/offline (online|offline)",
    # Run code
    "run-code": "run-code <code>      Run raw JS in page context [--file=F or stdin]",
    # Tracing
    "tracing-start": "tracing-start        Start Playwright tracing",
    "tracing-stop": "tracing-stop         Stop tracing and save",
    # Video
    "video-start": "video-start          Start video recording",
    "video-stop": "video-stop           Stop recording and save [--filename=F]",
    "video-chapter": "video-chapter <title>  Add chapter marker to video",
    # PDF
    "pdf": "pdf [--filename=F]   Save page as PDF",
    # DevTools
    "console": "console [level]      Show console messages [--clear]",
    "network": "network              Show network requests [--static] [--clear]",
    # Session
    "list": "list                 List sessions",
    "close-all": "close-all            Close all sessions",
    "kill-all": "kill-all             Kill all sessions",
    "delete-data": "delete-data          Delete persistent profile",
}

ALL_COMMANDS = list(COMMANDS_HELP.keys())


def _print_help():
    click.echo("patchright-cli — Undetected browser automation CLI\n")
    click.echo("Usage: patchright-cli [OPTIONS] <command> [args...]\n")
    click.echo("Options:")
    click.echo("  --headless          Run headless (default: headed)")
    click.echo("  --persistent        Use persistent profile")
    click.echo("  --profile=<path>    Custom profile directory")
    click.echo("  --proxy=<url>       Proxy server (e.g. http://host:port, socks5://host:port)")
    click.echo("  -s=<name>           Named session (default: 'default')")
    click.echo("  --port=<n>          Daemon port (default: 9321)")
    click.echo("  --config=<path>     Load config from JSON file")
    click.echo("  --timeout-action=ms   Default action timeout")
    click.echo("  --timeout-navigation=ms Default navigation timeout")
    click.echo("  --device=<name>     Emulate a device (e.g. 'iPhone 15')")
    click.echo("  --viewport-size=WxH Set viewport size")
    click.echo("  --locale=<code>     Locale (e.g. en-US)")
    click.echo("  --timezone=<id>     Timezone ID")
    click.echo("  --geolocation=lat,lon Geolocation override")
    click.echo("  --user-agent=<ua>   Custom user agent")
    click.echo("  --grant-permissions=P Comma-separated permissions to grant")
    click.echo("  --version           Show version")
    click.echo("  --help              Show this help\n")
    click.echo("Commands:")
    # Group by category
    categories = [
        (
            "Core",
            [
                "open",
                "goto",
                "click",
                "dblclick",
                "fill",
                "type",
                "hover",
                "select",
                "check",
                "uncheck",
                "snapshot",
                "eval",
                "text",
                "screenshot",
                "drag",
                "close",
            ],
        ),
        ("Navigation", ["go-back", "go-forward", "reload", "url", "title"]),
        ("Keyboard", ["press", "keydown", "keyup"]),
        ("Mouse", ["mousemove", "mousedown", "mouseup", "mousewheel"]),
        ("Scroll & Wait", ["scroll", "scroll-to", "wait", "wait-for"]),
        ("Tabs", ["tab-list", "tab-new", "tab-close", "tab-select"]),
        ("Dialog", ["dialog-accept", "dialog-dismiss"]),
        ("Upload/Resize", ["upload", "resize"]),
        ("State", ["state-save", "state-load"]),
        (
            "Storage",
            [
                "cookie-list",
                "cookie-get",
                "cookie-set",
                "cookie-delete",
                "cookie-clear",
                "cookie-import",
                "cookie-export",
                "localstorage-list",
                "localstorage-get",
                "localstorage-set",
                "localstorage-delete",
                "localstorage-clear",
                "sessionstorage-list",
                "sessionstorage-get",
                "sessionstorage-set",
                "sessionstorage-delete",
                "sessionstorage-clear",
            ],
        ),
        ("Permissions", ["grant-permissions"]),
        ("Route", ["route", "route-list", "unroute", "network-state-set"]),
        ("Code", ["run-code"]),
        ("Tracing", ["tracing-start", "tracing-stop"]),
        ("Video", ["video-start", "video-stop", "video-chapter"]),
        ("PDF", ["pdf"]),
        ("DevTools", ["console", "network"]),
        ("Session", ["list", "close-all", "kill-all", "delete-data"]),
    ]
    for cat_name, cmds in categories:
        click.echo(f"\n  {cat_name}:")
        for c in cmds:
            click.echo(f"    {COMMANDS_HELP[c]}")
    click.echo()


def main():
    """Entry point for the CLI."""
    argv = sys.argv[1:]

    # Parse global options manually (before the command)
    headless = False
    persistent = False
    profile = None
    proxy = None
    config_path = None
    session_name = os.environ.get("PATCHRIGHT_CLI_SESSION", "default")
    port = DEFAULT_PORT
    extra_opts: dict = {}

    # Extract options
    remaining = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--headless":
            headless = True
        elif arg == "--persistent":
            persistent = True
        elif arg.startswith("--profile="):
            profile = arg.split("=", 1)[1]
        elif arg == "--profile" and i + 1 < len(argv):
            i += 1
            profile = argv[i]
        elif arg.startswith("--proxy="):
            proxy = arg.split("=", 1)[1]
        elif arg == "--proxy" and i + 1 < len(argv):
            i += 1
            proxy = argv[i]
        elif arg.startswith("-s="):
            session_name = arg.split("=", 1)[1]
        elif arg == "-s" and i + 1 < len(argv):
            i += 1
            session_name = argv[i]
        elif arg.startswith("--port="):
            try:
                port = int(arg.split("=", 1)[1])
            except ValueError:
                click.echo(f"Invalid port value: {arg}", err=True)
                sys.exit(1)
        elif arg == "--port" and i + 1 < len(argv):
            i += 1
            try:
                port = int(argv[i])
            except ValueError:
                click.echo(f"Invalid port value: {argv[i]}", err=True)
                sys.exit(1)
        elif arg.startswith("--config="):
            config_path = arg.split("=", 1)[1]
        elif arg == "--config" and i + 1 < len(argv):
            i += 1
            config_path = argv[i]
        elif arg.startswith("--device="):
            extra_opts["device"] = arg.split("=", 1)[1]
        elif arg == "--device" and i + 1 < len(argv):
            i += 1
            extra_opts["device"] = argv[i]
        elif arg.startswith("--viewport-size="):
            vw, vh = arg.split("=", 1)[1].split("x", 1)
            extra_opts["viewport"] = {"width": vw, "height": vh}
        elif arg == "--viewport-size" and i + 1 < len(argv):
            i += 1
            vw, vh = argv[i].split("x", 1)
            extra_opts["viewport"] = {"width": vw, "height": vh}
        elif arg.startswith("--locale="):
            extra_opts["locale"] = arg.split("=", 1)[1]
        elif arg == "--locale" and i + 1 < len(argv):
            i += 1
            extra_opts["locale"] = argv[i]
        elif arg.startswith("--timezone="):
            extra_opts["timezone"] = arg.split("=", 1)[1]
        elif arg == "--timezone" and i + 1 < len(argv):
            i += 1
            extra_opts["timezone"] = argv[i]
        elif arg.startswith("--geolocation="):
            lat, lon = arg.split("=", 1)[1].split(",", 1)
            extra_opts["geolocation"] = {"lat": lat, "lon": lon}
        elif arg == "--geolocation" and i + 1 < len(argv):
            i += 1
            lat, lon = argv[i].split(",", 1)
            extra_opts["geolocation"] = {"lat": lat, "lon": lon}
        elif arg.startswith("--user-agent="):
            extra_opts["user-agent"] = arg.split("=", 1)[1]
        elif arg == "--user-agent" and i + 1 < len(argv):
            i += 1
            extra_opts["user-agent"] = argv[i]
        elif arg.startswith("--grant-permissions="):
            extra_opts["grant-permissions"] = arg.split("=", 1)[1]
        elif arg == "--grant-permissions" and i + 1 < len(argv):
            i += 1
            extra_opts["grant-permissions"] = argv[i]
        elif arg in ("--version", "-v"):
            click.echo(f"patchright-cli {__version__}")
            sys.exit(0)
        elif arg in ("--help", "-h"):
            _print_help()
            sys.exit(0)
        else:
            remaining.append(arg)
        i += 1

    if not remaining:
        _print_help()
        sys.exit(0)

    command = remaining[0]
    args = remaining[1:]

    if command not in ALL_COMMANDS:
        click.echo(f"Unknown command: {command}\n", err=True)
        _print_help()
        sys.exit(1)

    # Separate --key=value and --flag from positional args
    positional_args = []
    cmd_opts: dict = {}
    for a in args:
        if a.startswith("--") and "=" in a:
            k, v = a[2:].split("=", 1)
            cmd_opts[k] = v
        elif a.startswith("--"):
            cmd_opts[a[2:]] = True
        else:
            positional_args.append(a)
    args = positional_args
    # Merge: global opts as base, command-level opts take precedence
    extra_opts = {**extra_opts, **cmd_opts}

    # For eval/run-code: support --file=<path> and stdin via "-"
    if command in ("eval", "run-code"):
        file_opt = extra_opts.pop("file", None)
        if file_opt:
            # Read JS from file
            try:
                with open(file_opt, encoding="utf-8") as f:
                    args = [f.read()]
            except FileNotFoundError:
                click.echo(f"File not found: {file_opt}", err=True)
                sys.exit(1)
        elif args and args[0] == "-":
            # Read JS from stdin
            args = [sys.stdin.read()]
        elif not args and not sys.stdin.isatty():
            # Piped input with no positional arg
            args = [sys.stdin.read()]
        if not args:
            click.echo(f"'{command}' requires a JS expression, --file=<path>, or piped stdin.", err=True)
            sys.exit(1)

    # Build options dict (config file values are overridden by explicit CLI flags)
    config = _load_config(config_path)
    options = {"session": session_name, **_merge_config_with_options(config, extra_opts)}
    if headless:
        options["headless"] = True
    if persistent:
        options["persistent"] = True
    if profile:
        options["profile"] = profile
    if proxy:
        options["proxy"] = proxy

    # Ensure daemon is running (auto-start for all commands)
    try:
        ensure_daemon_running(port, headless)
    except RuntimeError as e:
        click.echo(str(e), err=True)
        sys.exit(1)

    # Send command to daemon
    try:
        response = _send_command(command, args, options, port)
    except ConnectionError as e:
        click.echo(f"Connection error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    # Print output
    output = response.get("output", "")
    if output:
        click.echo(output)

    success = response.get("success", True)
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
