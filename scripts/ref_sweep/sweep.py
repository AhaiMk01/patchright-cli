"""Before/after sweep of every command that takes or prints a snapshot ref.

Drives the real CLI (one subprocess per step) against the local fixture pages
in `fixtures/`, served over HTTP so the iframe is same-origin. Each step
records the exit code, the normalized output, the page state the fixture
reports through `window.__state()`, and for `snapshot` steps the YAML itself.

Refs are looked up by role and name in the latest snapshot the CLI printed,
the way an agent uses them, and the recorded argv names the target
(`<button "Save"#1>`) rather than the ref, so two runs line up step by step
even when ref numbering differs.

    python scripts/ref_sweep/sweep.py run --port=9341 --out=before.json
    python scripts/ref_sweep/sweep.py diff before.json after.json

`run` starts (and at the end kill-alls) a daemon on --port; never point it at
a port another daemon is using.
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
SESSION = "snaprefs-sweep"

_SNAPSHOT_LINK = re.compile(r"\[Snapshot\]\(([^)]+)\)")
_REF = re.compile(r"\[ref=([^\]]+)\]")

# Page state as JSON: field values across the document, open shadow roots and
# same-origin iframes, plus the log fixtures keep in <html data-log>. Plain DOM
# reads, because `eval` runs in an isolated world that cannot see page globals.
STATE_JS = """(() => {
  const fields = {};
  const roots = [document];
  const walk = root => root.querySelectorAll('*').forEach(el => {
    if (el.shadowRoot) { roots.push(el.shadowRoot); walk(el.shadowRoot); }
  });
  walk(document);
  document.querySelectorAll('iframe').forEach(f => { try { if (f.contentDocument) roots.push(f.contentDocument); } catch (e) {} });
  for (const root of roots) {
    root.querySelectorAll('input, select, textarea').forEach(el => {
      const key = el.id || el.name || el.type;
      if (el.type === 'checkbox' || el.type === 'radio') fields[key] = el.checked;
      else if (el.type === 'file') fields[key] = Array.from(el.files).map(f => f.name).join(',');
      else fields[key] = el.value;
    });
  }
  const dlg = document.querySelector('dialog');
  return JSON.stringify({
    url: location.pathname + location.hash,
    fields,
    log: document.documentElement.dataset.log || '',
    dialog: dlg ? dlg.open : null,
    scrollY: Math.round(window.scrollY),
  });
})()"""


@dataclass
class Target:
    """An element named the way a person reads the snapshot: role, name, and which duplicate."""

    role: str
    name: str | None = None
    nth: int = 0

    def __str__(self) -> str:
        label = self.role + (f' "{self.name}"' if self.name is not None else "")
        return f"<{label}#{self.nth}>"


@dataclass
class Sweep:
    port: int
    workdir: Path
    base: str
    steps: list[dict] = field(default_factory=list)
    snapshot: str = ""
    stale: dict[str, str] = field(default_factory=dict)

    # -- running the CLI ---------------------------------------------------

    def cli(self, *argv: str, timeout: float = 90) -> tuple[int, str]:
        cmd = [sys.executable, "-m", "patchright_cli", f"--port={self.port}", f"-s={SESSION}", *argv]
        env = {
            **os.environ,
            "PATCHRIGHT_CLI_NO_VERSION_CHECK": "1",
            "PATCHRIGHT_CLI_NO_SKILL_CHECK": "1",
            "PYTHONIOENCODING": "utf-8",
        }
        try:
            proc = subprocess.run(
                cmd, cwd=self.workdir, env=env, capture_output=True, text=True, encoding="utf-8", timeout=timeout
            )
        except subprocess.TimeoutExpired:
            return -1, "<timeout>"
        out = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr.strip() else "")
        self._remember_snapshot(out)
        return proc.returncode, out

    def _remember_snapshot(self, out: str) -> None:
        match = _SNAPSHOT_LINK.search(out)
        if not match:
            return
        path = Path(match.group(1))
        if not path.is_absolute():
            path = self.workdir / path
        with contextlib.suppress(OSError):
            self.snapshot = path.read_text(encoding="utf-8")

    def state(self) -> object:
        code, out = self.cli("eval", STATE_JS)
        if code != 0:
            return f"<state unavailable: {self.normalize(out).strip()[:200]}>"
        try:
            value = json.loads(out.strip())
            return json.loads(value) if isinstance(value, str) else value
        except ValueError:
            return self.normalize(out)

    # -- refs --------------------------------------------------------------

    def ref(self, target: Target) -> str | None:
        """The ref of `target` in the latest snapshot, or None."""
        if target.name is None:
            head = re.compile(rf"^\s*- {re.escape(target.role)}(?=[\s:\[]|$)(?!\s+\")")
        else:
            quoted = json.dumps(target.name, ensure_ascii=False)
            head = re.compile(rf"^\s*- {re.escape(target.role)} {re.escape(quoted)}(?=[\s:\[]|$)")
        seen = 0
        for line in self.snapshot.splitlines():
            if head.search(line) and (ref := _REF.search(line)):
                if seen == target.nth:
                    return ref.group(1)
                seen += 1
        return None

    # -- recording ---------------------------------------------------------

    def normalize(self, text: str) -> str:
        text = text.replace(self.base, "<base>")
        text = text.replace(str(self.workdir), "<work>").replace(str(self.workdir).replace("\\", "/"), "<work>")
        text = re.sub(r"[^\s()]*\.patchright-cli[\\/](page|element)-\d+\.(yml|png)", r"<\1.\2>", text)
        text = re.sub(r"annotation-\d+\.png", "annotation.png", text)
        return text.replace("\r\n", "\n")

    def step(self, step_id: str, *argv, state: bool = True, keep_snapshot: bool = False, timeout: float = 90):
        """Run one CLI step. argv items may be Targets, resolved to refs from the latest snapshot."""
        shown, actual, missing = [], [], []
        for item in argv:
            if isinstance(item, Target):
                ref = self.ref(item)
                shown.append(str(item))
                if ref is None:
                    missing.append(str(item))
                actual.append(ref or "e99999")
            else:
                shown.append(str(item))
                actual.append(str(item))
        start = time.perf_counter()
        code, out = self.cli(*actual, timeout=timeout)
        elapsed = round((time.perf_counter() - start) * 1000)
        record: dict = {
            "id": step_id,
            "argv": shown,
            "refs": [a for a, s in zip(actual, shown, strict=True) if s.startswith("<")],
            "exit": code,
            "out": self.normalize(out),
            "ms": elapsed,
        }
        if missing:
            record["missing_targets"] = missing
        if keep_snapshot:
            record["snapshot"] = self.normalize(self.snapshot)
        if state:
            record["state"] = self.state()
        self.steps.append(record)
        print(f"[{code:>3}] {step_id:<38} {elapsed:>6}ms {' '.join(shown)[:70]}", flush=True)
        return record

    def note_ref(self, key: str, target: Target) -> None:
        """Remember a ref now, to use after the page changes underneath it."""
        self.stale[key] = self.ref(target) or "e99999"

    def stale_step(self, step_id: str, verb: str, key: str, *rest: str):
        """Run `verb <remembered ref> ...` without re-snapshotting first."""
        start = time.perf_counter()
        code, out = self.cli(verb, self.stale[key], *rest)
        record = {
            "id": step_id,
            "argv": [verb, f"<stale {key}>", *rest],
            "refs": [self.stale[key]],
            "exit": code,
            "out": self.normalize(out),
            "ms": round((time.perf_counter() - start) * 1000),
            "state": self.state(),
        }
        self.steps.append(record)
        print(f"[{code:>3}] {step_id:<38} {record['ms']:>6}ms {verb} <stale {key}>", flush=True)

    def pick(self, step_id: str, selector: str) -> None:
        """Run `pick-locator` while the mouse clicks the element this CSS selector matches."""
        code, out = self.cli(
            "eval",
            f"(() => {{ const el = document.querySelector({json.dumps(selector)}); el.scrollIntoView();"
            " const r = el.getBoundingClientRect();"
            " return [Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)]; })()",
        )
        x, y = json.loads(out)
        result: dict = {}

        def run():
            result["value"] = self.cli("pick-locator", "--wait=20")

        thread = threading.Thread(target=run)
        thread.start()
        time.sleep(2)
        self.cli("mousemove", str(x), str(y))
        self.cli("mousedown")
        self.cli("mouseup")
        thread.join(30)
        code, out = result.get("value", (-1, "<no result>"))
        self.steps.append(
            {
                "id": step_id,
                "argv": ["pick-locator", f"<click {selector}>"],
                "refs": [],
                "exit": code,
                "out": self.normalize(out),
                "state": self.state(),
            }
        )
        print(f"[{code:>3}] {step_id:<38}        pick-locator {selector}", flush=True)

    def open(self, page: str) -> None:
        self.step(f"{page}:goto", "goto", f"{self.base}/{page}.html")
        self.snapshot = ""


def run_steps(s: Sweep, upload_file: Path) -> None:
    T = Target
    s.step("open", "--headless", "open", f"{s.base}/controls.html", state=False)
    s.snapshot = ""

    # -- controls: every ref-taking command on plain controls ------------------
    s.open("controls")
    s.step("controls:snapshot", "snapshot", keep_snapshot=True)
    s.step("controls:snapshot-i", "snapshot", "-i", keep_snapshot=True)
    s.step("controls:snapshot-depth2", "snapshot", "--depth=2", keep_snapshot=True)
    s.step("controls:snapshot-boxes", "snapshot", "--boxes", keep_snapshot=True)
    s.step("controls:snapshot-selector", "snapshot", "--selector=#dst", keep_snapshot=True)
    s.step("controls:snapshot-selector-multi", "snapshot", "--selector=button", keep_snapshot=True)
    s.step("controls:snapshot-selector-none", "snapshot", "--selector=#nope", keep_snapshot=True)
    s.step("controls:snapshot-filename", "snapshot", "--filename=sweep-controls.yml", keep_snapshot=False)
    s.step("controls:snapshot-full", "snapshot", keep_snapshot=True)
    s.step("controls:snapshot-ref-list", "snapshot", T("list"), keep_snapshot=True)
    s.step("controls:snapshot-ref-list-depth1", "snapshot", T("list"), "--depth=1", keep_snapshot=True)
    s.step("controls:click-after-scoped", "click", T("button", "Save", 0))
    s.step("controls:snapshot-again", "snapshot", keep_snapshot=True)
    s.step("controls:fill-name", "fill", T("textbox", "Name"), "Alice")
    s.step("controls:fill-bio", "fill", T("textbox", "Bio"), "line one")
    s.step("controls:fill-submit", "fill", T("textbox", "Name"), "Bob", "--submit")
    s.step("controls:click-name", "click", T("textbox", "Name"))
    s.step("controls:type", "type", " Jr")
    s.step("controls:click-go", "click", T("button", "Go"))
    s.step("controls:click-go-right", "click", T("button", "Go"), "right")
    s.step("controls:click-go-mod", "click", T("button", "Go"), "--modifiers=Shift")
    s.step("controls:dblclick", "dblclick", T("button", "Double me"))
    s.step("controls:hover", "hover", T("button", "Hover me"))
    s.step("controls:check", "check", T("checkbox", "I agree"))
    s.step("controls:uncheck", "uncheck", T("checkbox", "I agree"))
    s.step("controls:check-radio", "check", T("radio", "Large"))
    s.step("controls:select", "select", T("combobox", "Color"), "blue")
    s.step("controls:select-label", "select", T("combobox", "Color"), "Green")
    s.step("controls:upload", "upload", str(upload_file), T("button", "Avatar"))
    s.step("controls:upload-default", "upload", str(upload_file))
    s.step("controls:click-save-1", "click", T("button", "Save", 1))
    s.step("controls:click-save-2", "click", T("button", "Save", 2))
    s.step("controls:click-more-1", "click", T("link", "More", 1))
    s.step("controls:click-anchor", "click", T("link", "Jump to two"))
    s.step("controls:click-star", "click", T("button", "\u2605"))
    s.step("controls:click-quoted", "click", T("button", 'Say "hi"'))
    s.step("controls:drag", "drag", T("button", "Drag source"), T("region", "Drop target"))
    s.step("controls:drop-data", "drop", T("region", "Drop target"), "--data=text/plain=hello")
    s.step("controls:drop-file", "drop", T("region", "Drop target"), f"--path={upload_file}")
    s.step("controls:highlight", "highlight", T("button", "Go"), state=False)
    s.step("controls:highlight-style", "highlight", T("button", "Hover me"), "--style=outline: 2px solid red", state=False)
    s.step("controls:highlight-hide-ref", "highlight", T("button", "Go"), "--hide", state=False)
    s.step("controls:highlight-hide-all", "highlight", "--hide", state=False)
    s.step("controls:screenshot-ref", "screenshot", T("button", "Go"), state=False)
    s.step("controls:eval-ref", "eval", "el => el.id", T("button", "Save", 2), state=False)
    s.step("controls:eval-ref-tag", "eval", "el => el.tagName + ':' + el.textContent", T("link", "More"), state=False)
    s.step("controls:text-ref", "text", T("button", 'Say "hi"'), state=False)
    s.step("controls:text-selector", "text", "#para", state=False)
    s.step("controls:scroll-to", "scroll-to", T("region", "Drop target"), state=False)
    s.step("controls:wait-for", "wait-for", T("button", "Go"), state=False)
    for i, t in enumerate(
        [
            T("button", "Go"),
            T("button", "Save", 2),
            T("button", 'Say "hi"'),
            T("link", "More", 1),
            T("button", "\u2605"),
            T("checkbox", "I agree"),
            T("textbox", "Name"),
        ]
    ):
        s.step(f"controls:generate-locator-{i}", "generate-locator", t, state=False)
    s.step("controls:find-save", "find", "Save", state=False)
    s.step("controls:find-limit", "find", "Save", "--limit=2", state=False)
    s.step("controls:find-regex", "find", "--regex=/^(Go|Save)$/", state=False)
    s.step("controls:find-regex-flag", "find", "dr.g", "--regex", state=False)
    s.step("controls:find-all", "find", "paragraph", "--all", state=False)
    s.step("controls:find-none", "find", "zzz-nothing", state=False)
    s.step("controls:find-role-save", "find", "--role=button", "--name=Save", state=False)
    s.step("controls:find-role-regex", "find", "--role=button", "--name=/sav/i", "--limit=2", state=False)
    s.step("controls:find-role-link", "find", "--role=link", state=False)
    s.step("controls:find-role-exact", "find", "--role=textbox", "--name=Name", "--exact", state=False)
    s.step("controls:find-role-none", "find", "--role=slider", state=False)
    s.step("controls:find-role-then-click", "click", T("button", "Save", 1))
    s.step("controls:html-ref", "html", T("button", "Go"), state=False)
    s.step("controls:html-ref-list", "html", T("list"), state=False)
    s.step("controls:html-selector", "html", "--selector=#go", state=False)
    s.step("controls:html-bad-ref", "html", "e99999", state=False)
    s.step("controls:find-then-click", "click", T("button", "Save", 0))
    s.step("controls:snapshot-pre-pick", "snapshot", state=False)
    s.pick("controls:pick-save2", "#save2")
    s.pick("controls:pick-name", "#name")
    s.pick("controls:pick-dup-1", "section[aria-label=Pick] button:nth-of-type(2)")
    s.pick("controls:pick-dup-0", "section[aria-label=Pick] button:nth-of-type(1)")
    s.step("controls:bad-ref-num", "click", "e99999")
    s.step("controls:bad-ref-word", "click", "nonsense")
    s.step("controls:bad-ref-at", "click", "@e1", state=False)
    s.step("controls:generate-locator-bad", "generate-locator", "e99999", state=False)
    s.step("controls:highlight-bad", "highlight", "e99999", state=False)

    # -- nested lists --------------------------------------------------------
    s.open("nested")
    s.step("nested:snapshot", "snapshot", keep_snapshot=True)
    s.step("nested:snapshot-depth1", "snapshot", "--depth=1", keep_snapshot=True)
    s.step("nested:snapshot-depth3-i", "snapshot", "--depth=3", "-i", keep_snapshot=True)
    s.step("nested:snapshot", "snapshot", keep_snapshot=False, state=False)
    s.step("nested:click-green-1", "click", T("link", "Green", 1))
    s.step("nested:find-green", "find", "Green", state=False)
    s.step("nested:generate-locator-green-1", "generate-locator", T("link", "Green", 1), state=False)
    s.step("nested:snapshot-sub", "snapshot", T("listitem", None, 1), keep_snapshot=True, state=False)
    s.step("nested:click-green-0-after-sub", "click", T("link", "Green", 0))

    # -- same-origin iframe ---------------------------------------------------
    s.open("iframe")
    s.step("iframe:wait", "wait", "500", state=False)
    s.step("iframe:snapshot", "snapshot", keep_snapshot=True)
    s.step("iframe:snapshot-i", "snapshot", "-i", keep_snapshot=True)
    s.step("iframe:snapshot", "snapshot", keep_snapshot=False, state=False)
    s.step("iframe:click-outer", "click", T("button", "Submit", 0))
    s.step("iframe:click-inner", "click", T("button", "Submit", 1))
    s.step("iframe:fill-inner", "fill", T("textbox", "Inner field"), "deep")
    s.step("iframe:check-inner", "check", T("checkbox", "Inner check"))
    s.step("iframe:eval-inner", "eval", "el => el.id", T("textbox", "Inner field"), state=False)
    s.step("iframe:text-inner", "text", T("button", "Submit", 1), state=False)
    s.step("iframe:highlight-inner", "highlight", T("button", "Submit", 1), state=False)
    s.step("iframe:screenshot-inner", "screenshot", T("button", "Submit", 1), state=False)
    s.step("iframe:generate-locator-inner", "generate-locator", T("button", "Submit", 1), state=False)
    s.step("iframe:find-submit", "find", "Submit", state=False)
    s.step("iframe:find-role-submit", "find", "--role=button", "--name=Submit", state=False)
    s.step("iframe:html-inner", "html", T("button", "Submit", 1), state=False)
    s.step("iframe:snapshot-pre-pick", "snapshot", state=False)
    s.pick("iframe:pick-outer", "#outer-submit")
    s.step("iframe:snapshot-iframe-ref", "snapshot", T("iframe"), keep_snapshot=True, state=False)

    # -- open shadow DOM ---------------------------------------------------------
    s.open("shadow")
    s.step("shadow:snapshot", "snapshot", keep_snapshot=True)
    s.step("shadow:fill", "fill", T("textbox", "Shadow field"), "ghost")
    s.step("shadow:click", "click", T("button", "Shadow button"))
    s.step("shadow:click-deep", "click", T("button", "Deep button"))
    s.step("shadow:find-deep", "find", "Deep", state=False)
    s.step("shadow:generate-locator-deep", "generate-locator", T("button", "Deep button"), state=False)
    s.step("shadow:eval-deep", "eval", "el => el.id", T("button", "Deep button"), state=False)
    s.step("shadow:find-role", "find", "--role=button", state=False)
    s.step("shadow:html-deep", "html", T("button", "Deep button"), state=False)
    s.step("shadow:html-shadow", "html", "--selector=#host", "--include-shadow", state=False)

    # -- modal dialog -------------------------------------------------------------
    s.open("dialog")
    s.step("dialog:snapshot-closed", "snapshot", keep_snapshot=True)
    s.step("dialog:open", "click", T("button", "Open dialog"))
    s.step("dialog:snapshot-open", "snapshot", keep_snapshot=True)
    s.step("dialog:fill", "fill", T("textbox", "Reason"), "because")
    s.step("dialog:click-behind", "click", T("button", "Open dialog"), "--scroll=none", timeout=60)
    s.step("dialog:confirm", "click", T("button", "Confirm"))
    s.step("dialog:snapshot-after", "snapshot", keep_snapshot=True)

    # -- element that appears later, and one that disappears ---------------------
    s.open("delayed")
    s.step("delayed:snapshot-early", "snapshot", keep_snapshot=True)
    s.step("delayed:find-late-early", "find", "Late", state=False)
    s.step("delayed:wait", "wait", "2000", state=False)
    s.step("delayed:snapshot-late", "snapshot", keep_snapshot=True)
    s.step("delayed:click-late", "click", T("button", "Late button"))
    s.step("delayed:wait-for-hidden", "wait-for", T("button", "Vanishing"), "--state=hidden", state=False)
    s.step("delayed:snapshot-gone", "snapshot", keep_snapshot=True)

    # -- off-screen element ---------------------------------------------------------
    s.open("offscreen")
    s.step("offscreen:snapshot", "snapshot", keep_snapshot=True)
    s.step("offscreen:snapshot-boxes", "snapshot", "--boxes", keep_snapshot=True)
    s.step("offscreen:click-none", "click", T("button", "Far button"), "--scroll=none", timeout=60)
    s.step("offscreen:hover-none", "hover", T("button", "Far button"), "--scroll=none", timeout=60)
    s.step("offscreen:check-none", "check", T("checkbox", "Far check"), "--scroll=none", timeout=60)
    s.step("offscreen:click", "click", T("button", "Far button"))
    s.step("offscreen:screenshot", "screenshot", T("checkbox", "Far check"), state=False)
    s.step("offscreen:scroll-to", "scroll-to", T("heading", "Offscreen"), state=False)
    s.step("offscreen:snapshot-boxes-2", "snapshot", "--boxes", keep_snapshot=True)

    # -- large page ------------------------------------------------------------------
    s.open("large")
    s.step("large:snapshot", "snapshot", keep_snapshot=True, state=False)
    s.step("large:snapshot-i", "snapshot", "-i", keep_snapshot=True, state=False)
    s.step("large:snapshot-boxes", "snapshot", "--boxes", keep_snapshot=True, state=False)
    s.step("large:snapshot-plain", "snapshot", keep_snapshot=False, state=False)
    s.step("large:find-buy1", "find", "Buy 1", state=False)
    s.step("large:find-buy1-limit5", "find", "Buy 1", "--limit=5", state=False)
    s.step("large:find-regex", "find", "--regex=^Item 14\\d$", state=False)
    s.step("large:click-150", "click", T("button", "Buy 150"))
    s.step("large:generate-locator-150", "generate-locator", T("button", "Buy 150"), state=False)
    s.step("large:click-link-77", "click", T("link", "Item 77"))

    # -- refs going stale ---------------------------------------------------------
    s.open("nav")
    s.step("nav:snapshot", "snapshot", keep_snapshot=True)
    s.note_ref("item-a", Target("button", "Item A"))
    s.note_ref("stay", Target("button", "Stay"))
    s.step("nav:dom-rerender", "eval", "document.getElementById('rerender').click()", state=True)
    s.stale_step("nav:stale-after-rerender-click", "click", "item-a")
    s.step("nav:snapshot-2", "snapshot", keep_snapshot=True, state=False)
    s.note_ref("item-a", Target("button", "Item A"))
    s.note_ref("stay", Target("button", "Stay"))
    s.step("nav:click-rerender", "click", T("button", "Rerender"))
    s.stale_step("nav:stale-after-cli-click", "click", "item-a")
    s.step("nav:snapshot-3", "snapshot", keep_snapshot=False, state=False)
    s.note_ref("stay", Target("button", "Stay"))
    s.note_ref("link", Target("link", "Go to page two"))
    s.step("nav:js-navigate", "eval", "location.href = 'nav2.html'", state=False)
    s.step("nav:wait", "wait", "800", state=False)
    s.stale_step("nav:stale-after-js-nav-click", "click", "stay")
    s.stale_step("nav:stale-after-js-nav-gen", "generate-locator", "stay")
    s.stale_step("nav:stale-after-js-nav-html", "html", "stay")
    s.stale_step("nav:stale-after-js-nav-link", "click", "link")
    s.step("nav:snapshot-4", "snapshot", keep_snapshot=False, state=False)
    s.note_ref("other", Target("button", "Other"))
    s.step("nav:cli-goto", "goto", f"{s.base}/nav.html")
    s.stale_step("nav:stale-after-goto", "click", "other")
    s.step("nav:go-back", "go-back")
    s.step("nav:snapshot-5", "snapshot", keep_snapshot=False, state=False)
    s.step("nav:click-after-back", "click", T("button", "Other"))


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass


def cmd_run(args) -> int:
    handler = functools.partial(_QuietHandler, directory=str(FIXTURES))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    with tempfile.TemporaryDirectory(prefix="snaprefs-sweep-") as tmp:
        workdir = Path(tmp)
        upload_file = workdir / "upload.txt"
        upload_file.write_text("sweep upload\n", encoding="utf-8")
        sweep = Sweep(port=args.port, workdir=workdir, base=base)
        started = time.perf_counter()
        try:
            run_steps(sweep, upload_file)
        finally:
            sweep.cli("kill-all")
            server.shutdown()
        took = round(time.perf_counter() - started)

    report = {"port": args.port, "seconds": took, "steps": sweep.steps}
    Path(args.out).write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    failed = sum(1 for st in sweep.steps if st["exit"] != 0)
    print(f"{len(sweep.steps)} steps, {failed} non-zero exits, {took}s -> {args.out}")
    return 0


COMPARED = ("exit", "out", "state", "snapshot", "missing_targets")


def _key_steps(steps: list[dict]) -> dict[str, dict]:
    keyed: dict[str, dict] = {}
    for st in steps:
        key, n = st["id"], 2
        while key in keyed:
            key, n = f"{st['id']}#{n}", n + 1
        keyed[key] = st
    return keyed


def cmd_diff(args) -> int:
    import difflib

    before = _key_steps(json.loads(Path(args.before).read_text(encoding="utf-8"))["steps"])
    after = _key_steps(json.loads(Path(args.after).read_text(encoding="utf-8"))["steps"])
    changed = 0
    for key in list(before) + [k for k in after if k not in before]:
        b, a = before.get(key), after.get(key)
        if b is None or a is None:
            changed += 1
            print(f"## {key}: only in {'after' if b is None else 'before'}")
            continue
        fields = [f for f in COMPARED if b.get(f) != a.get(f)]
        if not fields:
            continue
        changed += 1
        print(f"## {key}  ({' '.join(b['argv'])})  refs {b.get('refs')} -> {a.get('refs')}")
        for f in fields:
            old, new = b.get(f), a.get(f)
            if f in ("exit", "missing_targets"):
                print(f"  {f}: {old!r} -> {new!r}")
                continue
            old_s = old if isinstance(old, str) else json.dumps(old, indent=1, ensure_ascii=False)
            new_s = new if isinstance(new, str) else json.dumps(new, indent=1, ensure_ascii=False)
            print(f"  {f}:")
            for line in difflib.unified_diff(
                (old_s or "").splitlines(), (new_s or "").splitlines(), "before", "after", lineterm="", n=1
            ):
                print("    " + line)
    same = len([k for k in before if k in after]) - changed
    print(f"\n{len(before)} before / {len(after)} after steps; {changed} differ, {same} identical")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--port", type=int, required=True)
    run.add_argument("--out", required=True)
    diff = sub.add_parser("diff")
    diff.add_argument("before")
    diff.add_argument("after")
    args = parser.parse_args()
    return cmd_run(args) if args.cmd == "run" else cmd_diff(args)


if __name__ == "__main__":
    sys.exit(main())
