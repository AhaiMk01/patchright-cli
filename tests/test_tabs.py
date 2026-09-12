"""Unit tests for per-tab session state."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from patchright_cli.daemon import DEFAULT_TAB, Session, Tab


def _fake_page(url="https://example.com"):
    page = MagicMock()
    page.url = url
    page.is_closed = MagicMock(return_value=False)
    page.close = AsyncMock()
    page.goto = AsyncMock()
    page.title = AsyncMock(return_value="Example")
    page.bring_to_front = AsyncMock()
    return page


def _session(pages=None):
    context = MagicMock()
    context.new_page = AsyncMock(side_effect=lambda: _fake_page())
    return Session("default", context, pages if pages is not None else [_fake_page()])


def test_new_session_has_one_default_tab():
    session = _session()
    assert list(session.tabs) == [DEFAULT_TAB]
    assert session.active_tab.name == DEFAULT_TAB


def test_default_tab_follows_the_index_based_selection():
    first, second = _fake_page("https://a.test"), _fake_page("https://b.test")
    session = _session([first, second])

    assert session.page is first
    session.current_tab = 1
    assert session.page is second


@pytest.mark.asyncio
async def test_named_tab_gets_its_own_page():
    session = _session()
    default_page = session.page

    tab = await session.open_tab("inbox")

    assert tab.name == "inbox"
    assert tab.page is not default_page
    assert tab.page in session.pages


@pytest.mark.asyncio
async def test_open_tab_is_idempotent():
    session = _session()
    first = await session.open_tab("inbox")
    second = await session.open_tab("inbox")
    assert first is second
    assert session.context.new_page.await_count == 1


@pytest.mark.asyncio
async def test_opening_a_named_tab_does_not_move_the_default_tab():
    first, second = _fake_page("https://a.test"), _fake_page("https://b.test")
    session = _session([first, second])
    session.current_tab = 1

    await session.open_tab("inbox")

    assert session.current_tab == 1
    assert session.page is second


@pytest.mark.asyncio
async def test_named_tab_keeps_its_own_refs_and_history():
    session = _session()
    session.ref_registry = "default-refs"
    session.push_history("https://a.test")

    await session.open_tab("inbox")
    session.activate_tab("inbox")

    assert session.ref_registry is None
    session.ref_registry = "inbox-refs"
    session.push_history("https://b.test")

    session.activate_tab(DEFAULT_TAB)
    assert session.ref_registry == "default-refs"
    assert session._history == ["https://a.test"]

    session.activate_tab("inbox")
    assert session.ref_registry == "inbox-refs"
    assert session._history == ["https://b.test"]


@pytest.mark.asyncio
async def test_named_tab_is_not_moved_by_tab_select():
    session = _session()
    await session.open_tab("inbox")
    session.activate_tab("inbox")
    inbox_page = session.page

    session.current_tab = 0
    assert session.page is inbox_page


@pytest.mark.asyncio
async def test_codegen_buffer_is_per_tab():
    session = _session()
    await session.open_tab("inbox")

    session._codegen = ["default-cmd"]
    session.activate_tab("inbox")
    assert session._codegen is None

    session.activate_tab(DEFAULT_TAB)
    assert session._codegen == ["default-cmd"]


@pytest.mark.asyncio
async def test_close_tab_frees_only_that_tab():
    session = _session()
    tab = await session.open_tab("inbox")
    inbox_page = tab.page

    remaining = await session.close_tab("inbox")

    inbox_page.close.assert_awaited_once()
    assert remaining is True
    assert "inbox" not in session.tabs
    assert inbox_page not in session.pages


@pytest.mark.asyncio
async def test_closing_a_named_tab_falls_back_to_the_default():
    session = _session()
    await session.open_tab("inbox")
    session.activate_tab("inbox")

    await session.close_tab("inbox")

    assert session.active_tab.name == DEFAULT_TAB


@pytest.mark.asyncio
async def test_closing_the_last_tab_reports_nothing_remaining():
    session = _session()
    assert await session.close_tab(DEFAULT_TAB) is False


@pytest.mark.asyncio
async def test_close_tab_that_does_not_exist_raises():
    session = _session()
    with pytest.raises(KeyError):
        await session.close_tab("nope")


def test_activating_an_unknown_tab_raises():
    session = _session()
    with pytest.raises(KeyError):
        session.activate_tab("nope")


def test_tab_state_starts_empty():
    tab = Tab("a")
    assert tab.page is None
    assert tab.ref_registry is None
    assert tab.codegen is None
    assert tab.history == []
    assert tab.history_index == -1


# -- Dispatch routing --------------------------------------------------------


@pytest.fixture
def state_with_session():
    from patchright_cli.daemon import DaemonState

    state = MagicMock(spec=DaemonState)
    state.profile_dirs = {}
    state.shutdown_event = None
    state.idle_timeout = 1800.0
    session = _session()
    state.sessions = {"default": session}
    state.close_session = AsyncMock(return_value=True)
    return state, session


@pytest.mark.asyncio
async def test_command_routes_to_the_named_tab(state_with_session):
    from patchright_cli.daemon import handle_command

    state, session = state_with_session
    inbox = await session.open_tab("inbox")

    response = await handle_command(state, {"command": "url", "args": [], "options": {"tab": "inbox"}})

    assert response["success"] is True
    assert session.active_tab is inbox


@pytest.mark.asyncio
async def test_command_for_an_unopened_tab_is_an_error(state_with_session):
    from patchright_cli.daemon import handle_command

    state, session = state_with_session

    response = await handle_command(state, {"command": "url", "args": [], "options": {"tab": "ghost"}})

    assert response["success"] is False
    assert "ghost" in response["output"]


@pytest.mark.asyncio
async def test_tab_option_does_not_reach_the_handler(state_with_session):
    from patchright_cli.daemon import handle_command

    state, session = state_with_session
    inbox = await session.open_tab("inbox")
    inbox.page.screenshot = AsyncMock()

    response = await handle_command(
        state, {"command": "screenshot", "args": [], "options": {"tab": "inbox"}, "cwd": None}
    )

    assert response["success"] is True
    assert "tab" not in inbox.page.screenshot.await_args.kwargs


@pytest.mark.asyncio
async def test_close_frees_only_the_addressed_tab(state_with_session):
    from patchright_cli.daemon import handle_command

    state, session = state_with_session
    tab = await session.open_tab("inbox")
    inbox_page = tab.page

    response = await handle_command(state, {"command": "close", "args": [], "options": {"tab": "inbox"}})

    assert response["success"] is True
    inbox_page.close.assert_awaited_once()
    assert "inbox" not in session.tabs
    state.close_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_closing_the_last_tab_closes_the_session(state_with_session):
    from patchright_cli.daemon import handle_command

    state, session = state_with_session

    response = await handle_command(state, {"command": "close", "args": [], "options": {}})

    assert response["success"] is True
    state.close_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_tab_list_names_the_owning_tab(state_with_session):
    from patchright_cli.daemon import handle_command

    state, session = state_with_session
    await session.open_tab("inbox")

    response = await handle_command(state, {"command": "tab-list", "args": [], "options": {}})

    assert response["success"] is True
    assert "inbox" in response["output"]
