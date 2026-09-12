"""End-to-end tests exercising real daemon + browser."""

import json
import os
import re
import socket
import struct
import time
from pathlib import Path

import pytest

from patchright_cli.daemon import ensure_daemon_running

FIXTURE_URL = "file://" + os.path.join(os.path.dirname(__file__), "fixture.html")
TEST_PORT = 19321


def _send_tcp(port: int, cmd: str, args=None, options=None) -> dict:
    msg = {"command": cmd, "args": args or [], "options": options or {}}
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
        assert "No matches" in resp["output"]

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
        resp = _cmd(daemon, "close", options={"tab": "inbox"})
        assert resp["success"] is True
        assert "still open" in resp["output"]

        still_there = _cmd(daemon, "url")
        assert still_there["success"] is True
