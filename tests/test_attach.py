"""Unit tests for `attach --context=new|host` (Isolated vs Host Context)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from patchright_cli import daemon
from patchright_cli.daemon import DaemonState, handle_command


def _fake_page(url="https://example.com", opener=None):
    page = MagicMock()
    page.url = url
    page.close = AsyncMock()
    page.goto = AsyncMock()
    page.opener = AsyncMock(return_value=opener)
    return page


def _fake_context(pages=None):
    context = MagicMock()
    context.pages = list(pages or [])
    context.new_page = AsyncMock(side_effect=lambda: _fake_page("about:blank"))
    context.new_cdp_session = AsyncMock(side_effect=RuntimeError("no CDP in unit tests"))
    context.close = AsyncMock()
    return context


def _fake_browser(contexts):
    browser = MagicMock()
    browser.contexts = contexts
    browser.new_context = AsyncMock(side_effect=lambda **_: _fake_context())
    browser.close = AsyncMock()
    browser.is_connected = MagicMock(return_value=True)
    return browser


def _state_with_browser(existing_contexts):
    """A DaemonState whose Playwright connects to a fake CDP browser."""
    browser = _fake_browser(existing_contexts)
    state = DaemonState()
    state.playwright = MagicMock()
    state.playwright.devices = {}
    state.playwright.chromium.connect_over_cdp = AsyncMock(return_value=browser)
    return state, browser


@pytest.fixture(autouse=True)
def _host_key_is_the_endpoint(monkeypatch):
    """No real Chrome to ask for its browser id; tests that care override this."""

    async def fake(endpoint, headers):
        return endpoint

    monkeypatch.setattr(daemon, "_resolve_host_key", fake)


@pytest.fixture
def no_page_info(monkeypatch):
    monkeypatch.setattr(daemon, "_page_info", AsyncMock(return_value={"success": True, "output": "PAGE"}))


async def _host_session(state, name="s", endpoint="http://cdp"):
    return await state.get_or_create_session(name, cdp_endpoint=endpoint, context_mode="host")


async def _run(state, command, args=None, **options):
    return await handle_command(state, {"command": command, "args": args or [], "options": options})


# -- choosing the context ------------------------------------------------------


@pytest.mark.asyncio
async def test_attach_defaults_to_a_new_isolated_context():
    host = _fake_context([_fake_page()])
    state, browser = _state_with_browser([host])

    session = await state.get_or_create_session("s", cdp_endpoint="http://cdp")

    browser.new_context.assert_awaited_once()
    assert session.context is not host
    assert session.uses_host_context is False


@pytest.mark.asyncio
async def test_attach_context_host_uses_the_host_context():
    host_page = _fake_page("https://logged-in.example")
    host = _fake_context([host_page])
    state, browser = _state_with_browser([host])

    session = await _host_session(state)

    browser.new_context.assert_not_awaited()
    assert session.context is host
    assert session.page is host_page
    assert session.uses_host_context is True


@pytest.mark.asyncio
async def test_attached_sessions_record_no_profile_of_ours():
    state, _ = _state_with_browser([_fake_context([_fake_page()])])

    await _host_session(state)
    await state.get_or_create_session("iso", cdp_endpoint="http://cdp")

    assert state.profile_dirs == {}


@pytest.mark.asyncio
async def test_attach_context_host_fails_and_disconnects_without_a_host_context():
    state, browser = _state_with_browser([])

    with pytest.raises(RuntimeError, match="no Host Context"):
        await _host_session(state)

    browser.close.assert_awaited_once()
    assert "s" not in state.sessions


@pytest.mark.asyncio
async def test_attach_disconnects_when_setup_fails_after_connecting():
    host = _fake_context([_fake_page()])
    host.on = MagicMock(side_effect=RuntimeError("listener boom"))
    state, browser = _state_with_browser([host])

    with pytest.raises(RuntimeError, match="listener boom"):
        await _host_session(state)

    browser.close.assert_awaited_once()
    assert state.sessions == {}


@pytest.mark.asyncio
async def test_attach_context_host_rejects_emulation_options_before_connecting():
    state, _ = _state_with_browser([_fake_context()])

    with pytest.raises(ValueError, match="locale"):
        await state.get_or_create_session("s", cdp_endpoint="http://cdp", context_mode="host", locale="de-DE")

    state.playwright.chromium.connect_over_cdp.assert_not_awaited()


@pytest.mark.asyncio
async def test_attach_rejects_unknown_context_mode():
    state, _ = _state_with_browser([_fake_context()])

    with pytest.raises(ValueError, match="Invalid --context"):
        await state.get_or_create_session("s", cdp_endpoint="http://cdp", context_mode="bogus")


@pytest.mark.asyncio
async def test_attach_command_passes_context_option_through(no_page_info):
    host = _fake_context([_fake_page()])
    state, _ = _state_with_browser([host])

    response = await _run(state, "attach", session="s", cdp="http://cdp", context="host")

    assert response["success"] is True
    assert state.sessions["s"].context is host


# -- one Session per Host Context ----------------------------------------------


@pytest.mark.asyncio
async def test_second_session_cannot_use_the_same_host_context_under_another_spelling(monkeypatch):
    async def same_chrome(endpoint, headers):
        return "browser-guid-1"

    monkeypatch.setattr(daemon, "_resolve_host_key", same_chrome)
    state, _ = _state_with_browser([_fake_context([_fake_page()])])
    await _host_session(state, "first", "http://localhost:9222")

    with pytest.raises(ValueError, match="already uses this Host Context"):
        await _host_session(state, "second", "http://127.0.0.1:9222/")

    assert state.playwright.chromium.connect_over_cdp.await_count == 1


@pytest.mark.asyncio
async def test_concurrent_host_attaches_cannot_both_win():
    state, _ = _state_with_browser([_fake_context([_fake_page()])])
    connect = state.playwright.chromium.connect_over_cdp
    browser = connect.return_value

    async def slow_connect(*args, **kwargs):
        await asyncio.sleep(0.01)
        return browser

    connect.side_effect = slow_connect

    results = await asyncio.gather(_host_session(state, "a"), _host_session(state, "b"), return_exceptions=True)

    assert sum(isinstance(r, ValueError) for r in results) == 1
    assert len(state.sessions) == 1


@pytest.mark.asyncio
async def test_isolated_sessions_can_share_a_host_with_a_host_context_session():
    state, _ = _state_with_browser([_fake_context([_fake_page()])])
    await _host_session(state, "host")

    isolated = await state.get_or_create_session("iso", cdp_endpoint="http://cdp")

    assert isolated.uses_host_context is False


# -- stale Sessions after the Host restarts Chrome -----------------------------


@pytest.mark.asyncio
async def test_reattach_by_name_replaces_a_stale_session_and_says_so(no_page_info):
    state, old_browser = _state_with_browser([_fake_context([_fake_page()])])
    stale = await _host_session(state)
    old_browser.is_connected.return_value = False
    fresh_host = _fake_context([_fake_page()])
    state.playwright.chromium.connect_over_cdp = AsyncMock(return_value=_fake_browser([fresh_host]))

    response = await _run(state, "attach", session="s", cdp="http://cdp", context="host")

    assert response["success"] is True
    assert state.sessions["s"] is not stale
    assert state.sessions["s"].context is fresh_host
    assert "Replaced stale session 's'" in response["output"]


@pytest.mark.asyncio
async def test_a_stale_session_on_the_same_host_does_not_block_a_new_name():
    state, old_browser = _state_with_browser([_fake_context([_fake_page()])])
    await _host_session(state, "old")
    old_browser.is_connected.return_value = False
    state.playwright.chromium.connect_over_cdp = AsyncMock(return_value=_fake_browser([_fake_context([_fake_page()])]))

    session = await _host_session(state, "new")

    assert list(state.sessions) == ["new"]
    assert any("Replaced stale session 'old'" in n for n in session.attach_notices)


# -- reattaching an open Session -------------------------------------------------


@pytest.mark.asyncio
async def test_reattach_with_the_same_mode_returns_the_open_session():
    state, _ = _state_with_browser([_fake_context([_fake_page()])])
    first = await _host_session(state)

    assert await _host_session(state) is first


@pytest.mark.asyncio
async def test_reattach_with_a_different_mode_is_refused_not_ignored():
    state, _ = _state_with_browser([_fake_context([_fake_page()])])
    await state.get_or_create_session("s", cdp_endpoint="http://cdp")

    with pytest.raises(ValueError, match="already attached .* --context=new"):
        await _host_session(state)


@pytest.mark.asyncio
async def test_attach_onto_a_launched_session_name_is_refused():
    state, _ = _state_with_browser([])
    launched = MagicMock(spec=daemon.Session)
    launched.name = "s"
    launched.browser = None
    launched.is_attached = False
    state.sessions["s"] = launched

    with pytest.raises(ValueError, match="started with `open`"):
        await _host_session(state)


# -- whose Pages we may close ----------------------------------------------------


@pytest.mark.asyncio
async def test_tab_close_refuses_a_host_page(no_page_info):
    host_page = _fake_page()
    state, _ = _state_with_browser([_fake_context([host_page])])
    await _host_session(state)

    response = await _run(state, "tab-close", ["0"], session="s")

    assert response["success"] is False
    assert "belongs to the Host" in response["output"]
    host_page.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_tab_close_allows_a_page_we_opened(no_page_info):
    state, _ = _state_with_browser([_fake_context([_fake_page()])])
    session = await _host_session(state)
    ours = await session.new_page()
    session.pages.append(ours)

    response = await _run(state, "tab-close", ["1"], session="s")

    assert response["success"] is True, response
    ours.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_popups_of_our_pages_are_ours_and_popups_of_host_pages_are_not():
    host_page = _fake_page()
    state, _ = _state_with_browser([_fake_context([host_page])])
    session = await _host_session(state)
    ours = await session.new_page()

    our_popup = _fake_page(opener=ours)
    host_popup = _fake_page(opener=host_page)
    await session._on_new_page(our_popup)
    await session._on_new_page(host_popup)

    assert session.may_close_page(our_popup)
    assert not session.may_close_page(host_popup)


# -- ending a Host Context Session -----------------------------------------------


@pytest.mark.asyncio
async def test_close_refuses_the_default_tab_of_a_host_session():
    host_page = _fake_page()
    state, browser = _state_with_browser([_fake_context([host_page])])
    await _host_session(state)

    response = await _run(state, "close", session="s")

    assert response["success"] is False
    assert "detach" in response["output"]
    assert "s" in state.sessions
    host_page.close.assert_not_awaited()
    browser.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_close_of_a_named_tab_in_a_host_session_keeps_the_session_attached():
    state, browser = _state_with_browser([_fake_context([_fake_page()])])
    session = await _host_session(state)
    ours = (await session.open_tab("worker")).page

    response = await _run(state, "close", session="s", tab="worker")

    assert response["success"] is True, response
    ours.close.assert_awaited_once()
    assert "s" in state.sessions
    browser.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_detach_closes_every_page_we_opened_and_no_host_page():
    host_page = _fake_page()
    host = _fake_context([host_page])
    state, browser = _state_with_browser([host])
    session = await _host_session(state)
    named = (await session.open_tab("worker")).page
    from_tab_new = await session.new_page()
    session.pages.append(from_tab_new)

    response = await _run(state, "detach", session="s")

    assert response["success"] is True
    named.close.assert_awaited_once()
    from_tab_new.close.assert_awaited_once()
    host_page.close.assert_not_awaited()
    host.close.assert_not_awaited()
    browser.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_detach_closes_the_page_attach_created_in_an_empty_host():
    host = _fake_context([])
    state, _ = _state_with_browser([host])
    session = await _host_session(state)
    created = session.page

    await _run(state, "detach", session="s")

    created.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_bulk_close_of_a_host_session_detaches_and_keeps_host_pages():
    host_page = _fake_page()
    host = _fake_context([host_page])
    state, browser = _state_with_browser([host])
    session = await _host_session(state)
    ours = (await session.open_tab("worker")).page

    assert await state.close_session("s") is True

    host.close.assert_not_awaited()
    host_page.close.assert_not_awaited()
    ours.close.assert_awaited_once()
    browser.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_closing_an_isolated_session_closes_its_context_and_disconnects():
    state, browser = _state_with_browser([_fake_context()])
    session = await state.get_or_create_session("s", cdp_endpoint="http://cdp")

    await state.close_session("s")

    session.context.close.assert_awaited_once()
    browser.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_kill_all_leaves_the_host_context_alone():
    host_page = _fake_page()
    host = _fake_context([host_page])
    state, browser = _state_with_browser([host])
    await _host_session(state)

    response = await _run(state, "kill-all", session="s")

    assert response["success"] is True
    host.close.assert_not_awaited()
    host_page.close.assert_not_awaited()
    browser.close.assert_awaited_once()
    assert state.sessions == {}


@pytest.mark.asyncio
async def test_delete_data_refuses_an_attached_session():
    state, browser = _state_with_browser([_fake_context([_fake_page()])])
    await _host_session(state)

    response = await _run(state, "delete-data", session="s")

    assert response["success"] is False
    assert "belongs to the Host" in response["output"]
    assert "s" in state.sessions
    browser.close.assert_not_awaited()


# -- identifying the Host ----------------------------------------------------------


@pytest.mark.asyncio
async def test_host_key_reads_the_browser_id_from_a_ws_endpoint(monkeypatch):
    monkeypatch.undo()  # use the real resolver
    key = await daemon._resolve_host_key("ws://127.0.0.1:9222/devtools/browser/abc-123", None)
    assert key == "abc-123"


@pytest.mark.asyncio
async def test_host_key_falls_back_to_the_endpoint_when_chrome_cannot_be_asked(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(daemon.asyncio, "to_thread", AsyncMock(side_effect=OSError("refused")))
    key = await daemon._resolve_host_key("http://127.0.0.1:9/", None)
    assert key == "http://127.0.0.1:9"
