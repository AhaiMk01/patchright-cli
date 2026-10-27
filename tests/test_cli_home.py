"""The no-arg home view: identity, live daemon state, next steps -- never starts anything."""

import json
import socket
import struct
import sys
import threading
import time
from pathlib import Path

import pytest

from patchright_cli import __version__, cli

LINE_BUDGET = 15


def _run(argv, monkeypatch, capsys):
    def boom(*a, **k):
        raise AssertionError("the home view must not start or command the daemon")

    monkeypatch.setattr(cli, "ensure_daemon_running", boom)
    monkeypatch.setattr(cli, "_send_command", boom)
    monkeypatch.setattr(sys, "argv", ["patchright-cli", *argv])
    code = 0
    try:
        cli.main()
    except SystemExit as exc:
        code = exc.code or 0
    out, err = capsys.readouterr()
    return code, out, err


def _daemon_down(monkeypatch):
    monkeypatch.setattr(cli, "read_daemon_token", lambda port: None)


def _daemon_up(monkeypatch, response, seen=None):
    """A running daemon, faked at the socket: answers one `list` request with `response`."""
    monkeypatch.setattr(cli, "read_daemon_token", lambda port: "tok")
    reply = json.dumps(response).encode("utf-8")

    class FakeSocket:
        def __init__(self):
            self.buf = struct.pack("!I", len(reply)) + reply

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def settimeout(self, t):
            pass

        def sendall(self, data):
            if seen is not None:
                seen.append(json.loads(data[4:].decode("utf-8")))

        def recv(self, n):
            chunk, self.buf = self.buf[:n], self.buf[n:]
            return chunk

    def connect(address, timeout=None):
        assert timeout is not None and timeout <= 1
        return FakeSocket()

    monkeypatch.setattr(socket, "create_connection", connect)


LIST_REPLY = {
    "success": True,
    "output": "### Sessions\n  - default *: 2 tab(s)\n  - work: 1 tab(s)",
    "data": [
        {"name": "default", "status": "open", "url": "https://example.com/"},
        {"name": "work", "status": "attached", "url": "about:blank"},
    ],
}


def test_daemon_down(monkeypatch, capsys):
    _daemon_down(monkeypatch)
    code, out, _ = _run([], monkeypatch, capsys)
    lines = out.splitlines()
    assert code == 0
    assert f"v{__version__}" in lines[0] and "anti-detect Chrome automation CLI" in lines[0]
    assert lines[1] == f"daemon: not running on port {cli.DEFAULT_PORT} (0 sessions)"
    assert "help: patchright-cli open <url>  (starts the daemon and a browser)" in lines
    assert any("--help" in line for line in lines if line.startswith("help:"))
    assert "Commands:" not in out
    assert len(lines) <= LINE_BUDGET


def test_daemon_up_lists_sessions_with_tab_counts(monkeypatch, capsys):
    seen = []
    _daemon_up(monkeypatch, LIST_REPLY, seen)
    code, out, _ = _run([], monkeypatch, capsys)
    lines = out.splitlines()
    assert code == 0
    assert lines[1] == f"daemon: running on port {cli.DEFAULT_PORT} (2 sessions)"
    assert "sessions[2]{name,status,url,tabs}:" in lines
    assert '  default,open,"https://example.com/",2' in lines
    assert '  work,attached,"about:blank",1' in lines
    assert any(line.startswith("help: patchright-cli snapshot") for line in lines)
    assert any(line.startswith("help: patchright-cli list") for line in lines)
    assert seen[0]["command"] == "list" and seen[0]["token"] == "tok"
    assert len(lines) <= LINE_BUDGET


def test_daemon_up_without_sessions(monkeypatch, capsys):
    _daemon_up(monkeypatch, {"success": True, "output": "### Sessions\n  0 sessions open", "data": []})
    _, out, _ = _run([], monkeypatch, capsys)
    assert f"daemon: running on port {cli.DEFAULT_PORT} (0 sessions)" in out
    assert "help: patchright-cli open <url>  (the daemon is up, no browser yet)" in out


def test_many_sessions_stay_within_budget(monkeypatch, capsys):
    rows = [{"name": f"s{i}", "status": "open", "url": "about:blank"} for i in range(40)]
    _daemon_up(monkeypatch, {"success": True, "output": "", "data": rows})
    _, out, _ = _run([], monkeypatch, capsys)
    lines = out.splitlines()
    assert "(40 sessions)" in lines[1]
    assert f"  ... {40 - cli._HOME_MAX_SESSIONS} more" in lines
    assert len(lines) <= LINE_BUDGET


def test_port_flag_is_respected(monkeypatch, capsys):
    ports = []
    monkeypatch.setattr(cli, "read_daemon_token", lambda port: ports.append(port))
    _, out, _ = _run(["--port=9451"], monkeypatch, capsys)
    assert ports == [9451]
    assert "daemon: not running on port 9451 (0 sessions)" in out
    assert "help: patchright-cli --port=9451 open <url>" in out


@pytest.mark.parametrize(
    "reply",
    [
        {"success": False, "output": "Daemon rejected the auth token"},
        {"success": True, "output": "no data field"},
        "not an object",
    ],
)
def test_odd_replies_read_as_down(reply, monkeypatch, capsys):
    _daemon_up(monkeypatch, reply)
    code, out, _ = _run([], monkeypatch, capsys)
    assert code == 0
    assert "daemon: not running" in out


def test_any_failure_degrades_to_the_down_view(monkeypatch, capsys):
    def explode(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "_query_running_daemon", explode)
    monkeypatch.setattr(cli, "_display_bin", explode)
    code, out, _ = _run([], monkeypatch, capsys)
    assert code == 0
    assert "daemon: not running" in out
    assert "error" not in out


def test_stale_token_with_nothing_listening_is_fast(monkeypatch, capsys):
    """A token file left by a crashed daemon: a real refused connect, well under a second."""
    monkeypatch.setattr(cli, "read_daemon_token", lambda port: "tok")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    start = time.monotonic()
    code, out, _ = _run([f"--port={port}"], monkeypatch, capsys)
    assert code == 0
    assert f"daemon: not running on port {port}" in out
    assert time.monotonic() - start < 1.0


def test_silent_listener_times_out_quickly(monkeypatch, capsys):
    """Something on the port that never answers must not hang the session-start hook."""
    monkeypatch.setattr(cli, "read_daemon_token", lambda port: "tok")
    monkeypatch.setattr(cli, "_HOME_REPLY_TIMEOUT", 0.2)
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    accepted = []
    threading.Thread(target=lambda: accepted.append(server.accept()), daemon=True).start()
    try:
        _, out, _ = _run([f"--port={server.getsockname()[1]}"], monkeypatch, capsys)
    finally:
        server.close()
    assert "daemon: not running" in out


def test_help_keeps_the_full_manual(monkeypatch, capsys):
    _daemon_down(monkeypatch)
    for flag in ("--help", "-h"):
        code, out, _ = _run([flag], monkeypatch, capsys)
        assert code == 0
        assert "Commands:" in out and "daemon:" not in out


def test_toon_and_json_render_the_state(monkeypatch, capsys):
    _daemon_up(monkeypatch, LIST_REPLY)
    _, out, _ = _run(["--toon"], monkeypatch, capsys)
    assert "sessions[2]{name,status,url,tabs}:" in out
    assert "  running: true" in out
    _, out, _ = _run(["--json"], monkeypatch, capsys)
    state = json.loads(out)
    assert state["daemon"] == {"port": cli.DEFAULT_PORT, "running": True}
    assert state["sessions"][0] == {"name": "default", "status": "open", "url": "https://example.com/", "tabs": 2}


def test_json_and_toon_together_is_a_usage_error(monkeypatch, capsys):
    _daemon_down(monkeypatch)
    code, out, _ = _run(["--json", "--toon"], monkeypatch, capsys)
    assert code == 2
    assert out.startswith("error: --json and --toon are mutually exclusive")


@pytest.mark.parametrize(
    ("argv0", "expected"),
    [
        (str(Path(cli.__file__).parent / "__main__.py"), "python -m patchright_cli"),
        ("", "python -m patchright_cli"),
    ],
)
def test_display_bin_module_run(argv0, expected):
    assert cli._display_bin(argv0) == expected


def test_display_bin_collapses_home(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    exe = tmp_path / "bin" / "patchright-cli"
    assert cli._display_bin(str(exe)) == "~" + cli.os.sep + str(Path("bin") / "patchright-cli")


def test_render_failure_degrades_to_the_down_view(monkeypatch, capsys):
    _daemon_up(monkeypatch, LIST_REPLY)
    real = cli._home_lines

    def fail_when_up(state):
        if state["daemon"]["running"]:
            raise ValueError("render failed")
        return real(state)

    monkeypatch.setattr(cli, "_home_lines", fail_when_up)
    code, out, _ = _run([], monkeypatch, capsys)
    assert code == 0
    assert "daemon: not running" in out
