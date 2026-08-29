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

# Matches `- role: value` lines (e.g. `- text: Star 95.3k`), whose accessible
# name is unquoted. Used only for search text, never for locator resolution.
_VALUE_LINE_RE = re.compile(r"^\s*-\s+\w+:\s*(.+)$")

# `/pattern/flags` form, matching playwright-cli's convention.
_SLASH_PATTERN_RE = re.compile(r"^/(.*)/([ims]*)$", re.S)
_REGEX_FLAGS = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL}


def _compile_query(query: str, regex: bool):
    """Return a predicate over a node's search text. Raises re.error on a bad pattern."""
    if not regex:
        needle = query.lower()
        return lambda text: needle in text.lower()

    slash_match = _SLASH_PATTERN_RE.match(query)
    if slash_match:
        pattern, flag_chars = slash_match.group(1), slash_match.group(2)
    else:
        # A bare pattern is case-insensitive, matching the substring default.
        pattern, flag_chars = query, "i"

    flags = 0
    for char in flag_chars:
        flags |= _REGEX_FLAGS[char]

    compiled = re.compile(pattern, flags)
    return lambda text: bool(compiled.search(text))


def _depth(line: str) -> int:
    """Tree depth of an aria snapshot line. Playwright indents two spaces per level."""
    return (len(line) - len(line.lstrip())) // 2


def _truncate(text: str, limit: int) -> str:
    """Shorten with an ASCII ellipsis. Non-ASCII mangles on the Windows console."""
    return text if len(text) <= limit else text[: limit - 3] + "..."


INTERACTIVE_ROLES = frozenset(
    {
        "link",
        "button",
        "textbox",
        "textarea",
        "combobox",
        "checkbox",
        "radio",
        "switch",
        "slider",
        "tab",
        "menuitem",
        "option",
        "select",
        "listbox",
        "searchbox",
        "spinbutton",
        "treeitem",
    }
)

# Roles `find` searches by default. Headings earn a place because they are the
# landmarks agents orient by on content pages. Deliberately a separate constant:
# adding "heading" to INTERACTIVE_ROLES would silently change `snapshot -i`.
SEARCHABLE_ROLES = INTERACTIVE_ROLES | {"heading"}


@dataclass
class AriaRefEntry:
    ref: str
    role: str
    name: str
    nth: int
    line_index: int = -1
    search_text: str = ""


@dataclass
class FindHit:
    """One `find` match: the node, where it sits, and what to print for it."""

    ref: str
    line_index: int
    breadcrumb: str
    block: list[str]


class RefRegistry:
    """Annotates an aria snapshot with refs and resolves them back to Playwright locators."""

    def __init__(self) -> None:
        self.entries: dict[str, AriaRefEntry] = {}
        self._counter = 0
        self._lines: list[str] = []

    def parse(self, aria_text: str, max_depth: int | None = None, interactive_only: bool = False) -> str:
        """Return annotated snapshot text with [ref=eN] tags inserted."""
        self.entries.clear()
        self._counter = 0
        self._lines = []
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

            if interactive_only and role not in INTERACTIVE_ROLES:
                result_lines.append(line)
                continue
            name = m.group(2) or ""

            self._counter += 1
            ref = f"e{self._counter}"

            key = (role, name)
            nth = seen.get(key, 0)
            seen[key] = nth + 1

            search_text = name
            if not search_text:
                value_match = _VALUE_LINE_RE.match(line)
                if value_match:
                    search_text = value_match.group(1).strip().strip('"')

            self.entries[ref] = AriaRefEntry(
                ref=ref,
                role=role,
                name=name,
                nth=nth,
                line_index=len(result_lines),
                search_text=search_text,
            )
            result_lines.append(f"{line.rstrip()} [ref={ref}]")

        self._lines = result_lines
        return "\n".join(result_lines)

    def search(
        self,
        query: str,
        *,
        regex: bool = False,
        all_roles: bool = False,
        limit: int = 20,
    ) -> tuple[list[FindHit], int]:
        """Find nodes whose accessible text matches `query`.

        Returns (hits, total_matches). `hits` is capped at `limit`; `total_matches`
        is not, so callers can tell the user how much was withheld.
        """
        predicate = _compile_query(query, regex)

        matched = [
            entry
            for entry in self.entries.values()
            if (all_roles or entry.role in SEARCHABLE_ROLES) and entry.search_text and predicate(entry.search_text)
        ]

        hits = [
            FindHit(
                ref=entry.ref,
                line_index=entry.line_index,
                breadcrumb=self._breadcrumb(entry.line_index),
                block=self._block(entry.line_index),
            )
            for entry in matched[:limit]
        ]
        return hits, len(matched)

    def _breadcrumb(self, line_index: int, max_ancestors: int = 3, trunc: int = 45) -> str:
        """Compact ancestor path, outermost to innermost.

        One line instead of the real ancestor lines: measured on four real pages,
        this costs 3KB across 13 queries where full ancestor lines cost 15KB, and
        disambiguates just as well.
        """
        chain: list[tuple[str, str]] = []
        depth = _depth(self._lines[line_index])

        for i in range(line_index - 1, -1, -1):
            line = self._lines[i]
            if not line.strip():
                continue
            line_depth = _depth(line)
            if line_depth >= depth:
                continue
            depth = line_depth
            node = _NODE_LINE_RE.match(line)
            if node:
                chain.append((node.group(1), node.group(2) or ""))
            if depth == 0:
                break

        chain.reverse()

        # ARIA tables routinely nest `row "X" > cell "X"`; keep the innermost only.
        collapsed: list[tuple[str, str]] = []
        for role, name in chain:
            if collapsed and name and collapsed[-1][1] == name:
                collapsed[-1] = (role, name)
            else:
                collapsed.append((role, name))

        parts = [f'{role} "{_truncate(name, trunc)}"' if name else role for role, name in collapsed[-max_ancestors:]]
        return " > ".join(parts)

    def _block(self, line_index: int) -> list[str]:
        """The matched line plus its subtree, re-indented relative to the match."""
        matched = self._lines[line_index]
        base_depth = _depth(matched)
        indent = len(matched) - len(matched.lstrip())

        block = [matched[indent:]]
        for i in range(line_index + 1, len(self._lines)):
            line = self._lines[i]
            if not line.strip():
                continue
            if _depth(line) <= base_depth:
                break
            block.append(line[indent:] if line.startswith(" " * indent) else line.lstrip())
        return block

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


def render_hits(hits: list[FindHit], total: int, query: str) -> str:
    """Format search results for the CLI.

    Emits no `### ` section headers: `--raw` strips lines that follow one, which
    would eat the result blocks.
    """
    if not hits:
        return (
            f'No matches for "{query}".\n'
            "Try --all to search non-interactive nodes (text, paragraphs), or --regex for a pattern."
        )

    if total > len(hits):
        header = f'Found {len(hits)} of {total} matches for "{query}". Narrow the query, or raise --limit to see more.'
    else:
        header = f'Found {total} match{"" if total == 1 else "es"} for "{query}".'

    lines = [header, ""]
    for hit in hits:
        if hit.breadcrumb:
            lines.append(f"  # {hit.breadcrumb}")
        lines.extend(hit.block)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
