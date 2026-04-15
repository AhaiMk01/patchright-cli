"""Parse Playwright aria_snapshot() output and annotate it with ephemeral refs.

Each accessible node gets a sequential ref (e1, e2, ...) injected as
[ref=eN] so that AI agents can target elements without DOM mutation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from patchright.async_api import Page

_NODE_LINE_RE = re.compile(r"^\s*-\s+(\w+)(?:\s+\"([^\"]*)\")?")


@dataclass
class AriaRefEntry:
    ref: str
    role: str
    name: str
    nth: int


class RefRegistry:
    """Annotates an aria snapshot with refs and resolves them back to Playwright locators."""

    def __init__(self) -> None:
        self.entries: dict[str, AriaRefEntry] = {}
        self._counter = 0

    def parse(self, aria_text: str, max_depth: int | None = None) -> str:
        """Return annotated snapshot text with [ref=eN] tags inserted."""
        self.entries.clear()
        self._counter = 0
        seen: dict[tuple[str, str], int] = {}
        result_lines: list[str] = []

        for line in aria_text.splitlines():
            m = _NODE_LINE_RE.match(line)
            if not m:
                result_lines.append(line)
                continue

            indent = len(line) - len(line.lstrip())
            depth = indent // 2
            if max_depth is not None and depth > max_depth:
                result_lines.append(line)
                continue

            role = m.group(1)
            name = m.group(2) or ""

            self._counter += 1
            ref = f"e{self._counter}"

            key = (role, name)
            nth = seen.get(key, 0)
            seen[key] = nth + 1

            self.entries[ref] = AriaRefEntry(ref=ref, role=role, name=name, nth=nth)
            result_lines.append(f"{line.rstrip()} [ref={ref}]")

        return "\n".join(result_lines)

    def resolve(self, page: Page, ref_str: str):
        """Resolve a ref (with or without leading @) to a Playwright Locator."""
        ref = ref_str.lstrip("@")
        entry = self.entries.get(ref)
        if not entry:
            raise ValueError(f"Ref @{ref} not found. The page may have changed — run 'snapshot' to refresh.")

        kwargs: dict = {"exact": True}
        if entry.name:
            kwargs["name"] = entry.name

        locator = page.get_by_role(entry.role, **kwargs)
        return locator.nth(entry.nth)
