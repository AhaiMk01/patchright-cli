"""Generate accessibility-tree snapshots from Patchright pages.

Uses Playwright's native aria_snapshot() and annotates the output with
ephemeral refs via RefRegistry.
"""

from __future__ import annotations

import time
from pathlib import Path

from patchright_cli.ref_registry import RefRegistry


async def take_snapshot(
    page, root_element=None, max_depth: int | None = None, interactive_only: bool = False
) -> tuple[str, RefRegistry]:
    """Take a DOM snapshot. Returns (annotated_text, registry).

    If root_element is provided, only snapshot the subtree under that element.
    """
    try:
        if root_element:
            aria_text = await root_element.aria_snapshot()
        else:
            aria_text = await page.locator("body").aria_snapshot()
    except Exception:
        aria_text = ""

    if not aria_text or not aria_text.strip():
        return "# Empty page - no accessible elements found\n", RefRegistry()

    registry = RefRegistry()
    annotated = registry.parse(aria_text, max_depth=max_depth, interactive_only=interactive_only)
    return annotated + "\n", registry


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
