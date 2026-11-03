"""End-to-end tests exercising real daemon + browser."""

import json
import os
import re
import socket
import struct
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from patchright_cli.daemon import ensure_daemon_running, read_daemon_token

FIXTURE_URL = "file://" + os.path.join(os.path.dirname(__file__), "fixture.html")
WEBMCP_FIXTURE_URL = (Path(__file__).parent / "webmcp_fixture.html").resolve().as_uri()
DETECT_FIXTURE_URL = (Path(__file__).parent / "detect_fixture.html").resolve().as_uri()
# Overridable so parallel runs (one per worktree) do not share a daemon.
TEST_PORT = int(os.environ.get("PATCHRIGHT_CLI_E2E_PORT", "19321"))


def _send_tcp(port: int, cmd: str, args=None, options=None) -> dict:
    msg = {"command": cmd, "args": args or [], "options": options or {}, "token": read_daemon_token(port)}
    data = json.dumps(msg).encode("utf-8")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(30)
    sock.connect(("127.0.0.1", port))
    sock.sendall(struct.pack("!I", len(data)) + data)
    header = sock.recv(4)
    length = struct.unpack("!I", header)[0]
    resp = b""
    while len(resp) < length:
        chunk = sock.recv(length - len(resp))
        if not chunk:
            break
        resp += chunk
    sock.close()
    return json.loads(resp.decode("utf-8"))


@pytest.fixture(scope="module")
def daemon():
    # Ensure port is free
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        s.connect(("127.0.0.1", TEST_PORT))
        s.close()
        pytest.skip(f"Port {TEST_PORT} already in use")
    except (ConnectionRefusedError, OSError, TimeoutError):
        pass

    ensure_daemon_running(port=TEST_PORT, headless=True)

    # Open fixture
    profile = {} if TEST_PORT == 19321 else {"profile": f"e2e-{TEST_PORT}"}
    resp = _send_tcp(TEST_PORT, "open", args=[FIXTURE_URL], options=profile)
    assert resp["success"] is True

    yield TEST_PORT

    # Teardown
    try:
        _send_tcp(TEST_PORT, "kill-all")
    except Exception:
        pass
    time.sleep(0.5)


def _cmd(port: int, action: str, args=None, options=None) -> dict:
    return _send_tcp(port, action, args, options)


def _read_snapshot(port: int) -> str:
    resp = _cmd(port, "snapshot")
    assert resp["success"] is True
    path = resp["snapshot_path"]
    return Path(path).read_text(encoding="utf-8")


def _find_ref(snapshot_text: str, role: str) -> str:
    for line in snapshot_text.splitlines():
        if f"- {role}" in line and "[ref=" in line:
            start = line.index("[ref=") + 5
            end = line.index("]", start)
            return line[start:end]
    raise ValueError(f"No ref found for role '{role}' in snapshot")


@pytest.mark.e2e
class TestE2E:
    def test_open_returns_url_and_title(self, daemon):
        resp = _cmd(daemon, "url")
        assert resp["success"] is True
        assert "fixture.html" in resp["output"]

        resp = _cmd(daemon, "title")
        assert resp["success"] is True
        assert resp["output"] == "Test Fixture"

    def test_snapshot_has_refs(self, daemon):
        snap = _read_snapshot(daemon)
        # Playwright's own refs: eN on a first document, fKeN after a navigation or in a frame.
        assert re.search(r"\[ref=(f\d+)?e\d+\]", snap)

    def test_fill_textbox(self, daemon):
        snap = _read_snapshot(daemon)
        ref = _find_ref(snap, "textbox")
        resp = _cmd(daemon, "fill", args=[ref, "E2E-Alice"])
        assert resp["success"] is True
        resp = _cmd(daemon, "eval", args=["document.getElementById('name').value"])
        assert '"E2E-Alice"' in resp["output"]

    def test_click_button(self, daemon):
        snap = _read_snapshot(daemon)
        ref = _find_ref(snap, "button")
        resp = _cmd(daemon, "click", args=[ref])
        assert resp["success"] is True
        resp = _cmd(daemon, "eval", args=["document.getElementById('output').textContent"])
        assert '"clicked"' in resp["output"]

    def test_select_dropdown(self, daemon):
        snap = _read_snapshot(daemon)
        ref = _find_ref(snap, "combobox")
        resp = _cmd(daemon, "select", args=[ref, "Green"])
        assert resp["success"] is True
        resp = _cmd(daemon, "eval", args=["document.getElementById('color').value"])
        assert '"green"' in resp["output"]

    def test_check_uncheck(self, daemon):
        snap = _read_snapshot(daemon)
        ref = _find_ref(snap, "checkbox")
        resp = _cmd(daemon, "check", args=[ref])
        assert resp["success"] is True
        resp = _cmd(daemon, "eval", args=["document.getElementById('agree').checked"])
        assert "true" in resp["output"].lower()

    def test_scroll(self, daemon):
        resp = _cmd(daemon, "scroll", args=["0", "100"])
        assert resp["success"] is True

    def test_wait(self, daemon):
        resp = _cmd(daemon, "wait", args=["50"])
        assert resp["success"] is True

    def test_wait_for_element(self, daemon):
        snap = _read_snapshot(daemon)
        ref = _find_ref(snap, "button")
        resp = _cmd(daemon, "wait-for", args=[ref])
        assert resp["success"] is True

    # `find` tests run last: the first one clicks the fixture button, which sets
    # #output to "clicked". test_click_button above asserts that same transition,
    # so these must not run before it.

    def test_find_returns_actionable_ref(self, daemon):
        goto = _cmd(daemon, "goto", args=[FIXTURE_URL])
        assert goto["success"] is True

        resp = _cmd(daemon, "find", args=["Click me"])
        assert resp["success"] is True
        assert 'button "Click me"' in resp["output"]

        match = re.search(r"\[ref=((?:f\d+)?e\d+)\]", resp["output"])
        assert match is not None, f"No ref found in find output: {resp['output']!r}"

        resp = _cmd(daemon, "click", args=[match.group(1)])
        assert resp["success"] is True
        resp = _cmd(daemon, "text", args=["#output"])
        assert "clicked" in resp["output"]

    def test_find_no_matches_is_not_an_error(self, daemon):
        goto = _cmd(daemon, "goto", args=[FIXTURE_URL])
        assert goto["success"] is True

        resp = _cmd(daemon, "find", args=["nonexistent-element"])
        assert resp["success"] is True
        assert "0 matches" in resp["output"]

    def test_snapshot_scoped_by_css_selector(self, daemon):
        goto = _cmd(daemon, "goto", args=[FIXTURE_URL])
        assert goto["success"] is True

        full = _read_snapshot(daemon)
        resp = _cmd(daemon, "snapshot", options={"selector": "#color"})
        assert resp["success"] is True
        scoped = Path(resp["snapshot_path"]).read_text(encoding="utf-8")

        assert "combobox" in scoped
        assert "heading" not in scoped
        assert len(scoped) < len(full)

    def test_snapshot_selector_without_a_match_fails(self, daemon):
        resp = _cmd(daemon, "snapshot", options={"selector": "#not-on-this-page"})
        assert resp["success"] is False
        assert "#not-on-this-page" in resp["output"]

    def test_wait_for_url_pattern_matches_current_page(self, daemon):
        goto = _cmd(daemon, "goto", args=[FIXTURE_URL])
        assert goto["success"] is True

        resp = _cmd(daemon, "wait", options={"url": "**/fixture.html"})
        assert resp["success"] is True

    def test_highlight_shows_and_hides_the_native_overlay(self, daemon):
        goto = _cmd(daemon, "goto", args=[FIXTURE_URL])
        assert goto["success"] is True
        ref = _find_ref(_read_snapshot(daemon), "button")

        shown = _cmd(daemon, "highlight", args=[ref], options={"style": "outline: 3px dashed blue"})
        assert shown == {"success": True, "output": f"Highlighted {ref}."}
        # Playwright draws the overlay inside its own glass-pane element.
        glass = _cmd(daemon, "eval", args=["document.querySelectorAll('x-pw-glass').length"])
        assert glass["output"] == "1"

        hidden = _cmd(daemon, "highlight", args=[ref], options={"hide": True})
        assert hidden["output"] == f"Hid highlight on {ref}."
        again = _cmd(daemon, "highlight", args=[ref], options={"hide": True})
        assert "(no-op)" in again["output"]

    def test_scroll_none_refuses_an_element_outside_the_viewport(self, daemon):
        page = "data:text/html,<div style='height:3000px'></div><button>Far</button>"
        assert _cmd(daemon, "goto", args=[page])["success"] is True
        ref = _find_ref(_read_snapshot(daemon), "button")

        refused = _cmd(daemon, "click", args=[ref], options={"scroll": "none", "timeout-action": "1500"})
        assert refused["success"] is False
        assert "outside of the viewport" in refused["output"]

        clicked = _cmd(daemon, "click", args=[ref], options={"scroll": "auto", "timeout-action": "30000"})
        assert clicked["success"] is True

    def test_console_messages_carry_the_page_timestamp(self, daemon):
        assert _cmd(daemon, "goto", args=[FIXTURE_URL])["success"] is True
        assert _cmd(daemon, "console", options={"clear": True})["success"] is True
        logged = _cmd(daemon, "eval", args=["(() => { const t = Date.now(); console.log('stamp'); return t; })()"])
        page_ms = float(logged["output"])

        resp = _cmd(daemon, "console")
        stamp = next(m for m in resp["data"] if m["text"] == "stamp")
        assert abs(stamp["timestamp"] - page_ms) < 1000

    def test_request_shows_the_http_version(self, daemon):
        import http.server
        import threading

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), http.server.SimpleHTTPRequestHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/"
            assert _cmd(daemon, "goto", args=[url])["success"] is True
            listing = _cmd(daemon, "network")
            entry = next(r for r in reversed(listing["data"]) if r["url"] == url)
            detail = _cmd(daemon, "request", args=[str(entry["id"])])
            assert re.search(r"^- HTTP version: http/1\.[01]$", detail["output"], re.MULTILINE | re.IGNORECASE)
        finally:
            server.shutdown()

    # Tab tests run last: they add pages to the shared session, and the
    # index-based tests above assume only the fixture page is open.

    def test_named_tabs_keep_independent_refs(self, daemon):
        goto = _cmd(daemon, "goto", args=[FIXTURE_URL])
        assert goto["success"] is True

        opened = _cmd(daemon, "open", args=["data:text/html,<button>Archive</button>"], options={"tab": "inbox"})
        assert opened["success"] is True

        inbox = _cmd(daemon, "find", args=["Archive"], options={"tab": "inbox"})
        assert inbox["success"] is True
        assert 'button "Archive"' in inbox["output"]

        # The default tab still sees the fixture, not the tab's page.
        default = _cmd(daemon, "find", args=["Click me"])
        assert default["success"] is True
        assert 'button "Click me"' in default["output"]

        listing = _cmd(daemon, "tab-list")
        assert "(inbox)" in listing["output"]

    def test_command_for_an_unopened_tab_fails(self, daemon):
        resp = _cmd(daemon, "url", options={"tab": "never-opened"})
        assert resp["success"] is False
        assert "never-opened" in resp["output"]

    def test_closing_a_named_tab_leaves_the_session_open(self, daemon):
        # Open the tab this test closes rather than relying on a sibling test
        # having created it -- that made the test order load-bearing.
        opened = _cmd(daemon, "open", args=["data:text/html,<p>scratch</p>"], options={"tab": "scratch"})
        assert opened["success"] is True

        resp = _cmd(daemon, "close", options={"tab": "scratch"})
        assert resp["success"] is True
        assert "still open" in resp["output"]

        still_there = _cmd(daemon, "url")
        assert still_there["success"] is True


_DROP_PAGE = (
    "data:text/html,<div id=zone role=region aria-label=dropzone style='width:200px;height:100px'>drop</div>"
    "<div id=nodrop role=region aria-label=nodrop style='width:200px;height:50px'>no</div>"
    "<div id=out>none</div><script>"
    "zone.addEventListener('dragover',e=>e.preventDefault());"
    "zone.addEventListener('drop',async e=>{e.preventDefault();const dt=e.dataTransfer,s=[];"
    "for(const f of dt.files)s.push('file:'+f.name+':'+f.type+':'+(await f.text()));"
    "for(const t of dt.types)if(t!=='Files')s.push(t+'='+dt.getData(t));out.textContent=s.join('|')});"
    "</script>"
)


@pytest.fixture(scope="module")
def http_origin(tmp_path_factory):
    """A real http:// origin (localStorage and storage state need one; data: URLs are opaque)."""
    import http.server
    import threading
    from functools import partial

    root = tmp_path_factory.mktemp("site")
    (root / "page.html").write_text("<title>storage</title><p>storage page</p>", encoding="utf-8")
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


PASSKEY_PAGE = r"""<!doctype html><html><body>
<button onclick="run()">Register then sign in</button><pre id="out">idle</pre>
<script>
const b64u = b => btoa(String.fromCharCode(...new Uint8Array(b)))
  .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
async function run() {
  const out = document.getElementById('out');
  try {
    const c = await navigator.credentials.create({publicKey: {challenge: new Uint8Array(32),
      rp: {name: 'Test', id: 'localhost'},
      user: {id: new TextEncoder().encode('alice'), name: 'alice', displayName: 'Alice'},
      pubKeyCredParams: [{type: 'public-key', alg: -7}], authenticatorSelection: {residentKey: 'required'}}});
    const a = await navigator.credentials.get({publicKey: {challenge: new Uint8Array(32), rpId: 'localhost'}});
    out.textContent = 'created=' + b64u(c.rawId) + ' signed=' + b64u(a.rawId);
  } catch (e) { out.textContent = 'ERR ' + e.name + ': ' + e.message; }
}
</script></body></html>"""


@pytest.fixture(scope="module")
def passkey_site(tmp_path_factory):
    """WebAuthn needs a secure context with a real rp id, so serve the page from http://localhost."""
    root = tmp_path_factory.mktemp("passkey-site")
    (root / "passkey.html").write_text(PASSKEY_PAGE, encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://localhost:{server.server_port}/passkey.html"
    server.shutdown()


@pytest.mark.e2e
class TestNativeStorageAndDrop:
    def test_drop_files_and_data(self, daemon, tmp_path):
        tab = {"tab": "drop"}
        assert _cmd(daemon, "open", args=[_DROP_PAGE], options=tab)["success"] is True
        snap = _cmd(daemon, "snapshot", options=tab)
        zone = re.search(r'region "dropzone".*?\[ref=((?:f\d+)?e\d+)\]', Path(snap["snapshot_path"]).read_text("utf-8"))
        assert zone
        note = tmp_path / "note.txt"
        note.write_text("hello file", encoding="utf-8")

        resp = _cmd(
            daemon,
            "drop",
            args=[zone.group(1)],
            options={**tab, "path": str(note), "data": "text/uri-list=https://x.test/"},
        )
        assert resp["success"] is True, resp
        out = _cmd(daemon, "eval", args=["out.textContent"], options=tab)
        assert json.loads(out["output"]) == "file:note.txt:text/plain:hello file|text/uri-list=https://x.test/"
        _cmd(daemon, "close", options=tab)

    def test_web_storage_commands(self, daemon, http_origin):
        tab = {"tab": "storage"}
        assert _cmd(daemon, "open", args=[f"{http_origin}/page.html"], options=tab)["success"] is True
        for prefix, store in (("localstorage", "localStorage"), ("sessionstorage", "sessionStorage")):
            _cmd(daemon, f"{prefix}-clear", options=tab)
            empty = _cmd(daemon, f"{prefix}-list", options=tab)
            assert empty["output"] == f"0 {store} keys on this page"
            assert _cmd(daemon, f"{prefix}-set", args=["k1", "v1"], options=tab)["output"] == f"{store}['k1'] set."
            _cmd(daemon, f"{prefix}-set", args=["e", ""], options=tab)
            listed = _cmd(daemon, f"{prefix}-list", options=tab)
            raw = _cmd(daemon, "eval", args=[f"JSON.stringify({store})"], options=tab)
            assert listed["output"] == json.loads(raw["output"])
            assert _cmd(daemon, f"{prefix}-get", args=["k1"], options=tab)["output"] == '"v1"'
            assert _cmd(daemon, f"{prefix}-get", args=["nope"], options=tab)["output"] == "null"
            assert _cmd(daemon, f"{prefix}-delete", args=["e"], options=tab)["output"] == f"{store}['e'] deleted."
            assert (
                _cmd(daemon, f"{prefix}-delete", args=["e"], options=tab)["output"] == f"{store} has no key 'e' (no-op)"
            )
            assert _cmd(daemon, f"{prefix}-clear", options=tab)["output"] == f"{store} cleared."
        _cmd(daemon, "close", options=tab)

    def test_state_save_then_load_round_trips(self, daemon, http_origin, tmp_path):
        tab = {"tab": "state"}
        assert _cmd(daemon, "open", args=[f"{http_origin}/page.html"], options=tab)["success"] is True
        _cmd(daemon, "localstorage-set", args=["tok", "abc"], options=tab)
        state_file = tmp_path / "state.json"
        assert _cmd(daemon, "state-save", args=[str(state_file)], options=tab)["success"] is True
        saved = json.loads(state_file.read_text(encoding="utf-8"))
        # Another origin in the file: state-load applies it too, without visiting it first.
        other = http_origin.replace("127.0.0.1", "localhost")
        saved["origins"].append({"origin": other, "localStorage": [{"name": "other", "value": "o"}]})
        state_file.write_text(json.dumps(saved), encoding="utf-8")

        _cmd(daemon, "localstorage-clear", options=tab)
        resp = _cmd(daemon, "state-load", args=[str(state_file)], options=tab)
        assert resp == {"success": True, "output": f"State loaded from {state_file}"}
        assert _cmd(daemon, "localstorage-get", args=["tok"], options=tab)["output"] == '"abc"'
        _cmd(daemon, "goto", args=[f"{other}/page.html"], options=tab)
        assert _cmd(daemon, "localstorage-get", args=["other"], options=tab)["output"] == '"o"'
        _cmd(daemon, "close", options=tab)


@pytest.mark.e2e
class TestPickLocator:
    """A click in the page ends `pick-locator` with that element's locator."""

    def _pick_while(self, port: int, act, options=None) -> dict:
        import threading

        result = {}
        thread = threading.Thread(
            target=lambda: result.update(
                _cmd(port, "pick-locator", options=options or {"wait": "20", "detectable": True})
            )
        )
        thread.start()
        time.sleep(1.5)
        act()
        thread.join(25)
        assert not thread.is_alive()
        return result

    def test_click_returns_locator_and_ref(self, daemon):
        _read_snapshot(daemon)  # gives the pick a snapshot to match a ref in
        box = _cmd(
            daemon,
            "eval",
            args=[
                "(() => { const r = document.getElementById('btn').getBoundingClientRect();"
                " return `at ${Math.round(r.x + r.width / 2)},${Math.round(r.y + r.height / 2)}`; })()"
            ],
        )
        x, y = re.search(r"at (\d+),(\d+)", box["output"]).groups()

        def click():
            _cmd(daemon, "mousemove", args=[x, y])
            _cmd(daemon, "mousedown")
            _cmd(daemon, "mouseup")

        resp = self._pick_while(daemon, click)
        assert resp["success"] is True, resp
        assert resp["data"]["locator"] == 'internal:role=button[name="Click me"i]'
        assert resp["data"]["ref"] == _find_ref(_read_snapshot(daemon), 'button "Click me"')

    def test_cancel_ends_the_pick(self, daemon):
        resp = self._pick_while(daemon, lambda: _cmd(daemon, "pick-locator", options={"cancel": True}))
        assert resp["success"] is False
        assert "cancelled" in resp["output"]

    def test_wait_expiry_cancels_pick_mode(self, daemon):
        resp = _cmd(daemon, "pick-locator", options={"wait": "1", "detectable": True})
        assert resp["success"] is False
        assert "No element was picked within 1s" in resp["output"]
        again = _cmd(daemon, "pick-locator", options={"cancel": True})
        assert "no-op" in again["output"]


class TestPasskeysE2E:
    def test_page_ceremony_uses_the_virtual_authenticator(self, daemon, passkey_site, tmp_path):
        opts = {"session": f"e2e-passkeys-{daemon}"}
        try:
            assert _cmd(daemon, "open", args=[passkey_site], options=opts)["success"] is True
            # Installed after the page loaded: the override still reaches the open page.
            first = _cmd(daemon, "passkey-list", options=opts)
            assert first["output"].startswith("Virtual passkey authenticator installed")
            assert first["data"] == []

            snap = _cmd(daemon, "snapshot", options=opts)
            ref = _find_ref(Path(snap["snapshot_path"]).read_text(encoding="utf-8"), "button")
            assert _cmd(daemon, "click", args=[ref], options=opts)["success"] is True
            out = ""
            for _ in range(20):
                out = _cmd(daemon, "eval", args=["document.getElementById('out').textContent"], options=opts)["output"]
                if "idle" not in out:
                    break
                time.sleep(0.25)
            created = re.search(r"created=([\w-]+) signed=([\w-]+)", out)
            assert created, out
            assert created.group(1) == created.group(2)

            listed = _cmd(daemon, "passkey-list", options=opts)
            assert listed["data"] == [
                {"id": created.group(1), "rpId": "localhost", "userHandle": "YWxpY2U", "signCount": 1}
            ]

            # Export, clear, import: the credential comes back with its keys.
            exported = tmp_path / "passkeys.json"
            assert _cmd(daemon, "passkey-export", args=[str(exported)], options=opts)["success"] is True
            assert json.loads(exported.read_text(encoding="utf-8"))[0]["privateKey"]
            assert _cmd(daemon, "passkey-clear", options=opts)["output"] == "Deleted 1 passkey(s)."
            assert _cmd(daemon, "passkey-list", options=opts)["data"] == []
            assert _cmd(daemon, "passkey-import", args=[str(exported)], options=opts)["success"] is True
            assert [c["id"] for c in _cmd(daemon, "passkey-list", options=opts)["data"]] == [created.group(1)]
        finally:
            _cmd(daemon, "close", options=opts)


@pytest.mark.e2e
class TestWebMCPE2E:
    """`open --webmcp` on a page that registers tools through `document.modelContext`."""

    SESSION = "webmcp-e2e"

    def _cmd(self, port, action, args=None, **options):
        return _cmd(port, action, args, {"session": self.SESSION, **options})

    def test_without_webmcp_the_tools_are_refused(self, daemon):
        resp = _cmd(daemon, "webmcp-list")
        assert resp["success"] is False
        assert "open --webmcp" in resp["output"]

    def test_list_and_call(self, daemon):
        opened = self._cmd(daemon, "open", [WEBMCP_FIXTURE_URL], webmcp=True)
        assert opened["success"] is True, opened
        try:
            listing = self._cmd(daemon, "webmcp-list")
            assert listing["success"] is True, listing
            assert [t["name"] for t in listing["data"]] == ["add", "refuse", "set_greeting"]
            assert listing["data"][0]["annotations"] == {"readOnly": True}
            assert "- add [readOnly]: Add two numbers" in listing["output"]

            called = self._cmd(daemon, "webmcp-call", ["add", '{"a": 2, "b": 40}'])
            assert called["success"] is True, called
            assert called["data"] == {"content": [{"type": "text", "text": "42"}]}

            greeted = self._cmd(daemon, "webmcp-call", ["set_greeting"], params='{"name": "Ada"}')
            assert greeted["success"] is True, greeted
            text = self._cmd(daemon, "eval", ["document.getElementById('out').textContent"])
            assert json.loads(text["output"]) == "Hello Ada"

            refused = self._cmd(daemon, "webmcp-call", ["refuse"])
            assert refused["success"] is False
            assert refused["data"]["isError"] is True

            unknown = self._cmd(daemon, "webmcp-call", ["nope"])
            assert unknown["success"] is False
            assert 'No WebMCP tool named "nope"' in unknown["output"]
        finally:
            self._cmd(daemon, "close")


@pytest.mark.e2e
class TestDebuggerE2E:
    """BrowserContext.debugger: a paused command is held, other commands keep working."""

    SESSION = "debugger-e2e"

    def _cmd(self, port, action, args=None, **options):
        return _cmd(port, action, args, {"session": self.SESSION, **options})

    def test_pause_hold_step_resume(self, daemon):
        opened = self._cmd(daemon, "open", [FIXTURE_URL], profile=f"debugger-e2e-{daemon}")
        assert opened["success"] is True, opened
        try:
            armed = self._cmd(daemon, "debugger-pause")
            assert armed["data"]["state"] == "armed", armed

            # aria_snapshot is not a pausable driver call: it runs through and says so.
            snap = self._cmd(daemon, "snapshot")
            assert snap["success"] is True, snap
            assert "paused" not in snap
            assert "Debugger: `snapshot` ran without pausing" in snap["output"]

            held = self._cmd(daemon, "eval", ["document.title = 'stepped'"])
            assert held.get("paused") is True, held
            assert held["data"]["title"] == "Evaluate"
            assert held["data"]["command"] == "eval document.title = 'stepped'"

            # Not paused itself: the driver lets every call through while one is paused.
            title = self._cmd(daemon, "title")
            assert title["success"] is True, title
            assert "stepped" not in title["output"]

            resumed = self._cmd(daemon, "debugger-resume")
            assert resumed["success"] is True, resumed
            assert resumed["output"].startswith("`eval document.title = 'stepped'` finished:")
            assert self._cmd(daemon, "title")["output"].strip().endswith("stepped")
            assert self._cmd(daemon, "debugger-status")["data"]["state"] == "off"

            # An armed pause nothing has hit can be cancelled.
            self._cmd(daemon, "debugger-pause")
            cancelled = self._cmd(daemon, "debugger-resume")
            assert cancelled["output"] == "Pause request cancelled. Debugger off."
        finally:
            self._cmd(daemon, "close")

    def test_close_while_paused(self, daemon):
        opened = self._cmd(daemon, "open", [FIXTURE_URL], profile=f"debugger-e2e-{daemon}")
        assert opened["success"] is True, opened
        self._cmd(daemon, "debugger-pause")
        held = self._cmd(daemon, "eval", ["1"])
        assert held.get("paused") is True, held
        closed = self._cmd(daemon, "close")
        assert closed["success"] is True, closed
        assert "closed" in closed["output"]


EXTRAS_PAGE = """<!doctype html><html><head><title>extras</title></head><body>
<p id="tip">Deletes the file forever</p>
<button aria-describedby="tip">Delete</button>
<button aria-describedby="tip">Delete</button>
<button title="Save your work">Save</button>
<button>Plain</button>
<div id="host"></div><div id="closed"></div>
<div id="anim" style="width:40px;height:40px;background:red;position:relative"></div>
<script>
const open = document.getElementById("host").attachShadow({mode: "open"});
open.innerHTML = '<span>OPEN</span><div id="inner"></div>';
open.getElementById("inner").attachShadow({mode: "open"}).innerHTML = "<b>NESTED</b>";
document.getElementById("closed").attachShadow({mode: "closed"}).innerHTML = "<i>HIDDEN</i>";
let x = 0;
setInterval(() => { x = (x + 3) % 300; document.getElementById("anim").style.left = x + "px"; }, 16);
</script></body></html>"""


@pytest.fixture
def extras_page(daemon, tmp_path):
    page = tmp_path / "extras.html"
    page.write_text(EXTRAS_PAGE, encoding="utf-8")
    resp = _cmd(daemon, "goto", args=[page.resolve().as_uri()])
    assert resp["success"] is True, resp
    return daemon


class TestFindByRoleHtmlAndVideoFps:
    def test_find_by_role_and_description_returns_refs(self, extras_page):
        resp = _cmd(extras_page, "find", options={"role": "button", "description": "deletes the file"})
        assert resp["success"] is True, resp
        refs = [m["ref"] for m in resp["data"]]
        assert len(refs) == 2 and all(refs) and refs[0] != refs[1]
        # Each ref is its own element: the second Delete button follows the first.
        second = _cmd(extras_page, "eval", args=["el => el.previousElementSibling.textContent", refs[1]])
        assert json.loads(second["output"]) == "Delete"

        titled = _cmd(extras_page, "find", options={"role": "button", "description": "/^save/i"})
        assert [m["name"] for m in titled["data"]] == ["Save"]
        exact = _cmd(extras_page, "find", options={"role": "button", "description": "deletes", "exact": True})
        assert exact["data"] == []

    def test_html_includes_open_shadow_roots_only_with_the_flag(self, extras_page):
        plain = _cmd(extras_page, "html", options={"max-chars": "0"})
        assert plain["success"] is True
        assert "shadowrootmode" not in plain["output"]

        shadow = _cmd(extras_page, "html", options={"max-chars": "0", "include-shadow": True})
        assert '<div id="host"><template shadowrootmode="open"><span>OPEN</span>' in shadow["output"]
        assert '<template shadowrootmode="open"><b>NESTED</b></template>' in shadow["output"]
        assert '<div id="closed"></div>' in shadow["output"]

        scoped = _cmd(extras_page, "html", options={"selector": "#host", "include-shadow": True})
        assert scoped["output"].startswith('<div id="host"><template shadowrootmode="open">')
        assert _cmd(extras_page, "html", options={"selector": "#host"})["output"] == '<div id="host"></div>'

        cut = _cmd(extras_page, "html", options={"max-chars": "50"})
        assert "... (truncated: 50 of" in cut["output"]

    @pytest.mark.parametrize(("options", "rate"), [({}, "25/1"), ({"fps": "10"}, "10/1")])
    def test_video_is_real_time_at_the_fps(self, extras_page, tmp_path, options, rate):
        import shutil
        import subprocess

        if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
            pytest.skip("ffmpeg/ffprobe not installed")
        assert _cmd(extras_page, "video-start", options=options)["success"] is True
        time.sleep(2)
        out = tmp_path / "fps.webm"
        stopped = _cmd(extras_page, "video-stop", options={"filename": str(out)})
        assert stopped["success"] is True, stopped
        assert "Warning" not in stopped["output"]
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate"]
            + ["-show_entries", "format=duration", "-of", "json", str(out)],
            capture_output=True,
            text=True,
            check=True,
        )
        info = json.loads(probe.stdout)
        assert info["streams"][0]["avg_frame_rate"] == rate
        assert 1.8 <= float(info["format"]["duration"]) <= 3.5


NATIVE_REFS_PAGE = """<!doctype html><html><head><title>refs</title></head><body>
<button id="keep">Keep</button>
<ul id="items"><li><button id="a">Item A</button></li><li><button id="b">Item B</button></li></ul>
<iframe id="frame" title="Inner" srcdoc="<button id='inner' onclick='this.textContent=&quot;done&quot;'>Inner</button>"></iframe>
</body></html>"""


@pytest.fixture
def refs_page(daemon, tmp_path):
    page = tmp_path / "refs.html"
    page.write_text(NATIVE_REFS_PAGE, encoding="utf-8")
    resp = _cmd(daemon, "goto", args=[page.resolve().as_uri()])
    assert resp["success"] is True, resp
    time.sleep(0.5)
    return daemon


@pytest.mark.e2e
class TestNativeRefs:
    """Refs are Playwright's own: stable, iframe-aware, and stale ones fail fast."""

    def test_iframe_elements_get_refs_that_act(self, refs_page):
        snap = _read_snapshot(refs_page)
        inner = re.search(r'button "Inner" \[ref=(f\d+e\d+)\]', snap)
        assert inner, snap
        locator = _cmd(refs_page, "generate-locator", args=[inner.group(1)])
        assert (
            locator["output"]
            == "frameLocator('iframe[title=\"Inner\"]').getByRole('button', { name: 'Inner', exact: true })"
        )
        assert _cmd(refs_page, "click", args=[inner.group(1)])["success"] is True
        # The click renamed the button, and a renamed element gets a new ref.
        renamed = _cmd(refs_page, "text", args=[inner.group(1)])
        assert "a ref goes away when its element is removed or its role or name changes" in renamed["output"]
        done = re.search(r'button "done".*?\[ref=(f\d+e\d+)\]', _read_snapshot(refs_page)).group(1)
        assert _cmd(refs_page, "text", args=[done])["output"] == "done"

    def test_refs_survive_snapshots_and_stale_ones_fail_fast(self, refs_page):
        snap = _read_snapshot(refs_page)
        item_a = re.search(r'button "Item A" \[ref=((?:f\d+)?e\d+)\]', snap).group(1)
        keep = re.search(r'button "Keep" \[ref=((?:f\d+)?e\d+)\]', snap).group(1)
        assert f"[ref={item_a}]" in _read_snapshot(refs_page)  # a second snapshot keeps the number

        _cmd(
            refs_page, "eval", args=["document.getElementById('items').innerHTML = '<li><button>Item A</button></li>'"]
        )
        started = time.monotonic()
        stale = _cmd(refs_page, "click", args=[item_a])
        assert stale["success"] is False
        assert f"Ref {item_a} no longer matches an element" in stale["output"]
        assert time.monotonic() - started < 5
        assert _cmd(refs_page, "click", args=[keep])["success"] is True

    def test_a_malformed_ref_is_a_usage_error(self, refs_page):
        _read_snapshot(refs_page)
        resp = _cmd(refs_page, "click", args=["#keep"])
        assert resp["success"] is False
        assert resp["exit_code"] == 2

    def test_scoped_snapshot_keeps_refs_outside_it(self, refs_page):
        snap = _read_snapshot(refs_page)
        keep = re.search(r'button "Keep" \[ref=((?:f\d+)?e\d+)\]', snap).group(1)
        scoped = _cmd(refs_page, "snapshot", options={"selector": "#items"})
        assert scoped["success"] is True, scoped
        assert "Keep" not in Path(scoped["snapshot_path"]).read_text(encoding="utf-8")
        assert _cmd(refs_page, "click", args=[keep])["success"] is True


TRACES_SESSION = "traces"


@pytest.fixture
def traces(daemon):
    """A session of its own: after a pick, Playwright's recorder stays in every page of the session."""
    opened = _t(daemon, "open", args=[DETECT_FIXTURE_URL])
    assert opened["success"] is True, opened
    yield daemon
    _cmd(daemon, "close", options={"session": TRACES_SESSION})


def _t(port: int, action: str, args=None, **options) -> dict:
    profile = {"profile": f"e2e-{TEST_PORT}-{TRACES_SESSION}"} if action == "open" else {}
    return _cmd(port, action, args, {"session": TRACES_SESSION, **profile, **options})


def _probe(port: int) -> dict:
    """What the page's own main-world script sees (tests/detect_fixture.html)."""
    time.sleep(0.3)  # the page refreshes its probe every 50ms
    resp = _t(port, "eval", args=["document.getElementById('probe').value"])
    assert resp["success"] is True, resp
    return json.loads(json.loads(resp["output"]))


def _target_ref(port: int) -> str:
    snap = _t(port, "snapshot")
    return _find_ref(Path(snap["snapshot_path"]).read_text(encoding="utf-8"), 'button "Target"')


@pytest.mark.e2e
class TestPageVisibleTraces:
    def test_highlight_hide_leaves_no_glass(self, traces):
        ref = _target_ref(traces)
        assert _t(traces, "highlight", args=[ref])["success"] is True
        assert _probe(traces)["glass"] == 1

        assert _t(traces, "highlight", args=[ref], hide=True)["success"] is True
        seen = _probe(traces)
        assert seen["glass"] == 0
        assert seen["mutations"][-1] == "-X-PW-GLASS"
        assert seen["playwright"] == "undefined" and seen["pwGlobals"] == 0

        # Playwright puts the glass back for the next highlight; --hide without a ref clears it again.
        assert _t(traces, "highlight", args=[ref])["success"] is True
        assert _probe(traces)["glass"] == 1
        assert _t(traces, "highlight", hide=True)["output"] == "All highlights cleared."
        time.sleep(1)
        assert _probe(traces)["glass"] == 0

    def test_pick_locator_needs_detectable(self, traces):
        refused = _t(traces, "pick-locator", wait="5")
        assert refused["success"] is False and refused["exit_code"] == 2
        seen = _probe(traces)
        assert seen["playwright"] == "undefined" and seen["glass"] == 0

    def test_pick_locator_cleanup_and_what_stays_until_the_session_closes(self, traces):
        import threading

        box = _t(
            traces,
            "eval",
            args=[
                "(() => { const r = document.getElementById('target').getBoundingClientRect();"
                " return `at ${Math.round(r.x + r.width / 2)},${Math.round(r.y + r.height / 2)}`; })()"
            ],
        )
        x, y = re.search(r"at (\d+),(\d+)", box["output"]).groups()
        result = {}
        thread = threading.Thread(target=lambda: result.update(_t(traces, "pick-locator", wait="20", detectable=True)))
        thread.start()
        time.sleep(1.5)
        _t(traces, "mousemove", args=[x, y])
        _t(traces, "mousedown")
        _t(traces, "mouseup")
        thread.join(25)
        assert result["success"] is True, result
        assert result["output"].splitlines()[-1].startswith("help: run `patchright-cli close`")

        time.sleep(1.5)  # past the recorder's 1s mode poll and its 500ms glass loop
        seen = _probe(traces)
        assert seen["cursor"] is None  # removed from the isolated world, and it stays removed
        assert seen["sheets"] == 0
        assert "@BODY.data-pw-cursor=null" in seen["mutations"]
        # The recorder lives in the page's own world and follows the session to every document.
        assert seen["playwright"] == "object" and seen["pwGlobals"] > 0
        assert _t(traces, "reload")["success"] is True
        assert _probe(traces)["playwright"] == "object"

        # Closing the session is what drops it.
        assert _t(traces, "close")["success"] is True
        assert _t(traces, "open", args=[DETECT_FIXTURE_URL])["success"] is True
        seen = _probe(traces)
        assert (seen["glass"], seen["cursor"], seen["sheets"], seen["playwright"], seen["pwGlobals"]) == (
            0,
            None,
            0,
            "undefined",
            0,
        )
