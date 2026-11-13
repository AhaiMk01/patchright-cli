import socket
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from patchright_cli import cli

PORT = 9400


@pytest.fixture
def machine(monkeypatch):
    """A machine whose port and process table the test controls."""
    state = SimpleNamespace(listening=False, spawn_starts_daemon=True, spawned=[])

    def create_connection(address, timeout=None):
        assert address == ("127.0.0.1", PORT)
        if not state.listening:
            raise ConnectionRefusedError
        return MagicMock()

    def popen(argv, **kwargs):
        state.spawned.append((argv, kwargs))
        state.listening = state.spawn_starts_daemon

    monkeypatch.setattr(socket, "create_connection", create_connection)
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    return state


def test_a_running_daemon_is_left_alone(machine):
    machine.listening = True
    assert cli.ensure_daemon_running(PORT) is False
    assert machine.spawned == []


@pytest.mark.parametrize("headless, extra", [(False, []), (True, ["--headless"])])
def test_a_missing_daemon_is_started_on_the_port(machine, headless, extra):
    assert cli.ensure_daemon_running(PORT, headless) is True
    [(argv, kwargs)] = machine.spawned
    assert argv == [sys.executable, "-m", "patchright_cli.daemon", "--port", str(PORT), *extra]
    assert {kwargs[name] for name in ("stdin", "stdout", "stderr")} == {subprocess.DEVNULL}


def test_a_daemon_that_never_listens_is_an_error(machine):
    machine.spawn_starts_daemon = False
    with pytest.raises(RuntimeError):
        cli.ensure_daemon_running(PORT)
    assert len(machine.spawned) == 1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows creation flags")
def test_daemon_spawn_uses_a_hidden_console_on_windows(machine):
    # DETACHED_PROCESS leaves a venv launcher console-less, so the real interpreter it
    # starts opens a visible window; CREATE_NO_WINDOW gives a hidden console instead.
    cli.ensure_daemon_running(PORT)
    flags = machine.spawned[0][1]["creationflags"]
    assert flags & subprocess.CREATE_NO_WINDOW
    assert not flags & subprocess.DETACHED_PROCESS


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX sessions")
def test_daemon_spawn_leaves_the_clients_session_on_posix(machine):
    cli.ensure_daemon_running(PORT)
    assert machine.spawned[0][1]["start_new_session"] is True
