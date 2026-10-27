"""The daemon PID file and `kill-all`'s hard-stop fallback for a daemon that stops answering."""

import json
import os
import subprocess
import sys

import pytest

from patchright_cli import cli, daemon_pid

# -- PID file ------------------------------------------------------------------


def test_pid_file_round_trip(tmp_path):
    daemon_pid.write_pid_file(tmp_path, 9999)
    data = daemon_pid.read_pid_file(tmp_path, 9999)
    assert data["pid"] == os.getpid()
    assert data["started"] == daemon_pid.process_start_time(os.getpid())
    assert daemon_pid.live_daemon_pid(tmp_path, 9999) == os.getpid()
    daemon_pid.remove_pid_file(tmp_path, 9999)
    assert not daemon_pid.pid_path(tmp_path, 9999).exists()


def test_remove_leaves_another_daemons_file(tmp_path):
    path = daemon_pid.pid_path(tmp_path, 9999)
    path.write_text(json.dumps({"pid": os.getpid() + 1, "started": "x"}))
    daemon_pid.remove_pid_file(tmp_path, 9999)
    assert path.exists()


@pytest.mark.parametrize("content", ["", "not json", "[]", '{"pid": "12"}'])
def test_unreadable_pid_file_reads_as_none(tmp_path, content):
    daemon_pid.pid_path(tmp_path, 9999).write_text(content)
    assert daemon_pid.read_pid_file(tmp_path, 9999) is None


def test_start_time_of_an_exited_process_is_none():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    assert daemon_pid.process_start_time(proc.pid) is None
    assert daemon_pid.process_start_time(0) is None


@pytest.mark.skipif(sys.platform == "win32", reason="zombies are a POSIX state")
def test_killed_but_unreaped_process_reads_as_gone():
    # A killed daemon whose parent has not reaped it keeps its start time in
    # /proc (or ps); force_stop must not wait on it as if it were alive.
    import signal
    import time

    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        assert daemon_pid.process_start_time(proc.pid) is not None
        os.kill(proc.pid, signal.SIGKILL)
        for _ in range(50):  # until it is a zombie: dead, but not waited on
            if daemon_pid.process_start_time(proc.pid) is None:
                break
            time.sleep(0.1)
        assert daemon_pid.process_start_time(proc.pid) is None
    finally:
        proc.wait()


def test_reused_pid_is_not_the_daemon(tmp_path):
    # Same PID, different start time: another process now owns that PID.
    path = daemon_pid.pid_path(tmp_path, 9999)
    path.write_text(json.dumps({"pid": os.getpid(), "started": "some other process"}))
    assert daemon_pid.live_daemon_pid(tmp_path, 9999) is None
    status, _ = daemon_pid.force_stop(tmp_path, 9999, tmp_path / "token")
    assert status == "absent"
    assert not path.exists()  # stale record cleaned up, nothing killed


def test_force_stop_without_a_pid_file_is_absent(tmp_path):
    assert daemon_pid.force_stop(tmp_path, 9999, tmp_path / "token")[0] == "absent"


def test_force_stop_kills_the_recorded_process_tree(tmp_path):
    # A stand-in daemon with a child, like the daemon and its browser.
    code = "import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); time.sleep(60)"
    kwargs = {"start_new_session": True} if sys.platform != "win32" else {}
    proc = subprocess.Popen([sys.executable, "-c", code], **kwargs)
    try:
        started = None
        for _ in range(50):
            started = daemon_pid.process_start_time(proc.pid)
            if started:
                break
        daemon_pid.pid_path(tmp_path, 9999).write_text(json.dumps({"pid": proc.pid, "started": started}))
        token = tmp_path / "daemon-9999.token"
        token.write_text("secret")

        status, message = daemon_pid.force_stop(tmp_path, 9999, token)

        assert status == "stopped", message
        assert proc.wait(timeout=10) is not None
        assert not daemon_pid.pid_path(tmp_path, 9999).exists()
        assert not token.exists()
    finally:
        if proc.poll() is None:
            proc.kill()


def test_force_stop_leaves_a_replacement_daemons_files(tmp_path, monkeypatch):
    # A concurrent `open` can start a new daemon on the port while the old one
    # is being killed; its PID and token files must survive the cleanup.
    old = {"pid": 4242, "started": "old"}
    path = daemon_pid.pid_path(tmp_path, 9999)
    path.write_text(json.dumps(old))
    token = tmp_path / "daemon-9999.token"
    token.write_text("old-token")
    killed = []

    def kill(pid):
        killed.append(pid)
        path.write_text(json.dumps({"pid": 4343, "started": "new"}))
        token.write_text("new-token")

    monkeypatch.setattr(daemon_pid, "_kill_tree", kill)
    monkeypatch.setattr(daemon_pid, "process_start_time", lambda pid: "old" if pid == 4242 and not killed else None)

    assert daemon_pid.force_stop(tmp_path, 9999, token)[0] == "stopped"
    assert json.loads(path.read_text())["pid"] == 4343
    assert token.read_text() == "new-token"


# -- kill-all in the CLI ---------------------------------------------------------


def _run(argv, monkeypatch, capsys, send):
    def no_start(*a, **k):
        raise AssertionError("kill-all must not start a daemon")

    monkeypatch.setattr(cli, "ensure_daemon_running", no_start)
    monkeypatch.setattr(cli, "_send_command", send)
    monkeypatch.setattr(cli, "_warn_stale_skills", lambda: None)
    monkeypatch.setattr(cli, "_load_config", lambda p: {})
    monkeypatch.setattr(sys, "argv", ["patchright-cli", *argv])
    try:
        cli.main()
        code = 0
    except SystemExit as exc:
        code = exc.code or 0
    return code, capsys.readouterr().out


def _fake_force(monkeypatch, status, message="Force-stopped the daemon."):
    calls = []

    def force(directory, port, token_file):
        calls.append(port)
        return status, message

    monkeypatch.setattr(daemon_pid, "force_stop", force)
    return calls


def test_polite_kill_all_does_not_force(monkeypatch, capsys):
    calls = _fake_force(monkeypatch, "stopped")
    code, out = _run(["kill-all"], monkeypatch, capsys, lambda *a, **k: {"success": True, "output": "Killed 2."})
    assert (code, out.strip(), calls) == (0, "Killed 2.", [])


def test_wedged_daemon_is_force_stopped(monkeypatch, capsys):
    # Alive, port open, and it answers neither kill-all nor the `list` probe.
    def hang(*a, **k):
        raise TimeoutError("timed out")

    monkeypatch.setattr(daemon_pid, "live_daemon_pid", lambda d, p: 4242)
    monkeypatch.setattr(cli, "_port_listening", lambda port: True)
    calls = _fake_force(monkeypatch, "stopped")
    code, out = _run(["--port=9400", "kill-all"], monkeypatch, capsys, hang)
    assert code == 0
    assert calls == [9400]
    assert "stopped answering; force-stopping it" in out
    assert "Force-stopped the daemon." in out


def test_slow_but_responsive_daemon_is_not_killed(monkeypatch, capsys):
    # kill-all's reply times out while the daemon closes browsers, but it
    # still answers `list`, so the CLI waits for it to exit instead of killing.
    state = {"probes": 0}

    def send(command, args, options, port=0, timeout=None):
        if command == "kill-all":
            raise TimeoutError("timed out")
        state["probes"] += 1
        return {"success": True, "output": ""}

    monkeypatch.setattr(daemon_pid, "live_daemon_pid", lambda d, p: None if state["probes"] >= 2 else 4242)
    monkeypatch.setattr(cli, "_port_listening", lambda port: state["probes"] < 2)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    calls = _fake_force(monkeypatch, "stopped")
    code, out = _run(["--port=9400", "kill-all"], monkeypatch, capsys, send)
    assert (code, calls) == (0, [])
    assert out.strip() == "Daemon on port 9400 stopped."


def test_force_skips_asking(monkeypatch, capsys):
    def ask(*a, **k):
        raise AssertionError("--force must not ask the daemon")

    calls = _fake_force(monkeypatch, "stopped")
    code, _ = _run(["kill-all", "--force"], monkeypatch, capsys, ask)
    assert (code, calls) == (0, [cli.DEFAULT_PORT])


def test_nothing_running_is_a_no_op(monkeypatch, capsys):
    def refused(*a, **k):
        raise ConnectionRefusedError

    monkeypatch.setattr(daemon_pid, "live_daemon_pid", lambda d, p: None)
    calls = _fake_force(monkeypatch, "stopped")
    code, out = _run(["--port=9400", "kill-all"], monkeypatch, capsys, refused)
    assert (code, calls) == (0, [])
    assert out.strip() == "No daemon running on port 9400 (no-op)"


def test_force_with_nothing_running_is_a_no_op(monkeypatch, capsys):
    _fake_force(monkeypatch, "absent", "no daemon PID recorded")
    monkeypatch.setattr(cli, "_port_listening", lambda port: False)
    code, out = _run(["kill-all", "--force"], monkeypatch, capsys, None)
    assert code == 0
    assert "(no-op)" in out


def test_unrecorded_daemon_on_the_port_is_an_error(monkeypatch, capsys):
    # An older daemon writes no PID file, so there is nothing safe to kill.
    _fake_force(monkeypatch, "absent", "no daemon PID recorded")
    monkeypatch.setattr(cli, "_port_listening", lambda port: True)
    code, out = _run(["kill-all", "--force"], monkeypatch, capsys, None)
    assert code == 1
    assert out.startswith("error: port")
    assert "help:" in out


def test_kill_all_json_prints_the_reply(monkeypatch, capsys):
    reply = {"success": True, "output": "Killed 1 session(s) and stopping daemon."}
    code, out = _run(["--json", "kill-all"], monkeypatch, capsys, lambda *a, **k: reply)
    assert (code, json.loads(out)) == (0, reply)

    _fake_force(monkeypatch, "stopped", "Force-stopped.")
    code, out = _run(["--json", "kill-all", "--force"], monkeypatch, capsys, None)
    assert (code, json.loads(out)) == (0, {"success": True, "output": "Force-stopped."})
