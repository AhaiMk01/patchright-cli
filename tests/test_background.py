"""Background policy must survive reuse, empty contexts, and launch failures."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from patchright_cli import background, cli, daemon
from tests.test_cli_axi import _run, _run_reply


def _context():
    page = MagicMock(url="about:blank")
    page.is_closed.return_value = False
    context = MagicMock()
    context.pages = [page]
    context.close = AsyncMock()
    context.new_page = AsyncMock(side_effect=AssertionError("foreground page creation"))
    return context


@pytest.fixture
def state(monkeypatch):
    state = daemon.DaemonState()
    state.playwright = MagicMock()
    monkeypatch.setattr(state, "_launch", AsyncMock(return_value=_context()))
    monkeypatch.setattr(daemon.Session, "setup_listeners", AsyncMock())
    monkeypatch.setattr(daemon, "_page_info", AsyncMock(side_effect=lambda *a, **k: {"success": True, "output": ""}))
    return state


@pytest.mark.asyncio
async def test_protected_operation_requires_background_even_when_option_is_false(state):
    reply = await daemon.handle_command(state, {"command": "background-open", "options": {"background": False}})
    assert reply["success"], reply
    assert state.sessions["default"].background is True
    assert state._launch.await_args.kwargs["background"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["background-open", "open"])
async def test_background_is_a_noted_noop_on_a_new_headless_session(state, command):
    """Issue #56: a headless browser has no window to focus, so --background is already satisfied."""
    reply = await daemon.handle_command(state, {"command": command, "options": {"background": True, "headless": True}})
    assert reply["success"], reply
    assert reply["output"].splitlines()[0] == f"Note: {daemon.BACKGROUND_IGNORED_NOTE}"
    session = state.sessions["default"]
    assert session.headless is True
    assert session.background is False
    assert state._launch.await_args.kwargs["background"] is False
    assert state._launch.await_args.args[1] is True


@pytest.mark.asyncio
async def test_background_is_a_noted_noop_when_the_daemon_defaults_to_headless(state):
    state.default_headless = True
    reply = await daemon.handle_command(state, {"command": "background-open", "options": {}})
    assert reply["success"], reply
    assert f"Note: {daemon.BACKGROUND_IGNORED_NOTE}" in reply["output"]
    assert state.sessions["default"].background is False
    assert state._launch.await_args.kwargs["background"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [{}, {"headless": True}])
async def test_background_is_a_noted_noop_on_an_existing_headless_session(state, options):
    session = await state.get_or_create_session("work", headless=True)
    plain = await daemon.handle_command(state, {"command": "open", "options": {"session": "work", **options}})
    daemon._page_info.reset_mock()

    for _ in range(2):  # the note is reported on every such open, once each
        reply = await daemon.handle_command(
            state, {"command": "background-open", "options": {"session": "work", **options}}
        )
        assert reply["success"], reply
        assert reply["output"] == f"Note: {daemon.BACKGROUND_IGNORED_NOTE}\n" + plain["output"]
        assert "data" not in reply
    assert daemon._page_info.await_count == 2
    assert state.sessions["work"] is session
    assert session.background is False
    assert state._launch.await_count == 1
    session.context.close.assert_not_awaited()

    reply = await daemon.handle_command(state, {"command": "open", "options": {"session": "work"}})
    assert "Note:" not in reply["output"]


@pytest.mark.asyncio
async def test_headed_background_open_carries_no_headless_note(state):
    reply = await daemon.handle_command(state, {"command": "background-open", "options": {}})
    assert reply["success"], reply
    assert "Note:" not in reply["output"]
    assert state.sessions["default"].background is True


@pytest.mark.asyncio
async def test_headless_flag_does_not_stop_background_on_an_existing_headed_session(state):
    """The session's real mode decides: a headed one still has a window that could take focus."""
    session = await state.get_or_create_session("work")
    reply = await daemon.handle_command(
        state, {"command": "background-open", "options": {"session": "work", "headless": True}}
    )
    assert reply["success"], reply
    assert "Note:" not in reply["output"]
    assert session.background is True


@pytest.mark.asyncio
async def test_owned_headed_session_promotes_without_replacing_tabs(state, monkeypatch):
    session = await state.get_or_create_session("work")
    original_pages = list(session.pages)
    new_page = MagicMock(url="about:blank#new")
    new_page.is_closed.return_value = False
    creator = AsyncMock(return_value=new_page)
    monkeypatch.setattr(daemon, "new_background_page", creator)

    assert await state.get_or_create_session("work", background=True) is session
    assert await state.get_or_create_session("work", background=True) is session
    tab = await session.open_tab("later")

    assert session.background is True
    assert session.pages[: len(original_pages)] == original_pages
    assert tab.page is new_page
    assert state._launch.await_count == 1
    creator.assert_awaited_once_with(session.context)
    session.context.close.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["attached", "host"])
async def test_incompatible_existing_session_is_preserved(state, kind):
    context = _context()
    session = daemon.Session("work", context, list(context.pages))
    session.is_attached = kind in ("attached", "host")
    session.uses_host_context = kind == "host"
    state.sessions["work"] = session
    original_pages = list(session.pages)

    with pytest.raises(ValueError, match="attached"):
        await state.get_or_create_session("work", background=True)

    assert state.sessions["work"] is session
    assert session.pages == original_pages
    assert session.background is False
    context.close.assert_not_awaited()
    state._launch.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_headless_session_records_launch_fact(state):
    session = await state.get_or_create_session("work", headless=True)
    assert session.headless is True


@pytest.mark.asyncio
async def test_protected_policy_open_survives_closed_default_tab(state):
    context = _context()
    first = context.pages[0]
    live = MagicMock(url="about:blank#live")
    live.is_closed.return_value = False
    session = daemon.Session("work", context, [first, live])
    session.tabs["live"] = daemon.Tab("live", live)
    session._forget_page(first, outside=True)
    state.sessions["work"] = session

    reply = await daemon.handle_command(state, {"command": "background-open", "options": {"session": "work"}})
    assert reply["success"], reply
    assert session.background is True
    assert session.pages == [live]
    assert session.tabs["live"].page is live
    daemon._page_info.assert_not_awaited()
    context.new_page.assert_not_awaited()

    reply = await daemon.handle_command(state, {"command": "open", "options": {"session": "work"}})
    assert reply["success"] is False


@pytest.mark.asyncio
async def test_empty_initial_context_uses_background_creator(state, monkeypatch):
    context = state._launch.return_value
    context.pages = []
    page = MagicMock(url="about:blank")
    creator = AsyncMock(return_value=page)
    monkeypatch.setattr(daemon, "new_background_page", creator)
    session = await state.get_or_create_session("work", background=True)
    assert session.pages == [page]
    creator.assert_awaited_once_with(context)
    context.new_page.assert_not_awaited()


def test_cli_translates_public_background_flag(monkeypatch, capsys):
    code, _, _, sent = _run(["open", "--background"], monkeypatch, capsys)
    assert code == 0
    assert sent["command"] == "background-open"


def test_cli_sends_background_headless_to_the_daemon(monkeypatch, capsys):
    """Issue #56: the daemon knows whether the session is headless, so the CLI no longer refuses."""
    code, _, _, sent = _run(["open", "--background", "--headless", "about:blank"], monkeypatch, capsys)
    assert code == 0
    assert sent["command"] == "background-open"
    assert sent["options"]["headless"] is True
    assert sent["args"] == ["about:blank"]


@pytest.mark.parametrize("flags", [[], ["--json"]])
def test_cli_prints_the_background_ignored_note(monkeypatch, capsys, flags):
    reply = {"success": True, "output": f"Note: {daemon.BACKGROUND_IGNORED_NOTE}\n### Page\n- URL: about:blank"}
    code, out = _run_reply(["open", "--headless", "--background", *flags], reply, monkeypatch, capsys)
    assert code == 0
    assert f"Note: {daemon.BACKGROUND_IGNORED_NOTE}" in out
    if flags:
        assert json.loads(out)["output"].startswith("Note: --background ignored")


def test_background_help_says_headless_ignores_it():
    assert "headless" in cli.COMMAND_SPECS["open"]["flags"]["background"]


@pytest.mark.asyncio
@pytest.mark.parametrize("surviving_page", [False, True])
async def test_context_cookies_remain_readable_after_default_page_closes(state, surviving_page):
    context = _context()
    context.cookies = AsyncMock(return_value=[])
    context.pages = [_context().pages[0]] if surviving_page else []
    session = daemon.Session("work", context, list(context.pages))
    session._current_gone = True
    state.sessions["work"] = session

    reply = await daemon.handle_command(state, {"command": "cookie-list", "options": {"session": "work"}})

    assert reply["success"], reply
    assert reply["output"] == "0 cookies in this session"
    assert state.sessions["work"] is session


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", [False, True])
async def test_concurrent_pages_use_public_browser_cdp_without_an_existing_page(empty):
    context = _context()
    context.pages = [] if empty else context.pages
    pending = []
    sessions = []

    class ExpectedPage:
        def __init__(self, predicate):
            self.predicate = predicate
            self.value = asyncio.get_running_loop().create_future()

        async def __aenter__(self):
            pending.append(self)
            return self

        async def __aexit__(self, *exc):
            pending.remove(self)

    async def send(method, params):
        assert method == "Target.createTarget"
        assert params["background"] is True
        assert params.get("newWindow", False) is empty
        await asyncio.sleep(0)
        page = SimpleNamespace(url=params["url"])
        for expected in list(pending):
            if expected.predicate(page):
                expected.value.set_result(page)
        return {"targetId": params["url"]}

    async def new_cdp():
        cdp = SimpleNamespace(send=AsyncMock(side_effect=send), detach=AsyncMock())
        sessions.append(cdp)
        return cdp

    context.browser.new_browser_cdp_session = AsyncMock(side_effect=new_cdp)
    context.new_cdp_session = AsyncMock(side_effect=AssertionError("page CDP is not allowed"))
    context.expect_page.side_effect = lambda predicate: ExpectedPage(predicate)
    pages = await asyncio.gather(*(background.new_background_page(context) for _ in range(4)))
    assert len({page.url for page in pages}) == 4
    assert all(cdp.detach.await_count == 1 for cdp in sessions)
    context.new_page.assert_not_awaited()


def _failed_websocket(monkeypatch, error):
    ws_manager = MagicMock()
    ws_manager.__aenter__ = AsyncMock(side_effect=error)
    http = MagicMock()
    http.ws_connect.return_value = ws_manager
    http_manager = MagicMock()
    http_manager.__aenter__ = AsyncMock(return_value=http)
    monkeypatch.setattr(background.aiohttp, "ClientSession", MagicMock(return_value=http_manager))


@pytest.mark.asyncio
async def test_failed_bootstrap_drains_late_launch_and_preserves_error(tmp_path, monkeypatch):
    injected = RuntimeError("injected bootstrap failure")
    _failed_websocket(monkeypatch, injected)
    context = _context()
    launched = asyncio.Event()
    release = asyncio.Event()
    cancelled = []
    calls = []

    async def launch(profile, **options):
        calls.append(options)
        (tmp_path / "DevToolsActivePort").write_text("1234\n/devtools/browser/test\n")
        launched.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        return context

    task = asyncio.create_task(
        background.launch_background(SimpleNamespace(launch_persistent_context=launch), str(tmp_path), {"args": []})
    )
    await launched.wait()
    await asyncio.sleep(0.1)
    release.set()
    with pytest.raises(RuntimeError) as exc:
        await task
    assert exc.value is injected
    assert not cancelled
    assert 0 < calls[0]["timeout"] <= 30_000
    context.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancellation_during_bootstrap_completes_startup_then_closes(tmp_path, monkeypatch):
    context = _context()
    bootstrap_started = asyncio.Event()
    startup_complete = asyncio.Event()
    attempts = []

    async def launch(profile, **options):
        (tmp_path / "DevToolsActivePort").write_text("1234\n/devtools/browser/test\n")
        await startup_complete.wait()
        return context

    async def create_target(endpoint):
        attempts.append(endpoint)
        if len(attempts) == 1:
            bootstrap_started.set()
            await asyncio.Event().wait()
        startup_complete.set()

    monkeypatch.setattr(background, "_create_background_target", create_target)
    task = asyncio.create_task(
        background.launch_background(SimpleNamespace(launch_persistent_context=launch), str(tmp_path), {"args": []})
    )
    await bootstrap_started.wait()
    task.cancel("bootstrap cancellation")
    with pytest.raises(asyncio.CancelledError, match="bootstrap cancellation"):
        await asyncio.wait_for(task, 1)
    assert len(attempts) == 2
    context.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_bootstrap_requires_matching_reply_and_background_window_flags(monkeypatch):
    ws = MagicMock()
    ws.send_json = AsyncMock()
    ws.__aiter__.return_value = [
        MagicMock(json=lambda: {"method": "Target.targetCreated"}),
        MagicMock(json=lambda: {"id": 1, "result": {"targetId": "target"}}),
    ]
    manager = MagicMock()
    manager.__aenter__ = AsyncMock(return_value=ws)
    http = MagicMock()
    http.ws_connect.return_value = manager
    http_manager = MagicMock()
    http_manager.__aenter__ = AsyncMock(return_value=http)
    monkeypatch.setattr(background.aiohttp, "ClientSession", MagicMock(return_value=http_manager))

    await background._create_background_target("http://127.0.0.1:1234/devtools/browser/test")
    assert ws.send_json.await_args.args[0]["params"] == {"url": "about:blank", "background": True, "newWindow": True}
    ws.__aiter__.return_value = []
    with pytest.raises(RuntimeError, match="without a response"):
        await background._create_background_target("http://127.0.0.1:1234/devtools/browser/test")


@pytest.mark.asyncio
async def test_repeated_cancellation_waits_for_late_context_close(tmp_path):
    context = _context()
    launch_started = asyncio.Event()
    release_launch = asyncio.Event()
    close_started = asyncio.Event()
    release_close = asyncio.Event()
    cancelled = []

    async def launch(*args, **kwargs):
        launch_started.set()
        try:
            await release_launch.wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        return context

    async def close():
        close_started.set()
        await release_close.wait()

    context.close.side_effect = close
    task = asyncio.create_task(
        background.launch_background(SimpleNamespace(launch_persistent_context=launch), str(tmp_path), {"args": []})
    )
    try:
        await launch_started.wait()
        task.cancel("original cancellation")
        await asyncio.sleep(0)
        task.cancel("repeated cancellation during drain")
        release_launch.set()
        await asyncio.wait_for(close_started.wait(), 1)
        task.cancel("repeated cancellation during close")
        await asyncio.sleep(0)
        assert not task.done()
        release_close.set()
        with pytest.raises(asyncio.CancelledError, match="original cancellation"):
            await task
        assert not cancelled
        context.close.assert_awaited_once()
    finally:
        release_launch.set()
        release_close.set()
        await asyncio.gather(task, return_exceptions=True)


def _stale_attached(state):
    context = _context()
    stale = daemon.Session("work", context, list(context.pages))
    stale.is_attached = True
    stale.cdp_endpoint = "http://127.0.0.1:9222"
    stale.browser = MagicMock()
    stale.browser.is_connected.return_value = False
    stale.detach = AsyncMock()
    state.sessions["work"] = stale
    return stale


@pytest.mark.asyncio
async def test_background_replaces_stale_attached_session(state):
    stale = _stale_attached(state)

    session = await state.get_or_create_session("work", background=True)

    assert session is not stale
    assert session.background is True
    assert session.is_attached is False
    stale.detach.assert_awaited_once()
    state._launch.assert_awaited_once()


@pytest.mark.asyncio
async def test_background_open_reports_replacing_a_stale_session(state):
    _stale_attached(state)

    reply = await daemon.handle_command(state, {"command": "background-open", "options": {"session": "work"}})

    assert reply["success"], reply
    assert "Replaced stale session 'work'" in reply["output"]
    assert "uses background page creation" not in reply["output"]
