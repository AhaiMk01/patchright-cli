"""The only code that calls Playwright's aria_snapshot().

Any aria_snapshot() call -- default mode, or AI mode on a locator -- replaces
the frame's ref map, after which refs outside it stop resolving with
`page.get_by_ref`. So the daemon takes one full-page AI snapshot and derives
every view from its text (snapshot_text.py), and `tests/test_snapshot_text.py`
fails if `aria_snapshot(` shows up anywhere else in the package.
"""

from __future__ import annotations

import time
from pathlib import Path

from patchright_cli.snapshot_text import REF_TAG

EMPTY_SNAPSHOT = "# Empty page - no accessible elements found\n"


def is_target_closed(exc: Exception) -> bool:
    """The page, context or browser closed under the command.

    Patchright raises every driver-reported close as TargetClosedError, matched
    by name because Patchright is imported lazily. Never by message: a page's
    own error can say "has been closed".
    """
    return type(exc).__name__ == "TargetClosedError"


async def ai_snapshot(page, boxes: bool = False) -> str:
    """The full page's AI snapshot, iframes included. Refreshes the page's ref map.

    Callers hold the tab's snapshot lock (daemon._ai_snapshot does).
    """
    try:
        return await page.aria_snapshot(mode="ai", boxes=True if boxes else None)
    except Exception as exc:
        if is_target_closed(exc):
            raise
        return ""


async def probe_ref(locator) -> str | None:
    """The ref of the one element `locator` matches, read from a depth-0 AI snapshot of it.

    This replaces the element's frame ref map with a one-node map, so the caller
    must take a full ai_snapshot() afterwards (daemon._refs_of does, in a finally).
    """
    try:
        head = (await locator.aria_snapshot(mode="ai", depth=0, timeout=2000)).splitlines()[0]
    except Exception:
        return None
    match = REF_TAG.search(head)
    return match.group(1) if match else None


def save_snapshot(snapshot_text: str, cwd: str | None = None) -> str:
    """Save snapshot text to .patchright-cli/ directory."""
    base = Path(cwd) if cwd else Path.cwd()
    snap_dir = base / ".patchright-cli"
    snap_dir.mkdir(parents=True, exist_ok=True)

    timestamp = int(time.time() * 1000)
    filename = f"page-{timestamp}.yml"
    filepath = snap_dir / filename
    filepath.write_text(snapshot_text, encoding="utf-8")
    return str(filepath)
