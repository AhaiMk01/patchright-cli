"""Regenerate the command reference block in skills/patchright-cli/SKILL.md.

The rest of SKILL.md is hand-written. Only the text between BEGIN and END is
generated, from COMMAND_SPECS and COMMAND_CATEGORIES in cli.py, so a new or
renamed command reaches the skill agents read.

    python scripts/gen_skill.py           # rewrite the block in place
    python scripts/gen_skill.py --check   # exit 1 when the block is stale
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from patchright_cli.cli import COMMAND_CATEGORIES, COMMAND_SPECS

SKILL_PATH = Path(__file__).resolve().parent.parent / "skills" / "patchright-cli" / "SKILL.md"
BEGIN = "<!-- BEGIN GENERATED: commands (scripts/gen_skill.py from COMMAND_SPECS; do not edit by hand) -->"
END = "<!-- END GENERATED -->"
_WIDTH = 40


def _line(name: str) -> str:
    spec = COMMAND_SPECS[name]
    flags = " ".join(f"[--{f}]" for f in spec.get("flags", {}))
    usage = f"{spec['usage']} {flags}".strip()
    return f"{usage}{' ' * max(_WIDTH - len(usage), 2)}# {spec['summary']}"


def render_block() -> str:
    """The generated block, markers included, every command once in manual order."""
    listed = [name for _, names in COMMAND_CATEGORIES for name in names]
    missing = [name for name in COMMAND_SPECS if name not in listed]
    if missing:
        raise SystemExit(f"commands missing from COMMAND_CATEGORIES: {', '.join(missing)}")
    lines = [BEGIN, "```bash"]
    for i, (category, names) in enumerate(COMMAND_CATEGORIES):
        if i:
            lines.append("")
        lines.append(f"# {category}")
        lines.extend(_line(name) for name in names)
    lines += ["```", END]
    return "\n".join(lines)


def sync(text: str) -> str:
    """`text` with its generated block replaced by the current one."""
    start, end = text.find(BEGIN), text.find(END)
    if start < 0 or end < start:
        raise SystemExit(f"SKILL.md has no generated block; add the lines\n{BEGIN}\n{END}")
    return text[:start] + render_block() + text[end + len(END) :]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 when the block is stale; change nothing")
    parser.add_argument("--path", type=Path, default=SKILL_PATH, help="SKILL.md to sync (default: the bundled one)")
    opts = parser.parse_args(argv)

    raw = opts.path.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in raw else "\n"
    text = raw.replace("\r\n", "\n")
    synced = sync(text)
    if synced == text:
        print(f"{opts.path.name}: command block is current")
        return 0
    if opts.check:
        print(f"error: {opts.path.name} command block is stale")
        print("help: run `python scripts/gen_skill.py` and commit the result")
        return 1
    opts.path.write_bytes(synced.replace("\n", newline).encode("utf-8"))
    print(f"{opts.path.name}: command block updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
