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

import click

from patchright_cli import __version__
from patchright_cli.daemon import DEFAULT_PORT, ensure_daemon_running


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
    "fill": "fill <ref> <value>   Fill text into element",
    "type": "type <text>          Type text via keyboard",
    "hover": "hover <ref>          Hover over element",
    "select": "select <ref> <value> Select dropdown option",
    "check": "check <ref>          Check checkbox/radio",
    "uncheck": "uncheck <ref>        Uncheck checkbox/radio",
    "snapshot": "snapshot             Take accessibility snapshot",
    "eval": "eval <expr>          Evaluate JavaScript",
    "screenshot": "screenshot [ref]     Save screenshot [--full-page] [--filename=F]",
    "drag": "drag <from> <to>     Drag element to target",
    "close": "close                Close browser session",
    # Navigation
    "go-back": "go-back              Go back",
    "go-forward": "go-forward           Go forward",
    "reload": "reload               Reload page",
    # Keyboard
    "press": "press <key>          Press key",
    "keydown": "keydown <key>        Key down",
    "keyup": "keyup <key>          Key up",
    # Mouse
    "mousemove": "mousemove <x> <y>    Move mouse",
    "mousedown": "mousedown [button]   Mouse button down",
    "mouseup": "mouseup [button]     Mouse button up",
    "mousewheel": "mousewheel <dx> <dy> Scroll mouse wheel",
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
    "localstorage-list": "localstorage-list    List localStorage",
    "localstorage-get": "localstorage-get <k> Get localStorage item",
    "localstorage-set": "localstorage-set <k> <v>  Set localStorage item",
    "localstorage-delete": "localstorage-delete <k>   Delete localStorage item",
    "localstorage-clear": "localstorage-clear   Clear localStorage",
    # Dialog
    "dialog-accept": "dialog-accept [text] Accept next dialog",
    "dialog-dismiss": "dialog-dismiss       Dismiss next dialog",
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
    "route": "route <pattern> [--status=N] [--body=S] [--header=K:V] [--remove-header=H]  Mock requests",
    "route-list": "route-list           List active routes",
    "unroute": "unroute [pattern]    Remove route(s)",
    # Run code
    "run-code": "run-code <code>      Run raw JS in page context",
    # Tracing
    "tracing-start": "tracing-start        Start Playwright tracing",
    "tracing-stop": "tracing-stop         Stop tracing and save",
    # Video
    "video-start": "video-start          Start video recording",
    "video-stop": "video-stop [file]    Stop recording and save",
    # PDF
    "pdf": "pdf [--filename=F]   Save page as PDF",
    # DevTools
    "console": "console [level]      Show console messages",
    "network": "network              Show network requests",
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
    click.echo("  -s=<name>           Named session (default: 'default')")
    click.echo("  --port=<n>          Daemon port (default: 9321)")
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
                "screenshot",
                "drag",
                "close",
            ],
        ),
        ("Navigation", ["go-back", "go-forward", "reload"]),
        ("Keyboard", ["press", "keydown", "keyup"]),
        ("Mouse", ["mousemove", "mousedown", "mouseup", "mousewheel"]),
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
        ("Route", ["route", "route-list", "unroute"]),
        ("Code", ["run-code"]),
        ("Tracing", ["tracing-start", "tracing-stop"]),
        ("Video", ["video-start", "video-stop"]),
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
    session_name = "default"
    port = DEFAULT_PORT

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
        elif arg.startswith("--profile") and i + 1 < len(argv):
            i += 1
            profile = argv[i]
        elif arg.startswith("-s="):
            session_name = arg.split("=", 1)[1]
        elif arg == "-s" and i + 1 < len(argv):
            i += 1
            session_name = argv[i]
        elif arg.startswith("--port="):
            port = int(arg.split("=", 1)[1])
        elif arg == "--port" and i + 1 < len(argv):
            i += 1
            port = int(argv[i])
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
    extra_opts = {}
    for a in args:
        if a.startswith("--") and "=" in a:
            k, v = a[2:].split("=", 1)
            extra_opts[k] = v
        elif a.startswith("--"):
            extra_opts[a[2:]] = True
        else:
            positional_args.append(a)
    args = positional_args

    # Build options dict
    options = {"session": session_name, **extra_opts}
    if headless:
        options["headless"] = True
    if persistent:
        options["persistent"] = True
    if profile:
        options["profile"] = profile

    # Ensure daemon is running (auto-start for 'open', require for others)
    try:
        if command == "open":
            ensure_daemon_running(port, headless)
        else:
            # Try connecting first; if fails, tell user to open
            import socket as _socket

            try:
                sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
                sock.settimeout(1)
                sock.connect(("127.0.0.1", port))
                sock.close()
            except (ConnectionRefusedError, OSError, TimeoutError):
                click.echo(
                    f"Daemon is not running on port {port}. Run 'patchright-cli open' first.",
                    err=True,
                )
                sys.exit(1)
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
