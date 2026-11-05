"""Shared test setup."""

import subprocess
import sys


def pytest_configure(config):
    if sys.platform == "win32":
        _hide_child_consoles()


def _hide_child_consoles():
    """Start every child process with CREATE_NO_WINDOW on Windows.

    When pytest runs without a console of its own (an IDE, an agent's shell),
    each console child it starts (python, icacls, whoami, the Patchright
    driver) opens a new visible console window that takes focus. The daemon
    already does this for itself (see ensure_daemon_running); this covers the
    processes the tests start.
    """
    original = subprocess.Popen.__init__
    if getattr(original, "_hides_console", False):
        return

    def __init__(self, *args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
        original(self, *args, **kwargs)

    __init__._hides_console = True
    subprocess.Popen.__init__ = __init__
