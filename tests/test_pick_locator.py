"""`pick-locator`: a person clicks an element and the command prints its locator."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from patchright_cli import cli, daemon, mcp_server
from patchright_cli.daemon import DaemonState, Session, _picked_role, handle_command
from patchright_cli.ref_registry import AriaRefEntry, RefRegistry


@pytest.fixture
def state_with_page():
    state = MagicMock(spec=DaemonState)
    state.sessions = {}
    state.shutdown_event = None
    session = MagicMock(spec=Session)
    session.name = "default"
    session.ref_registry = None
    session.tabs = {"default": MagicMock()}
    session.activate_tab = MagicMock()
    session.uses_host_context = False
    session.pages = []
    session.current_tab = 0
    session.context = MagicMock()
    session.network_guard = None
    session._codegen = None
    session._video_recording = False
    session._video_show_actions = None
    page = MagicMock()
    page.cancel_pick_locator = AsyncMock()
    session.page = page
    state.sessions = {"default": session}
    yield state, session, page
    daemon._active_picks.discard(page)


def _picked(selector: str):
    locator = MagicMock()
    locator._impl_obj._selector = selector
    return locator


async def _run(state, **options):
    return await handle_command(state, {"command": "pick-locator", "args": [], "options": options})


# -- selector role parsing --------------------------------------------------


@pytest.mark.parametrize(
    ("selector", "role"),
    [
        ('internal:role=button[name="Submit"i]', "button"),
        ("internal:role=list >> internal:role=listitem >> nth=1", "listitem"),
        ("internal:role=main", "main"),
        ('internal:text="Item"i >> nth=1', None),
        ("#buy", None),
        ("nth=0", None),
    ],
)
def test_picked_role(selector, role):
    assert _picked_role(selector) == role


# -- daemon handler -----------------------------------------------------------


@pytest.mark.asyncio
async def test_pick_returns_the_locator(state_with_page):
    state, _, page = state_with_page
    page.pick_locator = AsyncMock(return_value=_picked('internal:text="Item"i >> nth=1'))
    r = await _run(state)
    assert r["success"] is True
    assert r["output"] == 'internal:text="Item"i >> nth=1'
    assert r["data"] == {"locator": 'internal:text="Item"i >> nth=1', "ref": None}
    assert page not in daemon._active_picks


@pytest.mark.asyncio
async def test_pick_adds_the_matching_snapshot_ref(state_with_page):
    state, session, page = state_with_page
    registry = RefRegistry()
    registry.entries = {
        "e1": AriaRefEntry(ref="e1", role="heading", name="Title", nth=0),
        "e2": AriaRefEntry(ref="e2", role="button", name="Cancel", nth=0),
        "e3": AriaRefEntry(ref="e3", role="button", name="Submit", nth=0),
    }
    session.ref_registry = registry
    picked = _picked('internal:role=button[name="Submit"i]')
    picked.element_handle = AsyncMock(return_value=MagicMock(dispose=AsyncMock()))
    page.pick_locator = AsyncMock(return_value=picked)

    def get_by_role(role, name, exact):
        candidate = MagicMock()
        candidate.nth.return_value.evaluate = AsyncMock(return_value=name == "Submit")
        return candidate

    page.get_by_role.side_effect = get_by_role
    r = await _run(state)
    assert r["success"] is True
    assert r["data"]["ref"] == "e3"
    assert "- e3" in r["output"]
    # Only the button refs were checked, not the heading.
    assert [c.args[0] for c in page.get_by_role.call_args_list] == ["button", "button"]


@pytest.mark.asyncio
async def test_pick_skips_ref_matching_with_too_many_candidates(state_with_page):
    state, session, page = state_with_page
    registry = RefRegistry()
    registry.entries = {
        f"e{i}": AriaRefEntry(ref=f"e{i}", role="link", name=f"L{i}", nth=0)
        for i in range(daemon.PICK_REF_CANDIDATES + 1)
    }
    session.ref_registry = registry
    picked = _picked('internal:role=link[name="L3"i]')
    picked.element_handle = AsyncMock()
    page.pick_locator = AsyncMock(return_value=picked)
    r = await _run(state)
    assert r["data"]["ref"] is None
    picked.element_handle.assert_not_awaited()


@pytest.mark.asyncio
async def test_pick_times_out_and_cancels_pick_mode(state_with_page):
    state, _, page = state_with_page
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def pick():
        started.set()
        await cancelled.wait()
        raise RuntimeError("Page.pick_locator: Locator picking was cancelled")

    page.pick_locator = pick
    page.cancel_pick_locator = AsyncMock(side_effect=lambda: cancelled.set())
    r = await _run(state, wait="0.05")
    assert r["success"] is False
    assert "No element was picked within 0.05s" in r["output"]
    assert "--wait=<seconds>" in r["output"]
    page.cancel_pick_locator.assert_awaited_once()
    assert page not in daemon._active_picks


@pytest.mark.asyncio
async def test_pick_reports_a_cancel_from_another_call(state_with_page):
    state, _, page = state_with_page
    page.pick_locator = AsyncMock(side_effect=RuntimeError("Page.pick_locator: Locator picking was cancelled"))
    r = await _run(state)
    assert r["success"] is False
    assert "cancelled before an element was clicked" in r["output"]


@pytest.mark.parametrize(
    ("wait", "message"),
    [(True, "Give --wait a value"), ("abc", "Invalid --wait"), ("0", "above 0"), ("-5", "above 0"), ("inf", "above 0")],
)
@pytest.mark.asyncio
async def test_pick_rejects_a_bad_wait(state_with_page, wait, message):
    state, _, page = state_with_page
    page.pick_locator = AsyncMock()
    r = await _run(state, wait=wait)
    assert r["success"] is False
    assert message in r["output"]
    page.pick_locator.assert_not_called()


@pytest.mark.asyncio
async def test_pick_refuses_a_second_pick_on_the_same_tab(state_with_page):
    state, _, page = state_with_page
    page.pick_locator = AsyncMock()
    daemon._active_picks.add(page)
    r = await _run(state)
    assert r["success"] is False
    assert "already waiting" in r["output"]
    page.pick_locator.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_without_a_pick_is_a_noop(state_with_page):
    state, _, page = state_with_page
    r = await _run(state, cancel=True)
    assert r["success"] is True
    assert "no-op" in r["output"]
    page.cancel_pick_locator.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_ends_a_pick_in_progress(state_with_page):
    state, _, page = state_with_page
    daemon._active_picks.add(page)
    r = await _run(state, cancel=True)
    assert r["success"] is True
    assert "Cancelled" in r["output"]
    page.cancel_pick_locator.assert_awaited_once()


# -- CLI ----------------------------------------------------------------------


def test_socket_timeout_outlasts_the_pick_wait():
    assert cli._socket_timeout("pick-locator", {}) == cli.DEFAULT_SOCKET_TIMEOUT
    assert cli._socket_timeout("pick-locator", {"wait": "600"}) == 600 + cli.ANNOTATE_SOCKET_MARGIN
    assert cli._socket_timeout("pick-locator", {"wait": "inf"}) == cli.DEFAULT_SOCKET_TIMEOUT
    assert cli._socket_timeout("pick-locator", {"wait": "600", "cancel": True}) == cli.DEFAULT_SOCKET_TIMEOUT


def test_recv_exact_retries_short_timeouts_until_the_deadline(monkeypatch):
    class SlowSocket:
        def __init__(self):
            self.calls = 0

        def recv(self, n):
            self.calls += 1
            if self.calls < 3:
                raise TimeoutError
            return b"abcd"[:n]

    sock = SlowSocket()
    assert cli._recv_exact(sock, 4, deadline=float("inf")) == b"abcd"
    assert sock.calls == 3

    with pytest.raises(TimeoutError):
        cli._recv_exact(SlowSocket(), 4, deadline=0)
    with pytest.raises(TimeoutError):
        cli._recv_exact(SlowSocket(), 4)


def test_pick_is_sent_interruptibly(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "ensure_daemon_running", lambda *a: None)
    monkeypatch.setattr(cli, "_send_command", lambda *a, **k: calls.append((a, k)) or {"success": True})
    cli._ensure_and_send("pick-locator", [], {"session": "s"}, 9333, False)
    cli._ensure_and_send("pick-locator", [], {"session": "s", "cancel": True}, 9333, False)
    assert calls[0][1] == {"interruptible": True}
    assert calls[1][1] == {}


def test_ctrl_c_cancels_the_pick(monkeypatch, capsys):
    sent = []

    def fake_send(command, args, options, port, timeout=None, interruptible=False):
        sent.append(dict(options))
        if interruptible:
            raise KeyboardInterrupt
        return {"success": True}

    monkeypatch.setattr(cli, "_send_command", fake_send)
    with pytest.raises(SystemExit) as exc:
        cli._send_pick_locator([], {"session": "s", "tab": "default"}, 9333)
    assert exc.value.code == 130
    assert sent == [{"session": "s", "tab": "default"}, {"session": "s", "tab": "default", "cancel": True}]
    assert "interrupted" in capsys.readouterr().out


def test_unknown_flag_exits_2(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["patchright-cli", "pick-locator", "--timeout-ms=5"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


def test_send_command_reads_in_slices_when_interruptible(monkeypatch):
    timeouts = []
    reply = json.dumps({"success": True, "output": "x"}).encode()
    chunks = [TimeoutError(), len(reply).to_bytes(4, "big"), reply]

    class FakeSocket:
        def __init__(self, *a, **k):
            pass

        def settimeout(self, value):
            timeouts.append(value)

        def connect(self, _):
            pass

        def sendall(self, _):
            pass

        def recv(self, _n):
            chunk = chunks.pop(0)
            if isinstance(chunk, Exception):
                raise chunk
            return chunk

        def close(self):
            pass

    monkeypatch.setattr(cli.socket, "socket", FakeSocket)
    monkeypatch.setattr(cli, "read_daemon_token", lambda port: None)
    assert cli._send_command("pick-locator", [], {}, 9333, interruptible=True)["output"] == "x"
    assert timeouts == [cli.DEFAULT_SOCKET_TIMEOUT, cli.INTERRUPT_POLL]


# -- MCP ------------------------------------------------------------------------


def test_mcp_pick_locator_args():
    assert mcp_server._build_args("patchright_pick_locator", {"wait": 90, "cancel": False}) == [
        "pick-locator",
        "--wait=90",
    ]
    assert mcp_server._build_args("patchright_pick_locator", {"wait": 90, "cancel": True}) == [
        "pick-locator",
        "--cancel",
    ]
