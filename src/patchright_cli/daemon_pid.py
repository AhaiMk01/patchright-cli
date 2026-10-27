"""The daemon's PID file, and the hard stop that uses it when the daemon stops answering.

`kill-all` normally asks the daemon to shut down over its socket. A wedged
daemon cannot answer, and it has no window to close, so the CLI ends the
process itself: it reads `daemon-<port>.pid`, checks the PID still belongs to
the process that wrote the file (PIDs are reused), and kills its process tree.

The file stores the PID and the process's start time; a PID whose start time
differs is some other process and is never touched.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def pid_path(directory: Path, port: int) -> Path:
    return directory / f"daemon-{port}.pid"


def process_start_time(pid: int) -> str | None:
    """An identity for the process `pid`: its start time, or None if it is gone or unreadable."""
    if pid <= 0:
        return None
    if sys.platform == "win32":
        return _windows_start_time(pid)
    with contextlib.suppress(OSError, IndexError, ValueError):
        # Field 22 of /proc/<pid>/stat, counted after the parenthesised name,
        # which may itself contain spaces.
        stat = Path(f"/proc/{pid}/stat").read_text()
        return stat.rsplit(")", 1)[1].split()[19]
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def _windows_start_time(pid: int) -> str | None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    process_query_limited_information = 0x1000
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        exit_code = wintypes.DWORD()
        still_active = 259
        if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)) and exit_code.value != still_active:
            return None  # exited, but a handle still keeps its record alive
        created = times[0]
        return str((created.dwHighDateTime << 32) | created.dwLowDateTime)
    finally:
        kernel32.CloseHandle(handle)


def write_pid_file(directory: Path, port: int) -> None:
    """Record this process as the daemon on `port`. Call after the port is bound."""
    pid = os.getpid()
    path = pid_path(directory, port)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{pid}.tmp")
    tmp.write_text(json.dumps({"pid": pid, "started": process_start_time(pid)}), encoding="utf-8")
    os.replace(tmp, path)


def read_pid_file(directory: Path, port: int) -> dict | None:
    try:
        data = json.loads(pid_path(directory, port).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("pid"), int):
        return None
    return data


def remove_pid_file(directory: Path, port: int) -> None:
    """Delete the port's PID file if this process wrote it."""
    data = read_pid_file(directory, port)
    if data and data["pid"] == os.getpid():
        with contextlib.suppress(OSError):
            pid_path(directory, port).unlink()


def live_daemon_pid(directory: Path, port: int) -> int | None:
    """The PID of the daemon recorded for `port`, if that same process is still running."""
    data = read_pid_file(directory, port)
    if not data or not data.get("started"):
        return None
    pid = data["pid"]
    return pid if process_start_time(pid) == data["started"] else None


def _kill_tree(pid: int) -> None:
    if sys.platform == "win32":
        # /T also ends the browsers the daemon launched. CREATE_NO_WINDOW keeps
        # taskkill from flashing a console when the CLI itself has none.
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=30,
        )
        return
    # The daemon starts in its own session, so its group is its PID.
    try:
        if os.getpgid(pid) == pid:
            os.killpg(pid, signal.SIGKILL)
        else:
            os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def force_stop(directory: Path, port: int, token_file: Path, wait: float = 5.0) -> tuple[str, str]:
    """Kill the daemon on `port` without asking it.

    Returns (status, message); status is "stopped", "absent" (no recorded daemon
    is running) or "failed".
    """
    data = read_pid_file(directory, port)
    if data is None:
        return "absent", f"no daemon PID recorded for port {port}"
    pid = data["pid"]
    if not data.get("started") or process_start_time(pid) != data["started"]:
        # Gone already, or the PID now belongs to another process: clean up only.
        with contextlib.suppress(OSError):
            pid_path(directory, port).unlink()
        return "absent", f"the daemon recorded for port {port} (pid {pid}) is not running"
    try:
        token = token_file.read_text(encoding="utf-8")
    except OSError:
        token = None
    _kill_tree(pid)
    deadline = time.monotonic() + wait
    while process_start_time(pid) == data["started"]:
        if time.monotonic() > deadline:
            return "failed", f"could not stop the daemon on port {port} (pid {pid})"
        time.sleep(0.1)
    # A concurrent `open` may already have started a replacement daemon that
    # rewrote both files; delete them only if they are still the killed one's.
    if read_pid_file(directory, port) == data:
        with contextlib.suppress(OSError):
            pid_path(directory, port).unlink()
    with contextlib.suppress(OSError):
        if token is not None and token_file.read_text(encoding="utf-8") == token:
            token_file.unlink()
    return "stopped", f"Force-stopped the daemon on port {port} (pid {pid}) and its browsers."
