"""Thin CLI client for patchright-cli.

Parses arguments, connects to the daemon socket, sends a JSON command,
receives the result, and prints it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import struct
import sys
import time
from pathlib import Path

import click

from patchright_cli import __version__
from patchright_cli.daemon import DEFAULT_PORT, PROTOCOL_VERSION, ensure_daemon_running, read_daemon_token, token_path


def _soften_output_encoding(*streams) -> None:
    """Make unencodable characters print as `?` instead of killing the command.

    Windows consoles default to a legacy code page (GBK on this machine), and a
    single character outside it raised UnicodeEncodeError from click.echo --
    losing a whole page snapshot over one glyph.
    """
    for stream in streams:
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def _load_config(config_path: str | None) -> dict:
    """Load JSON config file. If config_path is None, try .patchright-cli/config.json in cwd."""
    p = Path(config_path) if config_path else Path.cwd() / ".patchright-cli" / "config.json"
    if not p.exists():
        return {}
    try:
        config = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise UsageError(f"cannot read config file {p}: {e}", "help: fix the file, or pass --config=<path>") from None
    if not isinstance(config, dict):
        raise UsageError(f"config file {p} must hold a JSON object", 'help: e.g. {"headless": true}')
    return config


# Options that shape a browser context when it is created; meaningless (and
# rejected) for `attach --context=host`, whose context already exists.
_CONTEXT_EMULATION_KEYS = frozenset(
    {
        "device",
        "mobile",
        "viewport",
        "locale",
        "timezone",
        "geolocation",
        "user-agent",
        "userAgent",
        "grant-permissions",
        "grantPermissions",
    }
)


def _merge_config_with_options(config: dict, options: dict) -> dict:
    """Merge config dict with CLI options. CLI options take precedence."""
    merged = dict(config)
    for k, v in options.items():
        if v is not None:
            merged[k] = v
    return merged


# Sections whose `- ` lines are the answer rather than decoration. Everything
# else keeps the strip-all behaviour. Without this, --raw ate the annotated
# screenshot path and any reviewer notes written as a bullet list.
_RAW_CONTENT_SECTIONS = ("### Annotation", "### Notes")


def _strip_raw_output(output: str) -> str:
    """Strip page info and snapshot decorations, returning only the result value."""
    lines = output.splitlines()
    result_lines = []
    in_section = False
    for line in lines:
        if line.startswith("### "):
            in_section = not line.startswith(_RAW_CONTENT_SECTIONS)
            continue
        if in_section and (line.startswith("- ") or line.startswith("[Snapshot](")):
            continue
        in_section = False
        result_lines.append(line)
    return "\n".join(result_lines).strip()


def _detect_agent_dirs(home: Path | None = None) -> list[tuple[str, Path]]:
    """Detect installed AI agent config directories."""
    if home is None:
        home = Path.home()
    agents = [
        ("Claude Code", home / ".claude"),
        ("Gemini CLI", home / ".gemini"),
        ("Codex CLI", home / ".codex"),
        ("OpenCode", home / ".opencode"),
    ]
    return [(name, d) for name, d in agents if d.exists()]


def _get_bundled_skills_dir() -> Path | None:
    """Get the path to bundled skill files in the installed package."""
    pkg_dir = Path(__file__).parent / "_skills" / "patchright-cli"
    if pkg_dir.exists():
        return pkg_dir
    # Fallback: check if running from source
    src_dir = Path(__file__).resolve().parent.parent.parent / "skills" / "patchright-cli"
    if src_dir.exists():
        return src_dir
    return None


def _read_skill_text(path: Path) -> str | None:
    """Read a SKILL.md, normalising line endings. None when it is not there."""
    try:
        return path.read_text(encoding="utf-8").replace("\r\n", "\n")
    except OSError:
        return None


def _stale_skill_installs(agents: list[tuple[str, Path]] | None = None) -> list[tuple[str, Path]]:
    """Installed skill copies that no longer match the bundled one.

    Line endings are normalised first -- git and editors rewrite them, and a
    CRLF copy of an identical skill is not stale.
    """
    source = _get_bundled_skills_dir()
    if source is None:
        return []
    bundled = _read_skill_text(source / "SKILL.md")
    if bundled is None:
        return []

    stale = []
    for name, agent_dir in _detect_agent_dirs() if agents is None else agents:
        target = agent_dir / "skills" / "patchright-cli"
        installed = _read_skill_text(target / "SKILL.md")
        if installed is not None and installed != bundled:
            stale.append((name, target))
    return stale


_SKILL_WARN_INTERVAL = 24 * 60 * 60


def _skill_check_stamp() -> Path:
    return Path.home() / ".patchright-cli" / "skill-check.json"


def _stamp_time(data: dict) -> float:
    """A stamp's `at`, or 0 (long ago) when it is missing or not a number."""
    try:
        return float(data.get("at", 0))
    except (TypeError, ValueError):
        return 0.0


def _skill_warning_is_throttled() -> bool:
    """True when this version already warned within the last interval.

    Every command is its own process, so an unthrottled warning would repeat
    on each link of a chain and bury the command output an agent is reading.
    """
    import time

    stamp = _skill_check_stamp()
    try:
        data = json.loads(stamp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}  # a stamp this CLI did not write must not break every command
    if data.get("version") == __version__ and time.time() - _stamp_time(data) < _SKILL_WARN_INTERVAL:
        return True
    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(json.dumps({"version": __version__, "at": time.time()}), encoding="utf-8")
    except OSError:
        pass
    return False


def _warn_stale_skills() -> None:
    """Warn on stderr when an installed skill is older than this build.

    An agent reading a stale SKILL.md will use commands and flags that no
    longer match the tool. Warns at most once a day per version; set
    PATCHRIGHT_CLI_NO_SKILL_CHECK=1 to silence it entirely.
    """
    if os.environ.get("PATCHRIGHT_CLI_NO_SKILL_CHECK"):
        return
    try:
        stale = _stale_skill_installs()
    except Exception:
        return
    if not stale or _skill_warning_is_throttled():
        return
    for name, target in stale:
        click.echo(
            f"warning: {name} skill at {target} does not match patchright-cli v{__version__}. "
            f"Run `patchright-cli install --skills` to update it.",
            err=True,
        )


def _install_skills_to_dir(target: Path) -> None:
    """Copy SKILL.md and references/ to target directory."""
    import shutil

    source = _get_bundled_skills_dir()
    if source is None:
        raise FileNotFoundError("Could not find bundled skill files.")

    target.mkdir(parents=True, exist_ok=True)

    # Copy SKILL.md
    shutil.copy2(source / "SKILL.md", target / "SKILL.md")

    # Copy references/ if it exists
    refs_src = source / "references"
    if refs_src.exists():
        refs_dst = target / "references"
        if refs_dst.exists():
            shutil.rmtree(refs_dst)
        shutil.copytree(refs_src, refs_dst)


_INSTALL_USAGE = "usage: patchright-cli install --skills | --hooks [--uninstall] [--home=DIR]"
_INSTALL_FLAGS = "--skills, --hooks, --uninstall, --home=DIR"


def _handle_install(args: list) -> None:
    """Handle the install command: --skills copies skill files, --hooks registers session-start hooks."""
    from patchright_cli import agent_hooks

    flags: set[str] = set()
    home: Path | None = None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--skills", "--hooks", "--uninstall"):
            flags.add(arg)
        elif arg.startswith("--home="):
            home = Path(arg.split("=", 1)[1]).expanduser()
        elif arg == "--home" and i + 1 < len(args):
            i += 1
            home = Path(args[i]).expanduser()
        else:
            click.echo(f"error: unknown flag {arg} for `install`")
            click.echo(f"help: valid flags for `install`: {_INSTALL_FLAGS} (--help always allowed)")
            sys.exit(2)
        i += 1

    if "--uninstall" in flags and "--hooks" not in flags:
        click.echo("error: --uninstall only applies to --hooks")
        click.echo("help: patchright-cli install --hooks --uninstall")
        sys.exit(2)

    if not flags & {"--skills", "--hooks"}:
        click.echo(_INSTALL_USAGE)
        click.echo("\nTo install the browser, use: python -m patchright install chromium")
        sys.exit(0)

    if "--hooks" in flags:
        uninstall = "--uninstall" in flags
        try:
            command = None if uninstall else agent_hooks.hook_command()
            results = agent_hooks.apply_hooks(home=home, uninstall=uninstall, command=command)
        except Exception as e:
            click.echo(f"error: {e}")
            sys.exit(1)
        for line in agent_hooks.format_results(results, command):
            click.echo(line)
        if any(r.status == "error" for r in results):
            sys.exit(1)
        if "--skills" not in flags:
            return

    agents = _detect_agent_dirs(home=home)
    if not agents:
        click.echo("No AI agent installations detected.")
        click.echo("\nManual install — copy skills to your agent's directory:")
        click.echo("  Claude Code:  ~/.claude/skills/patchright-cli/")
        click.echo("  Gemini CLI:   ~/.gemini/skills/patchright-cli/")
        click.echo("  Codex CLI:    ~/.codex/skills/patchright-cli/")
        click.echo("  OpenCode:     ~/.opencode/skills/patchright-cli/")
        sys.exit(0)

    for name, agent_dir in agents:
        target = agent_dir / "skills" / "patchright-cli"
        try:
            _install_skills_to_dir(target)
            click.echo(f"  Installed to {name}: {target}")
        except Exception as e:
            click.echo(f"  Failed for {name}: {e}", err=True)

    click.echo(f"\npatchright-cli v{__version__} skills installed.")


def _handle_mcp(args: list[str]) -> None:
    """Handle the optional MCP server command."""
    parser = argparse.ArgumentParser(
        prog="patchright-cli mcp",
        description="Run the MCP server. Stdio is the default transport.",
    )
    parser.add_argument("--http", action="store_true", help="Use Streamable HTTP instead of stdio.")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host.")
    parser.add_argument("--port", type=int, default=8000, help="HTTP bind port.")
    options = parser.parse_args(args)

    if not 1 <= options.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if not options.http and (options.host != "127.0.0.1" or options.port != 8000):
        parser.error("--host and --port require --http")

    try:
        from patchright_cli.mcp_server import run_mcp_server
    except ImportError as exc:
        if exc.name not in {"fastmcp", "mcp", "pydantic"}:
            raise
        click.echo(
            "MCP support is not installed. Install it with: pip install 'patchright-cli[mcp]'",
            err=True,
        )
        raise SystemExit(1) from exc

    run_mcp_server(http=options.http, host=options.host, port=options.port)


_VERSION_CHECK_INTERVAL = 24 * 60 * 60
_PYPI_URL = "https://pypi.org/pypi/patchright-cli/json"


def _version_stamp() -> Path:
    return Path.home() / ".patchright-cli" / "version-check.json"


def _version_tuple(version: str) -> tuple[int, ...]:
    """Numeric release segments of a version, ignoring any suffix.

    Returns () for anything unparseable, which compares lower than every real
    version and so can never trigger an upgrade notice.
    """
    parts: list[int] = []
    for segment in version.split("."):
        digits = ""
        for ch in segment:
            if not ch.isdigit():
                break
            digits += ch
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _fetch_latest_version() -> str | None:
    """Latest patchright-cli release on PyPI, or None."""
    import urllib.request

    with urllib.request.urlopen(_PYPI_URL, timeout=1.5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    latest = payload.get("info", {}).get("version")
    return latest if isinstance(latest, str) else None


def _warn_outdated_version() -> None:
    """Warn on stderr when a newer release is on PyPI.

    Runs on `open` only, at most once a day, and the stamp is written before
    the request so an unreachable network cannot make every open pay the
    timeout. Any failure is silent -- a version notice is never worth breaking
    a command over. PATCHRIGHT_CLI_NO_VERSION_CHECK=1 disables it.
    """
    import time

    if os.environ.get("PATCHRIGHT_CLI_NO_VERSION_CHECK"):
        return

    stamp = _version_stamp()
    try:
        cached = json.loads(stamp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cached = {}
    if not isinstance(cached, dict):
        cached = {}

    latest = cached.get("latest")
    # No `or not latest`: the stamp is written before the request, so a failed
    # fetch still throttles. With it, a machine that cannot reach PyPI paid the
    # timeout on every single `open`.
    if time.time() - _stamp_time(cached) >= _VERSION_CHECK_INTERVAL:
        try:
            stamp.parent.mkdir(parents=True, exist_ok=True)
            stamp.write_text(json.dumps({"at": time.time(), "latest": latest}), encoding="utf-8")
        except OSError:
            pass
        try:
            latest = _fetch_latest_version()
        except Exception:
            return
        if not latest:
            return
        try:
            stamp.write_text(json.dumps({"at": time.time(), "latest": latest}), encoding="utf-8")
        except OSError:
            pass

    if _version_tuple(latest) > _version_tuple(__version__):
        click.echo(
            f"note: patchright-cli {latest} is available (you have {__version__}). "
            f"Upgrade with `pip install -U patchright-cli`.",
            err=True,
        )


DEFAULT_SOCKET_TIMEOUT = 120
ANNOTATE_SOCKET_MARGIN = 30


# `kill-all` is the way out of a wedged daemon, so it gives up asking quickly
# and kills the process instead (see _kill_all).
KILL_ALL_SOCKET_TIMEOUT = 20
# After that, a daemon that still answers `list` within the probe timeout is
# given up to the old 120s to finish shutting down before it is killed.
KILL_ALL_GRACE = 120
KILL_ALL_PROBE_TIMEOUT = 5


def _socket_timeout(command: str, options: dict) -> float:
    """How long to wait on the daemon for this command.

    `show --annotate` blocks on a person, so the default would hang up while
    they are still drawing. Everything else keeps the generous-but-finite
    default, which is what catches a wedged daemon.
    """
    if command == "kill-all":
        return KILL_ALL_SOCKET_TIMEOUT
    if command != "show" or not options.get("annotate"):
        return DEFAULT_SOCKET_TIMEOUT
    try:
        wait = float(options.get("wait", 300))
    except (TypeError, ValueError):
        return DEFAULT_SOCKET_TIMEOUT
    return max(DEFAULT_SOCKET_TIMEOUT, wait + ANNOTATE_SOCKET_MARGIN)


def _send_command(
    command: str, args: list, options: dict, port: int = DEFAULT_PORT, timeout: float | None = None
) -> dict:
    """Connect to daemon, send command, receive response."""
    msg = {
        "command": command,
        "args": args,
        "options": options,
        "cwd": os.getcwd(),
    }
    # A daemon that predates tokens writes no token file and accepts the
    # message without one, so a missing file is not an error here.
    token = read_daemon_token(port)
    if token:
        msg["token"] = token
    data = json.dumps(msg).encode("utf-8")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout if timeout is not None else _socket_timeout(command, options))
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


def _port_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def _await_daemon_exit(port: int, deadline: float) -> bool:
    """Wait for a daemon that is shutting down. True once it is gone, False when it is wedged.

    While it still answers `list` it is busy, not stuck, so keep waiting. A
    closed port means it is in its final cleanup. A connection it accepts but
    never answers means it is stuck.
    """
    from patchright_cli import daemon_pid
    from patchright_cli.daemon import TOKEN_DIR

    while time.monotonic() < deadline:
        if daemon_pid.live_daemon_pid(TOKEN_DIR, port) is None and not _port_listening(port):
            return True
        try:
            _send_command("list", [], {"session": "default"}, port, timeout=KILL_ALL_PROBE_TIMEOUT)
        except ConnectionRefusedError:
            pass
        except (ConnectionError, OSError):
            return False
        time.sleep(0.5)
    return False


def _kill_all(port: int, options: dict, force: bool) -> dict:
    """Stop the daemon on `port`: ask it, and kill it when it does not answer or with --force.

    Returns a daemon-style reply, so --json and --raw print it like any other.
    """
    from patchright_cli import daemon_pid
    from patchright_cli.daemon import TOKEN_DIR

    note = ""
    if not force:
        try:
            return _send_command("kill-all", [], options, port)
        except ConnectionRefusedError:
            if daemon_pid.live_daemon_pid(TOKEN_DIR, port) is None:
                return {"success": True, "output": f"No daemon running on port {port} (no-op)"}
        except (ConnectionError, OSError):
            pass  # no reply in time, or the connection dropped
        # Closing many browsers can outlast the reply timeout. Only a daemon
        # that stops answering altogether is killed.
        if _await_daemon_exit(port, time.monotonic() + KILL_ALL_GRACE):
            return {"success": True, "output": f"Daemon on port {port} stopped."}
        note = f"The daemon on port {port} stopped answering; force-stopping it.\n"

    status, message = daemon_pid.force_stop(TOKEN_DIR, port, token_path(port))
    if status == "stopped":
        return {"success": True, "output": note + message}
    if status == "absent" and not _port_listening(port):
        return {"success": True, "output": note + f"No daemon running on port {port} (no-op)"}
    reason = message if status == "failed" else f"port {port} is in use but no running daemon PID is recorded for it"
    return {
        "success": False,
        "output": note
        + f"error: {reason}\n"
        + "help: a daemon from an older patchright-cli records no PID; end the python process whose command line "
        "contains patchright_cli.daemon (Task Manager, or `kill`)",
    }


def _ensure_and_send(command: str, args: list, options: dict, port: int, headless: bool) -> dict:
    """Start the daemon if needed (every command but kill-all does) and send it the command."""
    try:
        ensure_daemon_running(port, headless)
    except RuntimeError as e:
        _print_error(
            f"{e}",
            f"help: check that port {port} is free, or pick another with --port=N",
        )
        sys.exit(1)
    try:
        return _send_command(command, args, options, port)
    except (ConnectionError, OSError) as e:
        _print_error(
            f"cannot reach the daemon on port {port}: {e}",
            f"help: retry, or run `patchright-cli --port={port} kill-all` (it force-stops a daemon that does not "
            "answer) and `open` again",
        )
        sys.exit(1)


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

# One entry per command. `flags` are the command's own options -- exactly the
# ones its daemon handler reads -- mapped to a description that states the
# default. Anything else after the command, beyond GLOBAL_FLAGS, is rejected
# before the daemon is contacted. `min_args` defaults to the number of
# `<required>` positionals in `usage`.
COMMAND_SPECS: dict[str, dict] = {
    # Core
    "open": {
        "usage": "open [url]",
        "summary": "Open browser (starts daemon if needed)",
        "flags": {
            "block-networks": "Refuse navigation to link-local|private addresses (default: allow all)",
            "headed": "Force a headed window even with --headless (default: off)",
        },
        "examples": ["open https://example.com", "--headless open https://example.com", "-s=work open --profile=work"],
    },
    "attach": {
        "usage": "attach --cdp=<url>",
        "summary": "Attach to running Chrome via CDP",
        "flags": {
            "cdp-headers": "Extra headers for the CDP connection (default: none)",
            "cdp-timeout": "CDP connect timeout in ms (default: 30000)",
            "block-networks": "Refuse navigation to link-local|private addresses (default: allow all)",
        },
        "examples": ["attach --cdp=http://localhost:9222", "attach --cdp=http://localhost:9222 --context=host"],
    },
    "goto": {"usage": "goto <url>", "summary": "Navigate to URL", "examples": ["goto https://example.com"]},
    "click": {
        "usage": "click <ref> [button]",
        "summary": "Click element",
        "flags": {"modifiers": "Comma-separated modifier keys held during the click, e.g. Alt,Shift (default: none)"},
        "examples": ["click e5", "click e5 right", "click e5 --modifiers=Control"],
    },
    "dblclick": {
        "usage": "dblclick <ref> [button]",
        "summary": "Double-click element",
        "flags": {"modifiers": "Comma-separated modifier keys, e.g. Alt,Shift (default: none)"},
        "examples": ["dblclick e5", "dblclick e5 --modifiers=Shift"],
    },
    "fill": {
        "usage": "fill <ref> <value>",
        "summary": "Fill text into element",
        "flags": {"submit": "Press Enter after filling (default: off)"},
        "examples": ['fill e3 "hello"', 'fill e3 "query" --submit'],
    },
    "type": {
        "usage": "type <text>",
        "summary": "Type text via keyboard",
        "flags": {"submit": "Press Enter after typing (default: off)"},
        "examples": ['type "hello"', 'type "query" --submit'],
    },
    "hover": {"usage": "hover <ref>", "summary": "Hover over element", "examples": ["hover e4"]},
    "select": {"usage": "select <ref> <value>", "summary": "Select dropdown option", "examples": ["select e9 blue"]},
    "check": {"usage": "check <ref>", "summary": "Check checkbox/radio", "examples": ["check e12"]},
    "uncheck": {"usage": "uncheck <ref>", "summary": "Uncheck checkbox/radio", "examples": ["uncheck e12"]},
    "snapshot": {
        "usage": "snapshot [ref]",
        "summary": "Take accessibility snapshot",
        "flags": {
            "selector": "CSS selector to scope the snapshot to (default: whole page)",
            "filename": "Save under .patchright-cli/<name> (default: timestamped)",
            "depth": "Maximum tree depth (default: unlimited)",
            "interactive": "Only interactive elements; also -i (default: off)",
            "boxes": "Add bounding boxes to each ref (default: off)",
        },
        "examples": ["snapshot", "snapshot --depth=3 -i", "snapshot --selector=main --filename=main.yml"],
    },
    "find": {
        "usage": "find <text>",
        "summary": "Search the snapshot for matching elements",
        "flags": {
            "regex": "Treat <text> as a regular expression (default: off)",
            "all": "Match every role, not just interactive ones (default: off)",
            "limit": "Maximum matches (default: 20)",
        },
        # `find --regex=<pattern>` carries its text in the flag value.
        "args_from_flag_value": "regex",
        "examples": ['find "Sign in"', 'find "^Item \\d+" --regex --limit=50'],
    },
    "eval": {
        "usage": "eval <expr> [ref]",
        "summary": "Evaluate JavaScript (expression, --file or stdin)",
        "flags": {"file": "Read the JavaScript from this file (default: from <expr> or stdin)"},
        "examples": ['eval "document.title"', 'eval "el => el.textContent" e5', "eval --file=script.js"],
        "min_args": 0,
    },
    "text": {"usage": "text <ref|selector>", "summary": "Get text content of element", "examples": ["text e5"]},
    "screenshot": {
        "usage": "screenshot [ref]",
        "summary": "Save screenshot",
        "flags": {
            "full-page": "Capture the full scrollable page (default: viewport only)",
            "hires": "Capture at the device pixel ratio (default: one pixel per CSS pixel)",
            "filename": "Save under .patchright-cli/<name> (default: timestamped)",
        },
        "examples": ["screenshot", "screenshot --full-page --filename=page.png", "screenshot e5"],
    },
    "drag": {"usage": "drag <from> <to>", "summary": "Drag element to target", "examples": ["drag e3 e7"]},
    "drop": {
        "usage": "drop <ref>",
        "summary": "Drop file/data onto element",
        "flags": {
            "path": "File to drop (default: none)",
            "data": "Data to drop as mime=value (default: none)",
        },
        "examples": ["drop e4 --path=photo.png", 'drop e4 --data="text/plain=hello"'],
    },
    "close": {
        "usage": "close",
        "summary": "Close browser session (or the --tab it is addressed to)",
        "examples": ["close", "-s=work close"],
    },
    "detach": {
        "usage": "detach",
        "summary": "Detach attached session (keep external browser running)",
        "examples": ["detach", "-s=work detach"],
    },
    # Navigation
    "go-back": {"usage": "go-back", "summary": "Go back", "examples": ["go-back"]},
    "go-forward": {"usage": "go-forward", "summary": "Go forward", "examples": ["go-forward"]},
    "reload": {"usage": "reload", "summary": "Reload page", "examples": ["reload"]},
    "url": {"usage": "url", "summary": "Print current URL", "examples": ["url", "url --raw"]},
    "title": {"usage": "title", "summary": "Print page title", "examples": ["title", "title --raw"]},
    # Keyboard
    "press": {"usage": "press <key>", "summary": "Press key", "examples": ["press Enter", "press Control+a"]},
    "keydown": {"usage": "keydown <key>", "summary": "Key down", "examples": ["keydown Shift"]},
    "keyup": {"usage": "keyup <key>", "summary": "Key up", "examples": ["keyup Shift"]},
    # Mouse
    "mousemove": {"usage": "mousemove <x> <y>", "summary": "Move mouse", "examples": ["mousemove 100 200"]},
    "mousedown": {
        "usage": "mousedown [button]",
        "summary": "Mouse button down",
        "examples": ["mousedown", "mousedown right"],
    },
    "mouseup": {"usage": "mouseup [button]", "summary": "Mouse button up", "examples": ["mouseup"]},
    "mousewheel": {"usage": "mousewheel <dx> <dy>", "summary": "Scroll mouse wheel", "examples": ["mousewheel 0 300"]},
    # Scroll & Wait
    "scroll": {"usage": "scroll <dx> <dy>", "summary": "Scroll by pixel offset", "examples": ["scroll 0 500"]},
    "scroll-to": {"usage": "scroll-to <ref>", "summary": "Scroll element into view", "examples": ["scroll-to e20"]},
    "wait": {
        "usage": "wait [ms]",
        "summary": "Wait for milliseconds, or for a URL",
        "flags": {"url": "Wait until the URL matches this glob or /regex/ (default: none)"},
        "examples": ["wait 1000", "wait --url=**/dashboard"],
    },
    "wait-for": {
        "usage": "wait-for <ref>",
        "summary": "Wait for element to reach a state",
        "flags": {"state": "visible|hidden|attached|detached (default: visible)"},
        "examples": ["wait-for e5", "wait-for e5 --state=hidden"],
    },
    # Tabs
    "tab-list": {"usage": "tab-list", "summary": "List tabs", "examples": ["tab-list"]},
    "tab-new": {
        "usage": "tab-new [url]",
        "summary": "Open new tab",
        "examples": ["tab-new", "tab-new https://example.com"],
    },
    "tab-close": {"usage": "tab-close [index]", "summary": "Close tab", "examples": ["tab-close", "tab-close 2"]},
    "tab-select": {
        "usage": "tab-select <index>",
        "summary": "Switch to tab (default tab only)",
        "examples": ["tab-select 1"],
    },
    # Storage
    "cookie-list": {
        "usage": "cookie-list",
        "summary": "List cookies",
        "flags": {
            "domain": "Only cookies for this domain (default: all)",
            "path": "Only cookies for this path (default: all)",
        },
        "examples": ["cookie-list", "cookie-list --domain=example.com"],
    },
    "cookie-get": {"usage": "cookie-get <name>", "summary": "Get cookie", "examples": ["cookie-get session_id"]},
    "cookie-set": {
        "usage": "cookie-set <name> <value>",
        "summary": "Set cookie",
        "flags": {
            "domain": "Cookie domain (default: current page URL)",
            "path": "Cookie path (default: /)",
            "expires": "Expiry as a Unix timestamp (default: session cookie)",
            "httpOnly": "Mark HttpOnly (default: off)",
            "secure": "Mark Secure (default: off)",
            "sameSite": "Strict|Lax|None (default: browser default)",
        },
        "examples": ["cookie-set theme dark", "cookie-set sid abc --domain=example.com --httpOnly --secure"],
    },
    "cookie-delete": {"usage": "cookie-delete <name>", "summary": "Delete cookie", "examples": ["cookie-delete sid"]},
    "cookie-clear": {"usage": "cookie-clear", "summary": "Clear all cookies", "examples": ["cookie-clear"]},
    "cookie-import": {
        "usage": "cookie-import <file>",
        "summary": "Import cookies from JSON",
        "examples": ["cookie-import cookies.json"],
    },
    "cookie-export": {
        "usage": "cookie-export [file]",
        "summary": "Export cookies to JSON",
        "examples": ["cookie-export", "cookie-export cookies.json"],
    },
    "localstorage-list": {
        "usage": "localstorage-list",
        "summary": "List localStorage",
        "examples": ["localstorage-list"],
    },
    "localstorage-get": {
        "usage": "localstorage-get <key>",
        "summary": "Get localStorage item",
        "examples": ["localstorage-get token"],
    },
    "localstorage-set": {
        "usage": "localstorage-set <key> <value>",
        "summary": "Set localStorage item",
        "examples": ["localstorage-set theme dark"],
    },
    "localstorage-delete": {
        "usage": "localstorage-delete <key>",
        "summary": "Delete localStorage item",
        "examples": ["localstorage-delete theme"],
    },
    "localstorage-clear": {
        "usage": "localstorage-clear",
        "summary": "Clear localStorage",
        "examples": ["localstorage-clear"],
    },
    # Dialog
    "dialog-accept": {
        "usage": "dialog-accept [text]",
        "summary": "Accept next dialog",
        "examples": ["dialog-accept", 'dialog-accept "my answer"'],
    },
    "dialog-dismiss": {"usage": "dialog-dismiss", "summary": "Dismiss next dialog", "examples": ["dialog-dismiss"]},
    # Permissions
    "grant-permissions": {
        "usage": "grant-permissions <perms>",
        "summary": "Grant comma-separated permissions",
        "flags": {"origin": "Grant only for this origin (default: all origins)"},
        "examples": ["grant-permissions geolocation", "grant-permissions clipboard-read --origin=https://example.com"],
    },
    # Upload / Resize
    "upload": {"usage": "upload <file> [ref]", "summary": "Upload file to input", "examples": ["upload photo.png e7"]},
    "resize": {"usage": "resize <w> <h>", "summary": "Resize viewport", "examples": ["resize 1280 800"]},
    # Emulation
    "set-color-scheme": {
        "usage": "set-color-scheme <dark|light|no-preference>",
        "summary": "Emulate prefers-color-scheme",
        "examples": ["set-color-scheme dark"],
    },
    "clear-color-scheme": {
        "usage": "clear-color-scheme",
        "summary": "Stop emulating prefers-color-scheme",
        "examples": ["clear-color-scheme"],
    },
    "set-reduced-motion": {
        "usage": "set-reduced-motion <reduce|no-preference>",
        "summary": "Emulate prefers-reduced-motion",
        "examples": ["set-reduced-motion reduce"],
    },
    "clear-reduced-motion": {
        "usage": "clear-reduced-motion",
        "summary": "Stop emulating prefers-reduced-motion",
        "examples": ["clear-reduced-motion"],
    },
    "set-forced-colors": {
        "usage": "set-forced-colors <active|none>",
        "summary": "Emulate forced-colors",
        "examples": ["set-forced-colors active"],
    },
    "clear-forced-colors": {
        "usage": "clear-forced-colors",
        "summary": "Stop emulating forced-colors",
        "examples": ["clear-forced-colors"],
    },
    "set-contrast": {
        "usage": "set-contrast <more|no-preference>",
        "summary": "Emulate prefers-contrast",
        "examples": ["set-contrast more"],
    },
    "clear-contrast": {
        "usage": "clear-contrast",
        "summary": "Stop emulating prefers-contrast",
        "examples": ["clear-contrast"],
    },
    "set-media": {
        "usage": "set-media <screen|print>",
        "summary": "Emulate CSS media type",
        "examples": ["set-media print"],
    },
    "clear-media": {
        "usage": "clear-media",
        "summary": "Stop emulating the CSS media type",
        "examples": ["clear-media"],
    },
    # State
    "state-save": {
        "usage": "state-save [file]",
        "summary": "Save cookies+storage to JSON",
        "examples": ["state-save", "state-save auth.json"],
    },
    "state-load": {"usage": "state-load <file>", "summary": "Load saved state", "examples": ["state-load auth.json"]},
    # Session storage
    "sessionstorage-list": {
        "usage": "sessionstorage-list",
        "summary": "List sessionStorage",
        "examples": ["sessionstorage-list"],
    },
    "sessionstorage-get": {
        "usage": "sessionstorage-get <key>",
        "summary": "Get sessionStorage item",
        "examples": ["sessionstorage-get step"],
    },
    "sessionstorage-set": {
        "usage": "sessionstorage-set <key> <value>",
        "summary": "Set sessionStorage item",
        "examples": ["sessionstorage-set step 2"],
    },
    "sessionstorage-delete": {
        "usage": "sessionstorage-delete <key>",
        "summary": "Delete sessionStorage item",
        "examples": ["sessionstorage-delete step"],
    },
    "sessionstorage-clear": {
        "usage": "sessionstorage-clear",
        "summary": "Clear sessionStorage",
        "examples": ["sessionstorage-clear"],
    },
    # Route
    "route": {
        "usage": "route <pattern>",
        "summary": "Mock requests matching a URL pattern",
        "flags": {
            "status": "Response status (default: 200)",
            "body": "Response body (default: empty)",
            "content-type": "Response Content-Type (default: text/plain)",
            "header": "Extra response header as K:V (default: none)",
            "remove-header": "Comma-separated request headers to strip (default: none)",
        },
        "examples": [
            'route "**/api/*" --status=500',
            'route "**/data.json" --body=\'{"ok":true}\' --content-type=application/json',
        ],
    },
    "route-list": {"usage": "route-list", "summary": "List active routes", "examples": ["route-list"]},
    "unroute": {
        "usage": "unroute [pattern]",
        "summary": "Remove route(s)",
        "examples": ["unroute", 'unroute "**/api/*"'],
    },
    "network-state-set": {
        "usage": "network-state-set <online|offline>",
        "summary": "Set online/offline",
        "examples": ["network-state-set offline"],
    },
    # Run code
    "run-code": {
        "usage": "run-code <code>",
        "summary": "Run raw JS in page context (code, --file or stdin)",
        "flags": {"file": "Read the code from this file (default: from <code> or stdin)"},
        "examples": ['run-code "window.scrollTo(0, 0)"', "run-code --file=setup.js"],
        "min_args": 0,
    },
    # Tracing
    "tracing-start": {"usage": "tracing-start", "summary": "Start Playwright tracing", "examples": ["tracing-start"]},
    "tracing-stop": {"usage": "tracing-stop", "summary": "Stop tracing and save", "examples": ["tracing-stop"]},
    # Video
    "video-start": {"usage": "video-start", "summary": "Start video recording", "examples": ["video-start"]},
    "video-stop": {
        "usage": "video-stop",
        "summary": "Stop recording and save",
        "flags": {"filename": "Save under .patchright-cli/<name> (default: timestamped)"},
        "examples": ["video-stop", "video-stop --filename=demo.webm"],
    },
    "video-chapter": {
        "usage": "video-chapter <title>",
        "summary": "Add chapter marker to video",
        "examples": ['video-chapter "Checkout"'],
    },
    "video-show-actions": {
        "usage": "video-show-actions",
        "summary": "Annotate actions in the recording",
        "flags": {
            "duration": "How long each callout shows, in ms (default: 600)",
            "position": "Callout corner, e.g. top-left|bottom-right (default: top-right)",
        },
        "examples": ["video-show-actions", "video-show-actions --duration=1000 --position=bottom-left"],
    },
    "video-hide-actions": {
        "usage": "video-hide-actions",
        "summary": "Stop annotating actions",
        "examples": ["video-hide-actions"],
    },
    # PDF
    "pdf": {
        "usage": "pdf",
        "summary": "Save page as PDF",
        "flags": {"filename": "Save under .patchright-cli/<name> (default: timestamped)"},
        "examples": ["pdf", "pdf --filename=page.pdf"],
    },
    # DevTools
    "console": {
        "usage": "console [level]",
        "summary": "Show console messages",
        "flags": {"clear": "Clear the buffer after showing it (default: off)"},
        "examples": ["console", "console error", "console --clear"],
    },
    "network": {
        "usage": "network",
        "summary": "Show network requests",
        "flags": {
            "static": "Include static assets (default: off)",
            "clear": "Clear the buffer after showing it (default: off)",
        },
        "examples": ["network", "network --static"],
    },
    "requests": {
        "usage": "requests",
        "summary": "Alias for `network`",
        "flags": {
            "static": "Include static assets (default: off)",
            "clear": "Clear the buffer after showing it (default: off)",
        },
        "examples": ["requests", "requests --clear"],
    },
    "request": {
        "usage": "request <id>",
        "summary": "Show full detail for a network request",
        "flags": {"body": "Include the response body (default: off)"},
        "examples": ["request 12", "request 12 --body"],
    },
    "generate-locator": {
        "usage": "generate-locator <ref>",
        "summary": "Emit Playwright locator expression for a ref",
        "examples": ["generate-locator e5"],
    },
    "highlight": {
        "usage": "highlight <ref>",
        "summary": "Show overlay on element",
        "flags": {
            "style": "CSS for the overlay (default: 2px pink outline)",
            "hide": "Remove the overlay instead; without a ref, clear every overlay (default: off)",
        },
        # `highlight --hide` with no ref clears every overlay on the page.
        "optional_args_with": "hide",
        "examples": ["highlight e5", "highlight e5 --hide", "highlight --hide"],
    },
    # Session
    "list": {"usage": "list", "summary": "List sessions", "examples": ["list"]},
    "close-all": {"usage": "close-all", "summary": "Close all sessions", "examples": ["close-all"]},
    "kill-all": {
        "usage": "kill-all",
        "summary": "Kill all sessions and stop the daemon",
        "flags": {
            "force": "Kill the daemon process and its browsers without asking it "
            "(default: ask, force only if it does not answer)",
        },
        "examples": ["kill-all", "--port=9400 kill-all", "kill-all --force"],
    },
    "delete-data": {
        "usage": "delete-data",
        "summary": "Delete this session's profile",
        "examples": ["-s=work delete-data"],
    },
    # Profiles
    "profile-list": {
        "usage": "profile-list",
        "summary": "List profiles, their size and which session uses them",
        "examples": ["profile-list"],
    },
    "profile-delete": {
        "usage": "profile-delete <name>",
        "summary": "Delete a named profile",
        "examples": ["profile-delete work"],
    },
    # Dashboard
    "show": {
        "usage": "show",
        "summary": "Open session dashboard",
        "flags": {
            "annotate": "Wait for a person to annotate the page (default: off)",
            "wait": "Seconds to wait for the annotation (default: 300)",
            "no-open": "Do not open the dashboard in a browser (default: open it)",
        },
        "examples": ["show", "show --annotate --wait=120", "show --show-port=9400 --no-open"],
    },
    # Codegen
    "codegen": {
        "usage": "codegen [file]",
        "summary": "Start recording interactions",
        "examples": ["codegen", "codegen flow.sh"],
    },
    "codegen-stop": {
        "usage": "codegen-stop [file]",
        "summary": "Stop recording and save script",
        "examples": ["codegen-stop", "codegen-stop flow.sh"],
    },
    # Setup -- install and mcp parse their own flags, so they are not validated here.
    "install": {
        "usage": "install --skills | --hooks [--uninstall]",
        "summary": "Install agent skill files, or session-start hooks",
        "examples": ["install --skills", "install --hooks", "install --hooks --uninstall"],
        "validate": False,
    },
    "mcp": {
        "usage": "mcp [--http] [--host=HOST] [--port=N]",
        "summary": "Start MCP server (stdio by default)",
        "examples": ["mcp", "mcp --http --port=8000"],
        "validate": False,
    },
    "daemon-info": {
        "usage": "daemon-info --json",
        "summary": "Show daemon client protocol and token path without starting it",
        "examples": ["daemon-info --json", "daemon-info --json --port=9400"],
    },
}

# Flags every command accepts. Most are parsed in main() wherever they appear;
# the timeouts and --web-only travel with the command to the daemon, which
# reads them for every request (the MCP server sends --web-only on every call).
GLOBAL_FLAGS = frozenset(
    {
        "headless",
        "persistent",
        "profile",
        "proxy",
        "tab",
        "port",
        "config",
        "device",
        "mobile",
        "viewport-size",
        "locale",
        "timezone",
        "geolocation",
        "user-agent",
        "grant-permissions",
        "cdp",
        "context",
        "show-port",
        "raw",
        "json",
        "toon",
        "timeout",
        "timeout-action",
        "timeout-navigation",
        "web-only",
        "help",
        "version",
    }
)

# Spellings that are not flags of their own but obviously mean one that is.
# Only suggested when the target is valid for the command at hand.
RENAMED_FLAGS = {
    "http-only": "httpOnly",
    "httponly": "httpOnly",
    "same-site": "sameSite",
    "samesite": "sameSite",
    "fullpage": "full-page",
    "fullPage": "full-page",
    "contentType": "content-type",
    "noOpen": "no-open",
    "blockNetworks": "block-networks",
    "webOnly": "web-only",
    "viewport": "viewport-size",
    "userAgent": "user-agent",
    "grantPermissions": "grant-permissions",
    "showPort": "show-port",
    "max-depth": "depth",
    "output": "filename",
    "out": "filename",
    "css": "selector",
    "hidden": "state=hidden",
}


def _min_args(spec: dict) -> int:
    if "min_args" in spec:
        return spec["min_args"]
    return sum(1 for token in spec["usage"].split()[1:] if token.startswith("<"))


def _manual_line(name: str) -> str:
    spec = COMMAND_SPECS[name]
    flags = " ".join(f"[--{f}]" for f in spec.get("flags", {}))
    usage = f"{spec['usage']} {flags}".strip()
    return f"{usage:<28} {spec['summary']}" if len(usage) < 28 else f"{usage}  {spec['summary']}"


# One-line manual entries, kept for the full --help and for callers that list commands.
COMMANDS_HELP = {name: _manual_line(name) for name in COMMAND_SPECS}

ALL_COMMANDS = list(COMMANDS_HELP.keys())


def _command_help(name: str) -> str:
    """Concise reference for one command: usage, its flags with defaults, examples."""
    spec = COMMAND_SPECS[name]
    flags = spec.get("flags", {})
    lines = [f"usage: patchright-cli {spec['usage']}{' [flags]' if flags else ''}", f"  {spec['summary']}"]
    if flags:
        width = max(len(f) for f in flags) + 4
        lines.append("flags:")
        lines.extend(f"  {'--' + f:<{width}}{desc}" for f, desc in flags.items())
    lines.append("examples:")
    lines.extend(f"  patchright-cli {example}" for example in spec.get("examples", []))
    lines.append("global flags (any command): -s, --tab, --port, --json, --raw, ... see `patchright-cli --help`")
    return "\n".join(lines)


class UsageError(Exception):
    """A bad invocation, caught in main(): printed to stdout, exit 2."""

    def __init__(self, message: str, *help_lines: str, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.help_lines = help_lines
        self.detail = detail


def _unknown_flag_error(command: str, flag: str) -> UsageError:
    spec = COMMAND_SPECS[command]
    flags = spec.get("flags", {})
    shown = flag if flag.startswith("-") else f"--{flag}"
    name = flag.lstrip("-")
    target = RENAMED_FLAGS.get(name)
    if target is None:
        # kebab-case vs camelCase vs snake_case of a real flag
        squash = name.replace("-", "").replace("_", "").lower()
        target = next((f for f in [*flags, *GLOBAL_FLAGS] if f.replace("-", "").lower() == squash), None)
    if target is not None and target.split("=")[0] in {*flags, *GLOBAL_FLAGS} and target != name:
        hint = f"help: `{command}` takes --{target}, not --{name}"
    else:
        valid = ", ".join(f"--{f}" for f in flags) or "none"
        hint = f"help: valid flags for `{command}`: {valid} (--help always allowed)"
    return UsageError(f"unknown flag {shown} for `{command}`", hint, detail=_command_help(command))


def _validate_flags(command: str, cmd_opts: dict, interactive_flag: str | None = None) -> None:
    """Reject any flag `command` does not read, before anything else runs."""
    spec = COMMAND_SPECS[command]
    if not spec.get("validate", True):
        return
    flags = spec.get("flags", {})
    for key in cmd_opts:
        if key not in flags and key not in GLOBAL_FLAGS:
            raise _unknown_flag_error(command, key)
    if interactive_flag and "interactive" not in flags:
        raise _unknown_flag_error(command, interactive_flag)


def _validate_positionals(command: str, args: list, flags: dict) -> None:
    spec = COMMAND_SPECS[command]
    if spec.get("optional_args_with") in flags or isinstance(flags.get(spec.get("args_from_flag_value")), str):
        need = 0
    else:
        need = _min_args(spec)
    if len(args) < need:
        required = " ".join(t for t in spec["usage"].split()[1:] if t.startswith("<"))
        raise UsageError(
            f"`{command}` requires {required}",
            f"help: patchright-cli {spec['usage']}",
            f"help: run `patchright-cli {command} --help` for flags and examples",
        )


def _suggest_command(command: str) -> str | None:
    import difflib

    close = difflib.get_close_matches(command, ALL_COMMANDS, n=1, cutoff=0.75)
    return close[0] if close else None


# Manual order: the full --help and the generated command block in SKILL.md.
COMMAND_CATEGORIES = [
    (
        "Core",
        [
            "open",
            "attach",
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
            "find",
            "eval",
            "text",
            "screenshot",
            "drag",
            "drop",
            "close",
            "detach",
        ],
    ),
    ("Navigation", ["go-back", "go-forward", "reload", "url", "title"]),
    ("Keyboard", ["press", "keydown", "keyup"]),
    ("Mouse", ["mousemove", "mousedown", "mouseup", "mousewheel"]),
    ("Scroll & Wait", ["scroll", "scroll-to", "wait", "wait-for"]),
    ("Tabs", ["tab-list", "tab-new", "tab-close", "tab-select"]),
    ("Dialog", ["dialog-accept", "dialog-dismiss"]),
    ("Upload/Resize", ["upload", "resize"]),
    (
        "Emulation",
        [
            "set-color-scheme",
            "clear-color-scheme",
            "set-reduced-motion",
            "clear-reduced-motion",
            "set-forced-colors",
            "clear-forced-colors",
            "set-contrast",
            "clear-contrast",
            "set-media",
            "clear-media",
        ],
    ),
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
    ("Video", ["video-start", "video-stop", "video-chapter", "video-show-actions", "video-hide-actions"]),
    ("PDF", ["pdf"]),
    ("DevTools", ["console", "network", "requests", "request", "generate-locator", "highlight"]),
    ("Session", ["list", "close-all", "kill-all", "delete-data"]),
    ("Profiles", ["profile-list", "profile-delete"]),
    ("Dashboard", ["show"]),
    ("Codegen", ["codegen", "codegen-stop"]),
    ("Setup", ["install", "mcp", "daemon-info"]),
]


def _print_help():
    click.echo("patchright-cli — Undetected browser automation CLI\n")
    click.echo("Usage: patchright-cli [OPTIONS] <command> [args...]\n")
    click.echo("Options:")
    click.echo("  --headless          Run headless (default: headed)")
    click.echo("  --persistent        No effect; kept for compatibility (profiles always persist)")
    click.echo("  --profile=<name>    Named profile (default: the session name); a value")
    click.echo(r"                      with a / or \ is a directory instead")
    click.echo("  --proxy=<url>       Proxy server (e.g. http://host:port, socks5://host:port)")
    click.echo("  -s=<name>           Named session (default: 'default')")
    click.echo("  --tab=<name>        Named tab inside the session: shared browser,")
    click.echo("                      cookies and fingerprint; own page, refs and history")
    click.echo("  --port=<n>          Daemon port (default: 9321)")
    click.echo("  --config=<path>     Load config from JSON file")
    click.echo("  --device=<name>     Emulate a device (e.g. 'iPhone 15')")
    click.echo("  --mobile            Emulate a generic mobile device (Pixel 7)")
    click.echo("  --viewport-size=WxH Set viewport size")
    click.echo("  --locale=<code>     Locale (e.g. en-US)")
    click.echo("  --timezone=<id>     Timezone ID")
    click.echo("  --geolocation=lat,lon Geolocation override")
    click.echo("  --user-agent=<ua>   Custom user agent")
    click.echo("  --grant-permissions=P Comma-separated permissions to grant")
    click.echo("  --cdp=<url>         Attach to Chrome via CDP endpoint")
    click.echo("  --show-port=N       Dashboard port (default: 9322)")
    click.echo("  --raw               Raw output (strip page/snapshot decorations)")
    click.echo("  --json              Wrap response as JSON ({success, output, ...})")
    click.echo("  --toon              TOON output for list/url/title results (default: text)")
    click.echo("  -V, --version       Show version")
    click.echo("  --help              Show this help; `<command> --help` shows one command")
    click.echo("\nThese go after the command, not before:")
    click.echo("  --timeout=<seconds>   Daemon idle timeout before it exits (default: 1800)")
    click.echo("  --timeout-action=ms   Default action timeout")
    click.echo("  --timeout-navigation=ms Default navigation timeout")
    click.echo("  --web-only            Keep this request's navigations on the web\n")
    click.echo("Commands:")
    for cat_name, cmds in COMMAND_CATEGORIES:
        click.echo(f"\n  {cat_name}:")
        for c in cmds:
            click.echo(f"    {COMMANDS_HELP[c]}")
    click.echo()


# ---------------------------------------------------------------------------
# Home view: what a bare `patchright-cli` prints
# ---------------------------------------------------------------------------

# A bare run is the SessionStart hook `install --hooks` registers, so this is
# what an agent reads at the top of every session: short, live, never slow.
_HOME_DESCRIPTION = "anti-detect Chrome automation CLI (playwright-cli style commands)"
_HOME_CONNECT_TIMEOUT = 0.3
_HOME_REPLY_TIMEOUT = 1.5
_HOME_MAX_SESSIONS = 5
# The core loop, so an agent can start without reading the full --help manual.
_HOME_COMMAND_GROUPS = [
    ("session", ["open", "goto", "close", "kill-all"]),
    ("act on snapshot refs (e12)", ["snapshot", "click", "fill", "select", "check", "press"]),
    ("read", ["eval", "screenshot", "tab-list"]),
]


def _display_bin(argv0: str | None = None) -> str:
    """How this CLI was invoked, home collapsed to `~`."""
    import shutil

    argv0 = sys.argv[0] if argv0 is None else argv0
    path = Path(argv0) if argv0 else None
    if path is None or path.suffix == ".py" or path.name == "-c":
        # `python -m patchright_cli` puts __main__.py in argv[0].
        return "python -m patchright_cli"
    if path.parent == Path(""):
        found = shutil.which(argv0)
        path = Path(found) if found else path
    try:
        path = path.resolve()
        return "~" + os.sep + str(path.relative_to(Path.home()))
    except (OSError, ValueError):
        return str(path)


_TAB_COUNT_LINE = re.compile(r"^\s+- (?P<name>.+?)(?: \*)?: (?P<tabs>\d+) tab\(s\)$")


def _query_running_daemon(port: int, session: str) -> list[dict] | None:
    """Sessions of an already-running daemon, or None when there is none to ask.

    Never starts anything: no token file means no daemon of ours, and a closed
    port fails the short connect. Any failure reads as "not running".
    """
    token = read_daemon_token(port)
    if not token:
        return None
    msg = {"command": "list", "args": [], "options": {"session": session}, "cwd": os.getcwd(), "token": token}
    data = json.dumps(msg).encode("utf-8")
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=_HOME_CONNECT_TIMEOUT) as sock:
            sock.settimeout(_HOME_REPLY_TIMEOUT)
            sock.sendall(struct.pack("!I", len(data)) + data)
            length = struct.unpack("!I", _recv_exact(sock, 4))[0]
            response = json.loads(_recv_exact(sock, length).decode("utf-8"))
    except (OSError, ValueError, struct.error):
        return None
    if not isinstance(response, dict) or not response.get("success") or not isinstance(response.get("data"), list):
        return None
    # `data` has no tab count; the text output has one per session.
    tabs = {}
    for line in str(response.get("output", "")).splitlines():
        m = _TAB_COUNT_LINE.match(line)
        if m:
            tabs[m["name"]] = int(m["tabs"])
    sessions = []
    for row in response["data"]:
        if isinstance(row, dict) and "name" in row:
            sessions.append({**row, "tabs": tabs.get(row["name"], 0)})
    return sessions


def _home_state(port: int, session: str) -> dict:
    try:
        sessions = _query_running_daemon(port, session)
    except Exception:
        sessions = None
    return {
        "bin": _display_bin(),
        "version": __version__,
        "daemon": {"port": port, "running": sessions is not None},
        "sessions": sessions or [],
    }


def _home_hints(state: dict) -> list[str]:
    port = state["daemon"]["port"]
    cli = "patchright-cli" if port == DEFAULT_PORT else f"patchright-cli --port={port}"
    if not state["daemon"]["running"]:
        hints = [f"help: {cli} open <url>  (starts the daemon and a browser)"]
    elif not state["sessions"]:
        hints = [f"help: {cli} open <url>  (the daemon is up, no browser yet)"]
    else:
        hints = [
            f"help: {cli} snapshot  (element refs for the current page; -s=<name> for another session)",
            f"help: {cli} list  (sessions and their tabs)",
        ]
    hints.append("help: patchright-cli --help  (all commands); patchright-cli <command> --help  (one command)")
    return hints


def _home_command_lines() -> list[str]:
    return [
        f"{label}: "
        + " | ".join(COMMAND_SPECS[name]["usage"] for name in names)
        + ("  (kill-all stops the daemon)" if "kill-all" in names else "")
        for label, names in _HOME_COMMAND_GROUPS
    ]


def _home_lines(state: dict) -> list[str]:
    """The no-arg view: identity, live daemon state, next steps. Stays within ~15 lines."""
    from patchright_cli import toon

    port = state["daemon"]["port"]
    lines = [f"{state['bin']} v{state['version']} -- {_HOME_DESCRIPTION}"]
    if not state["daemon"]["running"]:
        lines.append(f"daemon: not running on port {port} (0 sessions)")
    else:
        sessions = state["sessions"]
        lines.append(f"daemon: running on port {port} ({len(sessions)} session{'s' if len(sessions) != 1 else ''})")
        if sessions:
            shown = sessions[:_HOME_MAX_SESSIONS]
            lines.extend(toon.encode({"sessions": shown}).split("\n"))
            if len(sessions) > len(shown):
                lines.append(f"  ... {len(sessions) - len(shown)} more")
    lines.extend(_home_command_lines())
    lines.extend(_home_hints(state))
    return lines


def _render_home(state: dict, as_json: bool, as_toon: bool) -> str:
    if as_json:
        return json.dumps(state)
    if as_toon:
        from patchright_cli import toon

        return "\n".join([toon.encode(state), *_home_hints(state)])
    return "\n".join(_home_lines(state))


def _print_home(port: int, session: str, *, as_json: bool = False, as_toon: bool = False) -> None:
    """Print the no-arg view. Must never raise: any failure degrades to the daemon-down view."""
    try:
        text = _render_home(_home_state(port, session), as_json, as_toon)
    except Exception:
        down = {"bin": "patchright-cli", "version": __version__, "daemon": {"port": port, "running": False}}
        text = _render_home({**down, "sessions": []}, as_json, as_toon)
    click.echo(text)


# ---------------------------------------------------------------------------
# --toon: render a reply's structured `data` as TOON
# ---------------------------------------------------------------------------

# The key a list reply's `data` sits under; anything unnamed is `items`.
_TOON_NOUNS = {
    "list": "sessions",
    "tab-list": "tabs",
    "cookie-list": "cookies",
    "localstorage-list": "entries",
    "sessionstorage-list": "entries",
    "route-list": "routes",
    "console": "messages",
    "network": "requests",
    "requests": "requests",
    "profile-list": "profiles",
    "find": "matches",
}

# What to do when a list reply was cut short (`total` > shown).
_MORE_HINTS = {
    "find": "narrow the query, or raise --limit=N",
    "console": "only the newest 50 are kept in view; filter by level, e.g. `console error`",
    "network": "only the newest 50 are shown; `network --clear` then reproduce to see a fresh window",
    "requests": "only the newest 50 are shown; `requests --clear` then reproduce to see a fresh window",
}


def _toon_lines(command: str, response: dict) -> list[str] | None:
    """TOON rendering of a reply's `data`, or None to print it as usual.

    None when the reply carries no `data`, or when TOON cannot encode it (a
    page title with an unpaired surrogate): the text output is still correct.
    """
    from patchright_cli import toon

    data = response.get("data")
    if data is None:
        return None
    doc = dict(data) if isinstance(data, dict) else {_TOON_NOUNS.get(command, "items"): data}
    total = response.get("total")
    if isinstance(total, int):
        doc["total"] = total
    try:
        encoded = toon.encode(doc)
    except (TypeError, ValueError):
        return None
    # split("\n"), not splitlines(): TOON leaves U+2028, U+0085 and the like
    # unescaped inside values, and splitlines() would break a value at them.
    lines = encoded.split("\n")
    if isinstance(total, int) and isinstance(data, list) and total > len(data):
        hint = _MORE_HINTS.get(command, "narrow the request to see the rest")
        lines.append(f"help: showing {len(data)} of {total}; {hint}")
    return lines


def _parse_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        port = 0
    if not 1 <= port <= 65535:
        raise UsageError(f"invalid --port value `{value}`", "help: --port takes an integer 1-65535, e.g. --port=9321")
    return port


def _parse_viewport(value: str) -> dict:
    vw, sep, vh = value.partition("x")
    if not (sep and vw.isdigit() and vh.isdigit()):
        raise UsageError(
            f"invalid --viewport-size value `{value}`", "help: use WIDTHxHEIGHT, e.g. --viewport-size=1280x800"
        )
    return {"width": vw, "height": vh}


def _parse_geolocation(value: str) -> dict:
    lat, sep, lon = value.partition(",")
    try:
        if not sep:
            raise ValueError
        float(lat), float(lon)
    except ValueError:
        raise UsageError(
            f"invalid --geolocation value `{value}`", "help: use LAT,LON, e.g. --geolocation=37.77,-122.42"
        ) from None
    return {"lat": lat, "lon": lon}


def _print_error(message: str, *help_lines: str) -> None:
    """CLI-originated errors go to stdout, where the agent reads its output."""
    click.echo(f"error: {message}")
    for line in help_lines:
        click.echo(line)


def main():
    """Entry point for the CLI."""
    try:
        _main()
    except UsageError as e:
        _print_error(e.message, *e.help_lines)
        if e.detail:
            click.echo(e.detail)
        sys.exit(2)
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as e:
        if os.environ.get("PATCHRIGHT_CLI_DEBUG"):
            import traceback

            traceback.print_exc()
        _print_error(str(e) or type(e).__name__, "help: set PATCHRIGHT_CLI_DEBUG=1 for a traceback on stderr")
        sys.exit(1)


def _main():
    _soften_output_encoding(sys.stdout, sys.stderr)
    argv = sys.argv[1:]

    # MCP is a self-contained subcommand with its own transport options.
    # Dispatch it before global option parsing so `patchright-cli mcp --help`
    # shows MCP help rather than the top-level CLI help.
    if argv and argv[0] == "mcp":
        _handle_mcp(argv[1:])
        return

    # Parse global options manually (before the command)
    headless = False
    persistent = False
    profile = None
    proxy = None
    config_path = None
    session_name = os.environ.get("PATCHRIGHT_CLI_SESSION", "default")

    tab_name = os.environ.get("PATCHRIGHT_CLI_TAB", "default")
    port = DEFAULT_PORT
    extra_opts: dict = {}
    raw = False
    as_json = False
    toon = False
    want_help = False
    interactive_flag = None  # the -i/--interactive spelling, for the error

    # Extract options
    remaining = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            # End of options: the rest is positional, even text that looks like a flag.
            remaining.extend(argv[i:])
            break
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
        elif arg.startswith("--tab="):
            tab_name = arg.split("=", 1)[1]
        elif arg == "--tab" and i + 1 < len(argv):
            i += 1
            tab_name = argv[i]
        elif arg.startswith("-s="):
            session_name = arg.split("=", 1)[1]
        elif arg == "-s" and i + 1 < len(argv):
            i += 1
            session_name = argv[i]
        elif arg.startswith("--port="):
            port = _parse_port(arg.split("=", 1)[1])
        elif arg == "--port" and i + 1 < len(argv):
            i += 1
            port = _parse_port(argv[i])
        elif arg.startswith("--config="):
            config_path = arg.split("=", 1)[1]
        elif arg == "--config" and i + 1 < len(argv):
            i += 1
            config_path = argv[i]
        elif arg == "--mobile":
            extra_opts["mobile"] = True
        elif arg.startswith("--device="):
            extra_opts["device"] = arg.split("=", 1)[1]
        elif arg == "--device" and i + 1 < len(argv):
            i += 1
            extra_opts["device"] = argv[i]
        elif arg.startswith("--viewport-size="):
            extra_opts["viewport"] = _parse_viewport(arg.split("=", 1)[1])
        elif arg == "--viewport-size" and i + 1 < len(argv):
            i += 1
            extra_opts["viewport"] = _parse_viewport(argv[i])
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
            extra_opts["geolocation"] = _parse_geolocation(arg.split("=", 1)[1])
        elif arg == "--geolocation" and i + 1 < len(argv):
            i += 1
            extra_opts["geolocation"] = _parse_geolocation(argv[i])
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
        elif arg.startswith("--cdp="):
            extra_opts["cdp"] = arg.split("=", 1)[1]
        elif arg == "--cdp" and i + 1 < len(argv):
            i += 1
            extra_opts["cdp"] = argv[i]
        elif arg.startswith("--context="):
            extra_opts["context"] = arg.split("=", 1)[1]
        elif arg == "--context" and i + 1 < len(argv):
            i += 1
            extra_opts["context"] = argv[i]
        elif arg.startswith("--show-port="):
            extra_opts["show-port"] = arg.split("=", 1)[1]
        elif arg == "--show-port" and i + 1 < len(argv):
            i += 1
            extra_opts["show-port"] = argv[i]
        elif arg == "--raw":
            raw = True
        elif arg == "--json":
            as_json = True
        elif arg == "--toon":
            toon = True
        elif arg in ("-i", "--interactive"):
            extra_opts["interactive"] = True
            interactive_flag = arg
        elif arg in ("--version", "-v", "-V"):
            click.echo(__version__)
            sys.exit(0)
        elif arg in ("--help", "-h"):
            want_help = True
        else:
            remaining.append(arg)
        i += 1

    # The command is the first bare word; a flag in front of it belongs to it
    # (`--dept=3 snapshot` is snapshot's unknown flag, not a command `--dept=3`).
    command_at = next(
        (n for n, a in enumerate(remaining) if a == "--" or not a.startswith("-")),
        len(remaining),
    )
    if as_json and toon and not want_help:
        raise UsageError("--json and --toon are mutually exclusive", "help: pick one output format")

    if command_at == len(remaining) or remaining[command_at] == "--":
        if remaining[:command_at]:
            raise UsageError(
                f"unknown flag {remaining[0].split('=', 1)[0]}", "help: run `patchright-cli --help` for all commands"
            )
        if want_help:
            _print_help()
        else:
            _print_home(port, session_name, as_json=as_json, as_toon=toon)
        sys.exit(0)

    command = remaining[command_at]
    args = remaining[:command_at] + remaining[command_at + 1 :]

    if command not in ALL_COMMANDS:
        help_lines = []
        suggestion = _suggest_command(command)
        if suggestion:
            help_lines.append(f"help: did you mean `{suggestion}`?")
        help_lines.append("help: run `patchright-cli --help` for all commands")
        raise UsageError(f"unknown command `{command}`", *help_lines)

    if want_help:
        click.echo(_command_help(command))
        sys.exit(0)

    if command == "install":
        _handle_install(args + [k if v is True else f"{k}={v}" for k, v in extra_opts.items()])
        sys.exit(0)

    if command == "mcp":
        _handle_mcp(args)
        sys.exit(0)

    # Separate --key=value and --flag from positional args
    positional_args = []
    cmd_opts: dict = {}
    end_of_options = False
    for a in args:
        if end_of_options:
            positional_args.append(a)
        elif a == "--":
            end_of_options = True
        elif a.startswith("--") and "=" in a:
            k, v = a[2:].split("=", 1)
            cmd_opts[k] = v
        elif a.startswith("--"):
            cmd_opts[a[2:]] = True
        else:
            positional_args.append(a)
    args = positional_args

    # Before anything touches the daemon, the network or the config file.
    _validate_flags(command, cmd_opts, interactive_flag)

    if command == "daemon-info":
        if not as_json or args or extra_opts:
            raise UsageError(
                "`daemon-info` takes only --json (and --port)",
                "help: patchright-cli daemon-info --json [--port=N]",
            )
        click.echo(
            json.dumps(
                {
                    "version": __version__,
                    "protocol": PROTOCOL_VERSION,
                    "port": port,
                    "token_path": str(token_path(port).resolve()),
                }
            )
        )
        return

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
                _print_error(f"file not found: {file_opt}", "help: pass an existing path to --file")
                sys.exit(1)
        elif args and args[0] == "-" and not end_of_options:
            # Read JS from stdin; an empty one is no input
            piped = sys.stdin.read()
            args = [piped] if piped.strip() else []
        elif not args and not sys.stdin.isatty():
            # Piped input with no positional arg; an empty pipe (or /dev/null)
            # is no input, not an empty script to run.
            piped = sys.stdin.read()
            args = [piped] if piped.strip() else []
        if not args:
            raise UsageError(
                f"`{command}` requires a JS expression, --file=<path>, or piped stdin",
                f"help: patchright-cli {COMMAND_SPECS[command]['examples'][0]}",
            )

    _validate_positionals(command, args, extra_opts)

    _warn_stale_skills()

    # Only on `open`: one browser launch already costs seconds, and this keeps
    # the outbound request off every other command.
    if command == "open":
        _warn_outdated_version()

    # Build options dict (config file values are overridden by explicit CLI flags)
    try:
        config = _load_config(config_path)
    except UsageError:
        if command != "kill-all":
            raise
        config = {}  # a broken project config must not block the way out
    if command == "attach" and (extra_opts.get("context") or config.get("context")) == "host":
        # The Host Context's emulation is fixed, so the daemon rejects these.
        # Explicit flags should still fail loudly, but project-wide config
        # defaults must not make every Host Context attach unusable.
        config = {k: v for k, v in config.items() if k not in _CONTEXT_EMULATION_KEYS}
    options = {"session": session_name, "tab": tab_name, **_merge_config_with_options(config, extra_opts)}
    # `session` and `tab` are parsed into their own variables rather than into
    # extra_opts, so the spread above let a stale config value win over an
    # explicit flag and silently address someone else's tab. Re-apply them.
    if tab_name != "default" or "tab" not in config:
        options["tab"] = tab_name
    if session_name != "default" or "session" not in config:
        options["session"] = session_name
    if headless:
        options["headless"] = True
    if persistent:
        options["persistent"] = True
    if profile:
        options["profile"] = profile
    if proxy:
        options["proxy"] = proxy

    if command == "kill-all":
        # Must not start a daemon just to stop it, and must work on a wedged one.
        response = _kill_all(port, options, bool(options.pop("force", False)))
    else:
        response = _ensure_and_send(command, args, options, port, headless)

    # Print output
    output = response.get("output", "")
    success = response.get("success", True)

    # --toon renders a successful reply's structured `data`; a reply without
    # it, or a failure, prints exactly as it would without the flag.
    toon_lines = _toon_lines(command, response) if toon and success else None

    if toon_lines is not None:
        click.echo("\n".join(toon_lines))
    elif as_json:
        payload = dict(response)
        if raw and isinstance(payload.get("output"), str):
            payload["output"] = _strip_raw_output(payload["output"])
        click.echo(json.dumps(payload, default=str))
    else:
        if output:
            if raw:
                output = _strip_raw_output(output)
            if output:
                click.echo(output)

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
