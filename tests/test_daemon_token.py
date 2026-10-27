"""The daemon token: written 0600 before listening, required on every message."""

import asyncio
import json
import socket
import stat
import struct
import sys

import pytest

from patchright_cli import cli, daemon


@pytest.fixture
def token_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon, "TOKEN_DIR", tmp_path)
    return tmp_path


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _send_raw(port: int, msg: dict) -> dict:
    data = json.dumps(msg).encode("utf-8")
    with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
        sock.sendall(struct.pack("!I", len(data)) + data)
        length = struct.unpack("!I", cli._recv_exact(sock, 4))[0]
        return json.loads(cli._recv_exact(sock, length).decode("utf-8"))


async def _with_daemon(port: int, body):
    """Run the real daemon on `port`, run blocking `body(port)` against it, then stop it."""
    task = asyncio.create_task(daemon.run_daemon(port, headless=True))
    for _ in range(100):
        if daemon.read_daemon_token(port):
            break
        await asyncio.sleep(0.02)
    try:
        return await asyncio.to_thread(body, port)
    finally:
        await asyncio.to_thread(cli._send_command, "kill-all", [], {}, port)
        await asyncio.wait_for(task, 10)


def test_token_file_is_per_port(token_dir):
    a = daemon.write_daemon_token(9398)
    b = daemon.write_daemon_token(9399)
    assert a != b
    assert daemon.read_daemon_token(9398) == a
    assert daemon.read_daemon_token(9399) == b
    assert sorted(p.name for p in token_dir.iterdir()) == ["daemon-9398.token", "daemon-9399.token"]


def test_stale_token_is_overwritten_and_removed_only_by_its_owner(token_dir):
    old = daemon.write_daemon_token(9398)
    new = daemon.write_daemon_token(9398)
    assert old != new
    assert daemon.read_daemon_token(9398) == new
    daemon.remove_daemon_token(9398, old)
    assert daemon.read_daemon_token(9398) == new
    daemon.remove_daemon_token(9398, new)
    assert not daemon.token_path(9398).exists()


def test_missing_token_file_reads_as_none(token_dir):
    assert daemon.read_daemon_token(9398) is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes do not apply on Windows")
def test_token_file_mode_is_0600(token_dir):
    daemon.write_daemon_token(9398)
    assert stat.S_IMODE(daemon.token_path(9398).stat().st_mode) == 0o600


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes do not apply on Windows")
def test_token_file_is_0600_even_with_permissive_umask(token_dir):
    import os

    old = os.umask(0)
    try:
        daemon.write_daemon_token(9398)
    finally:
        os.umask(old)
    assert stat.S_IMODE(daemon.token_path(9398).stat().st_mode) == 0o600


def test_check_auth_accepts_right_token():
    assert daemon.check_auth({"command": "list", "token": "abc"}, "abc", 9398) is None


@pytest.mark.parametrize("sent", ["abd", "", 123, None])
def test_check_auth_rejects_wrong_or_missing_token(sent):
    msg = {"command": "list"} if sent is None else {"command": "list", "token": sent}
    assert daemon.check_auth(msg, "abc", 9398)


def test_check_auth_rejects_everything_without_expected_token():
    assert daemon.check_auth({"command": "list", "token": "abc"}, None, 9398)


def test_check_auth_rejects_non_object_message():
    assert daemon.check_auth(["list"], "abc", 9398)


def test_old_client_error_explains_the_fix(token_dir):
    text = daemon.check_auth({"command": "list"}, "abc", 9398)
    assert "requires an auth token" in text
    assert "older than the daemon" in text
    assert "upgrade patchright-cli" in text
    assert "kill-all" in text and "kill-all" in cli.COMMANDS_HELP


def test_daemon_rejects_missing_and_wrong_tokens_and_runs_nothing(token_dir):
    port = _free_port()

    def body(port):
        missing = _send_raw(port, {"command": "kill-all", "args": [], "options": {}})
        wrong = _send_raw(port, {"command": "kill-all", "args": [], "options": {}, "token": "nope"})
        # Neither kill-all ran: the daemon still answers an authenticated client.
        listed = cli._send_command("list", [], {}, port)
        return missing, wrong, listed

    missing, wrong, listed = asyncio.run(_with_daemon(port, body))
    assert missing["success"] is False
    assert "older than the daemon" in missing["output"]
    assert wrong["success"] is False
    assert "rejected the auth token" in wrong["output"]
    assert listed["success"] is True
    # A clean shutdown removes the token file.
    assert not daemon.token_path(port).exists()


def test_cli_sends_token_and_omits_it_without_a_token_file(token_dir, monkeypatch):
    sent = []

    class FakeSocket:
        def __init__(self, *a, **k):
            pass

        def settimeout(self, _):
            pass

        def connect(self, _):
            pass

        def sendall(self, data):
            sent.append(json.loads(data[4:]))

        def close(self):
            pass

    reply = json.dumps({"success": True, "output": ""}).encode()
    monkeypatch.setattr(cli.socket, "socket", FakeSocket)
    monkeypatch.setattr(cli, "_recv_exact", lambda _s, n: struct.pack("!I", len(reply)) if n == 4 else reply)

    cli._send_command("list", [], {}, 9398)
    token = daemon.write_daemon_token(9398)
    cli._send_command("list", [], {}, 9398)
    assert "token" not in sent[0]
    assert sent[1]["token"] == token
