"""Text operations on Playwright's AI aria snapshot: trimming, scoping, filters and `find`.

The daemon takes one full-page `aria_snapshot(mode="ai")` (see snapshot.py)
and everything here works on its text, so none of it touches the page's ref
map. A line looks like

    - button "Save" [ref=e5] [cursor=pointer]
    - region "Form" [ref=f3e2] [box=8,101,1264,66]:
    - text: Name

Refs are Playwright's own (`e5`, or `f3e5` in a child frame and after a
navigation) and are printed verbatim.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# -- refs ----------------------------------------------------------------------

REF_FORMAT = re.compile(r"^(?:f\d+)?e\d+$")
REF_TAG = re.compile(r"\[ref=((?:f\d+)?e\d+)\]")
_BOX_TAG = re.compile(r"\[box=(-?\d+),(-?\d+),(-?\d+),(-?\d+)\]")


def normalize_ref(value) -> str | None:
    """`e5`, `@e5`, `ref=e5` or `[ref=e5]` as `e5`; None for anything that is not a ref."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1].strip()
    if text.startswith("ref="):
        text = text[4:]
    text = text.removeprefix("@")
    return text if REF_FORMAT.match(text) else None


def has_ref(raw: str | None, ref: str) -> bool:
    """Whether a snapshot text shows this ref."""
    return bool(raw) and f"[ref={ref}]" in raw


def frame_prefix(ref: str) -> str:
    """`f3` for `f3e5`, `` for `e5`."""
    return ref[: ref.index("e")]


def line_of(raw: str, ref: str) -> str | None:
    """The snapshot line that carries this ref."""
    tag = f"[ref={ref}]"
    for line in raw.splitlines():
        if tag in line:
            return line
    return None


# -- node lines --------------------------------------------------------------------

# Playwright writes each node as `- role "name" [attrs]`, with the name
# JSON-encoded, except that a name which starts and ends with "/" is written
# as is (`- link /about/:`). A key containing YAML-special text (": ", " #")
# is wrapped in YAML single quotes, with ' doubled. A plain key never contains
# ":" followed by whitespace or the line end, so that marks where its value
# starts.
_ITEM_RE = re.compile(r"^\s*-\s+(?:'((?:[^']|'')*)'|((?:[^:]|:(?!\s|$))*))")
_KEY_RE = re.compile(r"^(\w+)(?:\s+(?:\"((?:[^\"\\]|\\.)*)\"|(/.*/)(?=(?:\s\[[^\]]*\])*$)))?")


def parse_node(line: str) -> tuple[str, str] | None:
    """Return (role, name) for an aria snapshot node line, or None."""
    item = _ITEM_RE.match(line)
    if not item:
        return None
    key = item.group(1).replace("''", "'") if item.group(1) is not None else item.group(2)
    m = _KEY_RE.match(key)
    if not m:
        return None
    if m.group(2) is not None:
        return m.group(1), _unescape_name(m.group(2))
    return m.group(1), m.group(3) or ""


def _unescape_name(raw: str) -> str:
    """Undo the JSON escaping Playwright applies to an accessible name.

    A name containing a quote arrives as `- button "Say \\"hi\\""`; matching it
    naively truncated at the first inner quote.
    """
    out = []
    i = 0
    while i < len(raw):
        if raw[i] == "\\" and i + 1 < len(raw):
            out.append(raw[i + 1])
            i += 2
        else:
            out.append(raw[i])
            i += 1
    return "".join(out)


# `- role [attrs]: value` lines (e.g. `- text: Star 95.3k`, `- paragraph [ref=e9]: Hi`),
# whose text is unquoted. Used only for search text, never for resolution.
_VALUE_LINE_RE = re.compile(r"^\s*-\s+\w+(?:\s+\[[^\]]*\])*:\s+(.+)$")


def _depth(line: str) -> int:
    """Tree depth of a snapshot line. Playwright indents two spaces per level."""
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
        "menuitemcheckbox",
        "menuitemradio",
        "option",
        "select",
        "listbox",
        "searchbox",
        "spinbutton",
        "treeitem",
    }
)

# Roles `find` searches by default. Headings earn a place because they are the
# landmarks agents orient by on content pages.
SEARCHABLE_ROLES = INTERACTIVE_ROLES | {"heading"}

# Roles whose element is clickable anyway, so `[cursor=pointer]` on them says
# nothing. On a generic (or any other role) it is the only hint that a div acts
# like a button, so it stays there.
_CLICKABLE_ROLES = INTERACTIVE_ROLES - {"textarea", "select"}

# Containers `snapshot -i` keeps for context when they are named (iframes always).
_I_CONTAINERS = frozenset({"region", "dialog", "alertdialog", "navigation", "main", "form", "table"})


# -- tree ------------------------------------------------------------------------


@dataclass
class _Node:
    text: str  # the line without its indent
    children: list[_Node] = field(default_factory=list)
    # Whether the line opened a block (`key:` with children below), so its
    # trailing colon is syntax rather than part of a text value.
    opened: bool = False

    @property
    def role(self) -> str | None:
        node = parse_node(self.text)
        return node[0] if node else None


def _tree(text: str) -> list[_Node]:
    roots: list[_Node] = []
    stack: list[tuple[int, _Node]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        node = _Node(line.strip())
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if stack:
            stack[-1][1].children.append(node)
            stack[-1][1].opened = True
        else:
            roots.append(node)
        stack.append((indent, node))
    return roots


def _render(nodes: list[_Node], level: int = 0, out: list[str] | None = None) -> list[str]:
    out = [] if out is None else out
    for node in nodes:
        text = node.text
        if node.children and not text.endswith(":"):
            text += ":"
        elif not node.children and node.opened:
            text = text.removesuffix(":")
        out.append("  " * level + text)
        _render(node.children, level + 1, out)
    return out


# A wrapper with nothing of its own: no name, no text, no [cursor=pointer].
_BARE_GENERIC = re.compile(r"^- generic(?: \[active\])? \[ref=[^\]]+\](?: \[box=[^\]]+\])?:$")


def _trim_nodes(nodes: list[_Node]) -> list[_Node]:
    out: list[_Node] = []
    for node in nodes:
        children = _trim_nodes(node.children)
        if children and _BARE_GENERIC.match(node.text):
            out.extend(children)  # hoist: the wrapper's ref stays valid, it is just not printed
            continue
        if node.role in _CLICKABLE_ROLES:
            node.text = node.text.replace(" [cursor=pointer]", "")
        node.children = children
        out.append(node)
    return out


def trim(raw: str) -> str:
    """The printed form of a native AI snapshot: bare generic wrappers hoisted
    (the body root included) and `[cursor=pointer]` dropped where the role is
    clickable anyway. Nothing else changes."""
    return "\n".join(_render(_trim_nodes(_tree(raw))))


def subtree(raw: str, ref: str) -> str | None:
    """The line carrying `ref` and everything under it, de-indented. None if the ref is absent."""
    lines = raw.splitlines()
    tag = f"[ref={ref}]"
    for i, line in enumerate(lines):
        if tag not in line:
            continue
        indent = len(line) - len(line.lstrip())
        block = [line[indent:]]
        for nxt in lines[i + 1 :]:
            if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= indent:
                break
            block.append(nxt[indent:])
        return "\n".join(block)
    return None


def _count_refs(nodes: list[_Node]) -> int:
    return sum(len(REF_TAG.findall(n.text)) + _count_refs(n.children) for n in nodes)


def limit_depth(text: str, max_depth: int) -> str:
    """Keep levels 0..max_depth. A node whose children were cut says how many refs it hides."""

    def cut(nodes: list[_Node], level: int) -> list[str]:
        out = []
        for node in nodes:
            if level < max_depth or not node.children:
                out.append(("  " * level) + node.text)
                out.extend(cut(node.children, level + 1))
                continue
            hidden = _count_refs(node.children)
            marker = f"# +{hidden} ref{'' if hidden == 1 else 's'}" if hidden else "# +more"
            out.append(("  " * level) + node.text.removesuffix(":") + ": " + marker)
        return out

    return "\n".join(cut(_tree(text), 0))


def _keeps_for_i(node: _Node) -> bool:
    role = node.role
    if role in INTERACTIVE_ROLES:
        return True
    return role == "generic" and "[cursor=pointer]" in node.text


def _i_nodes(nodes: list[_Node], parent_role: str | None = None) -> list[_Node]:
    out: list[_Node] = []
    for node in nodes:
        role = node.role
        if role is None or role == "text":
            continue  # `/url:` and `text:` lines
        if role == "option" and parent_role in ("combobox", "listbox"):
            node.children = []
            out.append(node)
            continue
        children = _i_nodes(node.children, role)
        if _keeps_for_i(node):
            node.children = children
            out.append(node)
            continue
        named = parse_node(node.text) is not None and parse_node(node.text)[1] != ""
        if children and (role == "iframe" or (role in _I_CONTAINERS and named)):
            node.children = children
            out.append(node)
            continue
        out.extend(children)  # drop the line, hoist what it held
    return out


def interactive_only(text: str) -> str:
    """`snapshot -i`: interactive nodes, clickable generics, a combobox's options,
    and named landmarks/iframes that contain them. Everything else is dropped and
    its children hoisted."""
    return "\n".join(_render(_i_nodes(_tree(text))))


# -- boxes -------------------------------------------------------------------------


def frame_box_anchors(raw: str) -> dict[str, tuple[str, tuple[int, int]]]:
    """For each child frame prefix, the first ref in it with a box: {prefix: (ref, (x, y))}.

    The main frame (the prefix of the snapshot's first ref) is left out: its boxes
    are already page coordinates.
    """
    anchors: dict[str, tuple[str, tuple[int, int]]] = {}
    main = None
    for line in raw.splitlines():
        ref_m = REF_TAG.search(line)
        if not ref_m:
            continue
        prefix = frame_prefix(ref_m.group(1))
        if main is None:
            main = prefix
        box_m = _BOX_TAG.search(line)
        if prefix != main and prefix not in anchors and box_m:
            anchors[prefix] = (ref_m.group(1), (int(box_m.group(1)), int(box_m.group(2))))
    return anchors


def _shifted_box(match: re.Match, offset: tuple[int, int]) -> str:
    x, y, w, h = (int(v) for v in match.groups())
    return f"[box={x + offset[0]},{y + offset[1]},{w},{h}]"


def shift_frame_boxes(raw: str, offsets: dict[str, tuple[int, int]]) -> str:
    """Add each frame's (dx, dy) to the boxes of lines whose ref is in that frame."""
    if not offsets:
        return raw
    out = []
    for line in raw.splitlines():
        ref_m = REF_TAG.search(line)
        if ref_m and (off := offsets.get(frame_prefix(ref_m.group(1)))):
            line = _BOX_TAG.sub(lambda m, off=off: _shifted_box(m, off), line)
        out.append(line)
    return "\n".join(out)


# -- find --------------------------------------------------------------------------

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


@dataclass
class SnapshotNode:
    """One node line of a printed snapshot."""

    ref: str | None
    role: str
    name: str
    line_index: int
    search_text: str
    # The nearest ancestor with a ref, for a node that has none (text, options).
    within: str | None = None


@dataclass
class FindHit:
    """One `find` match: the node, where it sits, and what to print for it."""

    node: SnapshotNode
    breadcrumb: str
    block: list[str]

    @property
    def ref(self) -> str | None:
        return self.node.ref


class SnapshotIndex:
    """The nodes of a printed snapshot, searchable by accessible text."""

    def __init__(self, text: str) -> None:
        self.lines = text.splitlines()
        self.nodes: list[SnapshotNode] = []
        ref_stack: list[tuple[int, str]] = []  # (depth, ref) of open ancestors with a ref
        for i, line in enumerate(self.lines):
            parsed = parse_node(line)
            if parsed is None:
                continue
            depth = _depth(line)
            while ref_stack and ref_stack[-1][0] >= depth:
                ref_stack.pop()
            ref_m = REF_TAG.search(line)
            role, name = parsed
            search_text = name
            if not search_text and (value := _VALUE_LINE_RE.match(line)):
                search_text = value.group(1).strip().strip('"')
            ref = ref_m.group(1) if ref_m else None
            self.nodes.append(
                SnapshotNode(
                    ref=ref,
                    role=role,
                    name=name,
                    line_index=i,
                    search_text=search_text,
                    within=None if ref else (ref_stack[-1][1] if ref_stack else None),
                )
            )
            if ref:
                ref_stack.append((depth, ref))

    def node(self, ref: str) -> SnapshotNode | None:
        return next((n for n in self.nodes if n.ref == ref), None)

    def search(
        self, query: str, *, regex: bool = False, all_roles: bool = False, limit: int = 20
    ) -> tuple[list[FindHit], int]:
        """Find nodes whose accessible text matches `query`. Returns (hits capped at `limit`, total)."""
        predicate = _compile_query(query, regex)
        matched = [
            n
            for n in self.nodes
            if (all_roles or n.role in SEARCHABLE_ROLES) and n.search_text and predicate(n.search_text)
        ]
        hits = [
            FindHit(node=n, breadcrumb=self._breadcrumb(n.line_index), block=self._block(n)) for n in matched[:limit]
        ]
        return hits, len(matched)

    def _breadcrumb(self, line_index: int, max_ancestors: int = 3, trunc: int = 45) -> str:
        """Compact ancestor path, outermost to innermost."""
        chain: list[tuple[str, str]] = []
        depth = _depth(self.lines[line_index])
        for i in range(line_index - 1, -1, -1):
            line = self.lines[i]
            if not line.strip():
                continue
            line_depth = _depth(line)
            if line_depth >= depth:
                continue
            depth = line_depth
            node = parse_node(line)
            if node:
                chain.append(node)
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

    def _block(self, node: SnapshotNode) -> list[str]:
        """The matched line plus its subtree, re-indented relative to the match.

        A node without a ref of its own names the ref to act on as `[in=<ref>]`.
        """
        matched = self.lines[node.line_index]
        base_depth = _depth(matched)
        indent = len(matched) - len(matched.lstrip())
        head = matched[indent:]
        if node.ref is None and node.within:
            head = _with_tag(head, f"[in={node.within}]")
        block = [head]
        for line in self.lines[node.line_index + 1 :]:
            if not line.strip():
                continue
            if _depth(line) <= base_depth:
                break
            block.append(line[indent:] if line.startswith(" " * indent) else line.lstrip())
        return block


def _with_tag(line: str, tag: str) -> str:
    """Add a `[tag]` after a node's key, before any `: value`."""
    item = _ITEM_RE.match(line)
    if not item:
        return f"{line} {tag}"
    end = item.end()
    return f"{line[:end].rstrip()} {tag}{line[end:]}"


def render_hits(hits: list[FindHit], total: int, query: str) -> str:
    """Format search results for the CLI.

    Emits no `### ` section headers: `--raw` strips lines that follow one, which
    would eat the result blocks.
    """
    if total > len(hits):
        header = f'Found {len(hits)} of {total} matches for "{query}". Narrow the query to reduce ambiguity.'
    else:
        header = f'Found {total} match{"" if total == 1 else "es"} for "{query}".'

    lines = [header, ""]
    for hit in hits:
        if hit.breadcrumb:
            lines.append(f"  # {hit.breadcrumb}")
        lines.extend(hit.block)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main_frame_prefix(raw: str) -> str:
    """The frame prefix of the page's own refs: that of the snapshot's first ref."""
    match = REF_TAG.search(raw)
    return frame_prefix(match.group(1)) if match else ""
