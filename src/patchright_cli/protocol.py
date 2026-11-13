"""The client-daemon contract: what both sides must agree on, and nothing else.

docs/daemon-protocol.md describes the wire format for third-party clients.
"""

from __future__ import annotations

from pathlib import Path

PROTOCOL_VERSION = 1
DEFAULT_PORT = 9321
DEFAULT_PICK_WAIT = 60.0

_DAEMON_DIR = Path.home() / ".patchright-cli"


def daemon_dir() -> Path:
    """The directory of a daemon's per-port files: daemon-<port>.token, .pid and .lock."""
    return _DAEMON_DIR


def token_path(port: int) -> Path:
    """The token file for the daemon on `port`, so daemons on different ports coexist."""
    return daemon_dir() / f"daemon-{port}.token"


def read_daemon_token(port: int) -> str | None:
    """The token of the daemon on `port`, or None when its token file is missing, unreadable or empty."""
    try:
        return token_path(port).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None
