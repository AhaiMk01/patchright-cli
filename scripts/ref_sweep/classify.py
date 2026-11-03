"""Classify every step that differs between two sweep results; fail on any step no rule explains.

    python scripts/ref_sweep/classify.py results/before.json results/after.json > results/diff-report.md

Each rule names the steps it covers and why the difference is intended. A step
the rules do not cover is printed as UNEXPLAINED and the exit code is 1.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

COMPARED = ("exit", "out", "state", "snapshot", "missing_targets")

# (category, reason, predicate over (step id, before, after))
RULES = [
    (
        "snapshot format",
        "Native AI-mode YAML: Playwright's own refs printed verbatim (`f1e5` after a navigation), bare `generic` "
        "wrappers hoisted, `[active]`/`[cursor=pointer]` tags, refs after a node's key instead of after its colon, "
        "iframe content included, options and `text:` lines without refs. `--depth` now cuts lines (with `# +N refs`) "
        "where it used to only hide tags; `-i` drops non-interactive lines where it used to only hide tags; scoped "
        "snapshots show page refs instead of renumbering from e1.",
        lambda sid, b, a: (
            "snapshot" in b["argv"][:2]
            and b["exit"] == a["exit"]
            and b.get("missing_targets") == a.get("missing_targets")
        ),
    ),
    (
        "find output",
        "Same search over the new YAML: refs are the page's refs (`f1e28`), ref-less hits show `[in=<ref>]`, and "
        "iframe content is searchable. find --role maps matches through a depth-0 probe (same matches, native refs).",
        lambda sid, b, a: b["argv"][0] == "find" and b["exit"] == a["exit"],
    ),
    (
        "--boxes no longer times out",
        "Old --boxes resolved every ref with a 30 s bounding_box and timed out (controls page > 90 s); native "
        "boxes=True takes ~0.2 s, iframe boxes shifted to page coordinates (iframe:snapshot-boxes f4e4 "
        "[box=332,91,58,21] equals the page box measured in iframe:inner-submit-page-box).",
        lambda sid, b, a: sid == "controls:snapshot-boxes" and b["exit"] == -1 and a["exit"] == 0,
    ),
    (
        "iframe elements are actionable",
        "The old snapshot had no iframe content, so no ref existed (`missing_targets`); native refs (`f4e3`) reach "
        "into the same-origin iframe.",
        lambda sid, b, a: (
            sid.startswith("iframe:") and b.get("missing_targets") and not a.get("missing_targets") and a["exit"] == 0
        ),
    ),
    (
        "refs outside a scoped view keep working",
        "A scoped snapshot used to replace the registry with subtree-local refs, so a page ref then failed; every "
        "view is now cut from one full snapshot.",
        lambda sid, b, a: (
            sid in ("controls:outside-ref-after-selector", "controls:outside-ref-after-ref-scope")
            and b["exit"] == 1
            and a["exit"] == 0
        ),
    ),
    (
        "stale ref refused instead of acting on another element",
        "Old role/name/nth lookup silently hit a different element after a DOM change or navigation (state log shows "
        "item-a2 / stay2 / rerender clicked); native refs fail fast with `no longer matches an element`, `is from a "
        "page this tab has left`, or the tab guard.",
        lambda sid, b, a: sid.startswith("nav:stale-") and a["exit"] == 1,
    ),
    (
        "new ref error wording",
        "Unknown/stale ref errors name the cause: tab guard `is not from a snapshot of this tab; run snapshot (...)`, "
        "`is from a page this tab has left`. Same exit code.",
        lambda sid, b, a: (
            b["exit"] == a["exit"] == 1
            and re.search(r"not found|Re-run", b["out"])
            and re.search(r"is not from a snapshot of this tab|is from a page this tab has left", a["out"])
        ),
    ),
    (
        "malformed ref is a usage error",
        '`click nonsense` exits 2 with `"nonsense" is not a ref (refs look like e5 or f3e5, from snapshot)`.',
        lambda sid, b, a: sid == "controls:bad-ref-word" and a["exit"] == 2,
    ),
    (
        "ref from the previous document refused",
        "`@e1` named the first document's root; after the navigations the page's refs are `f1e*`, and the old code "
        "resolved `e1` by role/name on the new page. The tab guard refuses it.",
        lambda sid, b, a: sid == "controls:bad-ref-at" and b["exit"] == 0 and a["exit"] == 1,
    ),
    (
        "select names the mistake",
        "A ref given as the option fails at once with the label hint instead of a 30 s select_option timeout; a "
        "missing option's timeout lists the combobox's options.",
        lambda sid, b, a: (
            sid in ("controls:select-ref-as-option", "controls:select-missing-option") and b["exit"] == a["exit"]
        ),
    ),
    (
        "ref names in output",
        "Same result, with the native ref in the message (`Highlighted f1e18.`, pick-locator `### Ref - f1e5`) or "
        'the locator in Playwright\'s call log (`locator("aria-ref=f8e4")` instead of `get_by_role(...)`).',
        lambda sid, b, a: (
            b["exit"] == a["exit"]
            and (re.search(r"\b(f\d+)?e\d+\b", a["out"]) or "aria-ref=" in a["out"])
            and a.get("state") == b.get("state")
        ),
    ),
    (
        "pick-locator now finds the ref",
        "Old matching only tried role-based picks (an id-based `#save2` pick got no ref) and capped candidates; the "
        "depth-0 probe maps any picked element, including inside the iframe.",
        lambda sid, b, a: "pick-" in sid and "### Ref" in a["out"] and b["exit"] == a["exit"] == 0,
    ),
    (
        "cascade: page state from earlier steps",
        "Same step result; the fixture's click log / field values differ only because earlier steps now succeed "
        "(iframe fills, refs outside a scoped view) or no longer act on a wrong element.",
        lambda sid, b, a: (
            b["exit"] == a["exit"] and _same_shape(b["out"], a["out"]) and b.get("log_delta") == a.get("log_delta")
        ),
    ),
]


def _add_log_deltas(steps: list[dict]) -> None:
    """What each step itself appended to the fixture's click log, so a cascade cannot hide a different action."""
    prev = ""
    for step in steps:
        state = step.get("state")
        log = state.get("log", "") if isinstance(state, dict) else None
        if log is None:
            continue
        step["log_delta"] = log[len(prev) :] if log.startswith(prev) else log
        prev = log


NOTES = """## Notes

- Known residual, identical before and after: `tabs:a-ref-in-b-same-number`. Two tabs that are both on
  their first document can print the same ref (`e3`); the tab guard cannot tell them apart, so tab b's
  `e3` is clicked. Both versions do this; SKILL.md says refs are per tab and to use the snapshot that
  `tab-select` prints.
- Ref numbers are Playwright's: the page is on a later document by the time most steps run, so refs read
  `f1e18` where the old registry printed `e19`. The sweep's argv names targets by role and name, so the
  steps still line up.
- Timing (ms, CLI round trip): controls `snapshot --boxes` 90016 (timeout) -> ~210; offscreen `--boxes`
  ~4250 -> ~215; large `--boxes` ~2700 -> ~250; plain large `snapshot` unchanged (~220).
"""


def _same_shape(x: str, y: str) -> bool:
    strip = lambda s: re.sub(r"\b(f\d+)?e\d+\b", "<ref>", s)  # noqa: E731
    return strip(x) == strip(y)


def main() -> int:
    runs = []
    for path in sys.argv[1:3]:
        steps = json.loads(Path(path).read_text(encoding="utf-8"))["steps"]
        _add_log_deltas(steps)
        keyed: dict[str, dict] = {}
        for step in steps:  # repeated ids get #2, #3 like sweep.py diff
            key, n = step["id"], 2
            while key in keyed:
                key, n = f"{step['id']}#{n}", n + 1
            keyed[key] = step
        runs.append(keyed)
    before, after = runs
    by_rule: dict[str, list[str]] = {name: [] for name, _, _ in RULES}
    unexplained = []
    for sid, b in before.items():
        a = after.get(sid)
        if a is None:
            unexplained.append(f"{sid} (missing after)")
            continue
        if not [f for f in COMPARED if b.get(f) != a.get(f)]:
            continue
        for name, _, match in RULES:
            if match(sid, b, a):
                by_rule[name].append(f"{sid} (exit {b['exit']} -> {a['exit']})")
                break
        else:
            unexplained.append(sid)

    differing = sum(len(v) for v in by_rule.values()) + len(unexplained)
    print("# Ref sweep: before (RefRegistry) vs after (native aria refs)\n")
    print(
        f"{len(before)} steps; {len(before) - differing} identical; {differing} differ; {len(unexplained)} unexplained.\n"
    )
    for name, reason, _ in RULES:
        if by_rule[name]:
            print(f"## Intended: {name} ({len(by_rule[name])})\n\n{reason}\n")
            for line in by_rule[name]:
                print(f"- {line}")
            print()
    print(NOTES)
    if unexplained:
        print(f"## UNEXPLAINED ({len(unexplained)})\n")
        for sid in unexplained:
            print(f"- {sid}")
    return 1 if unexplained else 0


if __name__ == "__main__":
    sys.exit(main())
