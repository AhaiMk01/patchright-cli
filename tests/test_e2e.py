"""End-to-end tests exercising real daemon + browser."""

import json
import os
import re
import socket
import struct
import time
from pathlib import Path

import pytest

from patchright_cli.daemon import ensure_daemon_running, read_daemon_token

FIXTURE_URL = "file://" + os.path.join(os.path.dirname(__file__), "fixture.html")
# Overridable so parallel runs on one machine do not share a daemon.
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
    resp = _send_tcp(TEST_PORT, "open", args=[FIXTURE_URL])
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
        assert "[ref=e1]" in snap

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

        match = re.search(r"\[ref=(e\d+)\]", resp["output"])
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


@pytest.mark.e2e
class TestNativeStorageAndDrop:
    def test_drop_files_and_data(self, daemon, tmp_path):
        tab = {"tab": "drop"}
        assert _cmd(daemon, "open", args=[_DROP_PAGE], options=tab)["success"] is True
        snap = _cmd(daemon, "snapshot", options=tab)
        zone = re.search(r'region "dropzone".*?\[ref=(e\d+)\]', Path(snap["snapshot_path"]).read_text("utf-8"))
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
            target=lambda: result.update(_cmd(port, "pick-locator", options=options or {"wait": "20"}))
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
        resp = _cmd(daemon, "pick-locator", options={"wait": "1"})
        assert resp["success"] is False
        assert "No element was picked within 1s" in resp["output"]
        again = _cmd(daemon, "pick-locator", options={"cancel": True})
        assert "no-op" in again["output"]
