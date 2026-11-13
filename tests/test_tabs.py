"""Unit tests for per-tab session state."""

import asyncio
import contextvars
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
    session.last_raw = "default-refs"
    session.push_history("https://a.test")

    await session.open_tab("inbox")
    session.activate_tab("inbox")

    assert session.last_raw is None
    session.last_raw = "inbox-refs"
    session.push_history("https://b.test")

    session.activate_tab(DEFAULT_TAB)
    assert session.last_raw == "default-refs"
    assert session._history == ["https://a.test"]

    session.activate_tab("inbox")
    assert session.last_raw == "inbox-refs"
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
async def test_closing_a_named_tab_leaves_new_requests_on_the_default():
    session = _session()
    await session.open_tab("inbox")
    session.activate_tab("inbox")

    await session.close_tab("inbox")

    # Each request runs in a fresh context; the closing task itself keeps its tab.
    assert contextvars.Context().run(lambda: session.active_tab.name) == DEFAULT_TAB
    assert session.active_tab.name == "inbox"


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
    assert tab.last_raw is None
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
    default_page = session.pages[0]
    default_page.screenshot = AsyncMock()
    inbox = await session.open_tab("inbox")
    inbox.page.screenshot = AsyncMock()

    response = await handle_command(
        state, {"command": "screenshot", "args": [], "options": {"tab": "inbox"}, "cwd": None}
    )

    assert response["success"] is True
    # cmd_screenshot never forwards **options, so asserting "tab" is absent from
    # its kwargs could not fail. Assert what actually matters instead: the
    # command ran against the addressed tab's page, not the default one.
    inbox.page.screenshot.assert_awaited_once()
    default_page.screenshot.assert_not_awaited()


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
async def test_plain_close_closes_the_session(state_with_session):
    from patchright_cli.daemon import handle_command

    state, session = state_with_session

    response = await handle_command(state, {"command": "close", "args": [], "options": {}})

    assert response["success"] is True
    state.close_session.assert_awaited_once()


# -- Issue #57: `close` is the session, `--tab <name> close` is one tab ---------


async def _close(state, **options):
    from patchright_cli.daemon import handle_command

    return await handle_command(state, {"command": "close", "args": [], "options": options})


@pytest.mark.asyncio
async def test_plain_close_closes_the_session_despite_named_tabs(state_with_session):
    state, session = state_with_session
    await session.open_tab("work")

    response = await _close(state)

    assert response["success"] is True
    state.close_session.assert_awaited_once_with("default")
    assert response["output"] == "Session 'default' closed."


@pytest.mark.asyncio
async def test_close_addressed_to_the_default_tab_by_name_closes_the_session(state_with_session):
    state, session = state_with_session
    await session.open_tab("work")

    response = await _close(state, tab=DEFAULT_TAB)

    state.close_session.assert_awaited_once_with("default")
    assert response["output"] == "Session 'default' closed."


@pytest.mark.asyncio
async def test_named_close_says_which_tab_closed_and_how_many_remain(state_with_session):
    from patchright_cli.daemon import handle_command

    state, session = state_with_session
    await session.open_tab("work")
    await session.open_tab("mail")

    response = await _close(state, tab="work")

    state.close_session.assert_not_awaited()
    assert "Tab 'work' closed" in response["output"]
    # default + mail: the same pages tab-list shows.
    assert "2 tab(s) still in use in session 'default'" in response["output"]
    assert "unused" not in response["output"]
    listing = await handle_command(state, {"command": "tab-list", "args": [], "options": {}})
    assert len(listing["data"]) == 2


@pytest.mark.asyncio
async def test_named_close_of_the_last_tab_in_use_says_the_session_closed(state_with_session):
    state, session = state_with_session
    session.default_in_use = False  # opened only through --tab
    await session.open_tab("work")

    response = await _close(state, tab="work")

    state.close_session.assert_awaited_once_with("default")
    assert "Tab 'work' closed" in response["output"]
    assert "last tab in use" in response["output"]
    assert "session 'default' closed" in response["output"]


@pytest.mark.asyncio
async def test_named_close_keeps_a_session_whose_default_tab_was_used_after_a_tab_only_open(state_with_session):
    from patchright_cli.daemon import handle_command

    state, session = state_with_session
    session.default_in_use = False  # opened only through --tab
    await session.open_tab("work")
    session.pages[0].evaluate = AsyncMock(return_value=1)
    await handle_command(state, {"command": "tab-new", "args": [], "options": {}})

    response = await _close(state, tab="work")

    state.close_session.assert_not_awaited()
    assert "2 tab(s) still in use" in response["output"]
    assert "unused" not in response["output"]


@pytest.mark.asyncio
async def test_two_named_tabs_in_a_tab_only_session_close_one_then_the_other(state_with_session):
    from patchright_cli.daemon import handle_command

    state, session = state_with_session
    session.default_in_use = False  # opened only through --tab
    await session.open_tab("a")
    await session.open_tab("b")

    first = await _close(state, tab="a")

    state.close_session.assert_not_awaited()
    # tab-list still shows two pages, but only b is in use: the other is the launch page.
    listing = await handle_command(state, {"command": "tab-list", "args": [], "options": {}})
    assert len(listing["data"]) == 2
    assert "Tab 'a' closed; 1 tab(s) still in use in session 'default'" in first["output"]
    assert "plus 1 unused page(s)" in first["output"]
    assert "launch page" in first["output"]
    assert "2 tab(s)" not in first["output"]

    second = await _close(state, tab="b")

    state.close_session.assert_awaited_once_with("default")
    assert second["output"] == "Tab 'b' closed; it was the last tab in use, so session 'default' closed."


@pytest.mark.asyncio
async def test_after_tab_close_removes_the_default_page_only_named_tabs_keep_the_session(state_with_session):
    from patchright_cli.daemon import handle_command

    state, session = state_with_session  # default tab in use
    await session.open_tab("work")
    removed = await handle_command(state, {"command": "tab-close", "args": ["0"], "options": {}})
    assert removed["success"] is True

    response = await _close(state, tab="work")

    state.close_session.assert_awaited_once_with("default")
    assert "last tab in use" in response["output"]


def test_every_registered_command_is_classified_by_whether_it_uses_the_page():
    # A new @register command must be added to one of the two sets (issue #57):
    # the set decides whether running it without --tab puts the default tab in use.
    from patchright_cli import daemon

    assert not daemon._PAGE_COMMANDS & daemon._SESSION_WIDE
    assert set(daemon.COMMAND_HANDLERS) == daemon._PAGE_COMMANDS | daemon._SESSION_WIDE


def _session_wide_commands():
    from patchright_cli import daemon

    return sorted(getattr(daemon, "_SESSION_WIDE", ["missing-_SESSION_WIDE"]))


@pytest.mark.asyncio
@pytest.mark.parametrize("command", _session_wide_commands())
async def test_a_session_wide_command_does_not_put_the_default_tab_in_use(state_with_session, command):
    from patchright_cli.daemon import COMMAND_HANDLERS, handle_command

    state, session = state_with_session
    session.default_in_use = False  # opened only through --tab
    await session.open_tab("work")
    assert command in COMMAND_HANDLERS

    # Whether it succeeds on these mocks does not matter: it never uses the tab.
    await handle_command(state, {"command": command, "args": [], "options": {}})

    assert session.default_in_use is False
    await _close(state, tab="work")
    state.close_session.assert_awaited_once_with("default")


@pytest.mark.asyncio
async def test_the_requests_alias_does_not_put_the_default_tab_in_use(state_with_session):
    from patchright_cli.daemon import handle_command

    state, session = state_with_session
    session.default_in_use = False
    reply = await handle_command(state, {"command": "requests", "args": [], "options": {}})

    assert reply["success"] is True
    assert session.default_in_use is False


@pytest.mark.asyncio
async def test_a_page_command_puts_the_default_tab_in_use(state_with_session):
    from patchright_cli.daemon import handle_command

    state, session = state_with_session
    session.default_in_use = False
    reply = await handle_command(state, {"command": "url", "args": [], "options": {}})

    assert reply["success"] is True
    assert session.default_in_use is True


@pytest.mark.asyncio
async def test_a_page_command_addressed_to_a_named_tab_leaves_the_default_tab_unused(state_with_session):
    from patchright_cli.daemon import handle_command

    state, session = state_with_session
    session.default_in_use = False
    await session.open_tab("work")
    reply = await handle_command(state, {"command": "url", "args": [], "options": {"tab": "work"}})

    assert reply["success"] is True
    assert session.default_in_use is False


@pytest.mark.asyncio
async def test_a_command_refused_on_a_gone_default_page_does_not_put_it_in_use(state_with_session):
    from patchright_cli.daemon import handle_command

    state, session = state_with_session
    session.default_in_use = False
    await session.open_tab("work")
    session._current_gone = True  # the default tab's page closed outside the daemon

    reply = await handle_command(state, {"command": "url", "args": [], "options": {}})

    assert reply["success"] is False
    assert session.default_in_use is False


@pytest.mark.asyncio
async def test_a_command_refused_with_no_page_does_not_put_the_default_tab_in_use(state_with_session):
    from patchright_cli.daemon import handle_command

    state, session = state_with_session
    session.default_in_use = False
    session.pages.clear()

    reply = await handle_command(state, {"command": "url", "args": [], "options": {}})

    assert reply["success"] is False
    assert session.default_in_use is False


@pytest.mark.asyncio
async def test_a_tab_new_that_fails_does_not_put_the_default_tab_in_use(state_with_session):
    from patchright_cli.daemon import handle_command

    state, session = state_with_session
    session.default_in_use = False
    session.context.new_page = AsyncMock(side_effect=RuntimeError("no page for you"))

    reply = await handle_command(state, {"command": "tab-new", "args": [], "options": {}})

    assert reply["success"] is False
    assert session.default_in_use is False


@pytest.mark.asyncio
async def test_a_page_command_whose_page_dies_under_it_does_not_put_the_default_tab_in_use(state_with_session):
    from patchright_cli.daemon import handle_command

    state, session = state_with_session
    session.default_in_use = False
    page = session.pages[0]

    async def dies():
        session._forget_page(page, outside=True)
        raise RuntimeError("Target page, context or browser has been closed")

    page.title = AsyncMock(side_effect=dies)

    reply = await handle_command(state, {"command": "title", "args": [], "options": {}})

    assert reply["success"] is False
    assert session.default_in_use is False


@pytest.mark.asyncio
async def test_close_tab_refuses_the_default_tab():
    # The default tab pins no page, so "closing" it closed nothing (issue #57).
    session = _session()
    with pytest.raises(ValueError):
        await session.close_tab(DEFAULT_TAB)


@pytest.mark.asyncio
async def test_a_named_tab_is_the_last_one_when_no_page_is_left():
    session = _session(pages=[])
    await session.open_tab("work")

    assert await session.close_tab("work") is False


@pytest.mark.asyncio
async def test_tab_list_names_the_owning_tab(state_with_session):
    from patchright_cli.daemon import handle_command

    state, session = state_with_session
    await session.open_tab("inbox")

    response = await handle_command(state, {"command": "tab-list", "args": [], "options": {}})

    assert response["success"] is True
    assert "inbox" in response["output"]


# -- Concurrency isolation ---------------------------------------------------


@pytest.mark.asyncio
async def test_two_tasks_do_not_share_the_active_tab():
    """The bug this guards: active_tab was one mutable field shared by every
    connection task, so a command that suspended came back writing into
    whichever tab had been activated in the meantime."""
    session = _session()
    await session.open_tab("alpha")
    await session.open_tab("beta")

    started = asyncio.Event()
    released = asyncio.Event()

    async def slow_alpha():
        session.activate_tab("alpha")
        started.set()
        await released.wait()
        # Post-await write: must land on alpha, not on whoever ran meanwhile.
        session.last_raw = "alpha-refs"
        session.push_history("https://alpha.test")
        return session.last_raw

    async def quick_beta():
        await started.wait()
        session.activate_tab("beta")
        session.last_raw = "beta-refs"
        session.push_history("https://beta.test")
        released.set()
        return session.last_raw

    a, b = await asyncio.gather(slow_alpha(), quick_beta())

    assert a == "alpha-refs"
    assert b == "beta-refs"
    assert session.tabs["alpha"].last_raw == "alpha-refs"
    assert session.tabs["beta"].last_raw == "beta-refs"
    assert session.tabs["alpha"].history == ["https://alpha.test"]
    assert session.tabs["beta"].history == ["https://beta.test"]


@pytest.mark.asyncio
async def test_activation_does_not_leak_across_sessions():
    first = _session()
    second = _session()
    second.name = "other"
    await first.open_tab("inbox")

    first.activate_tab("inbox")
    first.last_raw = "first-inbox"

    # `second` has no tab called inbox; it must fall back to its own default.
    assert second.active_tab.name == DEFAULT_TAB
    assert second.last_raw is None


@pytest.mark.asyncio
async def test_activation_is_scoped_to_the_task_that_set_it():
    session = _session()
    await session.open_tab("inbox")

    async def inner():
        session.activate_tab("inbox")
        return session.active_tab.name

    assert await asyncio.create_task(inner()) == "inbox"
    # The parent task never activated it, so it still sees the default.
    assert session.active_tab.name == DEFAULT_TAB


# -- Default tab lifecycle ---------------------------------------------------


@pytest.mark.asyncio
async def test_an_unused_default_tab_does_not_keep_the_session_alive():
    session = _session()
    session.default_in_use = False
    await session.open_tab("inbox")

    assert await session.close_tab("inbox") is False


@pytest.mark.asyncio
async def test_a_used_default_tab_does_keep_the_session_alive():
    session = _session()
    await session.open_tab("inbox")

    assert await session.close_tab("inbox") is True


@pytest.mark.asyncio
async def test_close_tab_shifts_current_tab_when_an_earlier_page_goes():
    first, second, third = _fake_page("a"), _fake_page("b"), _fake_page("c")
    session = _session([first, second, third])
    session.current_tab = 2
    session.tabs["early"] = Tab("early", first)

    await session.close_tab("early")

    # current_tab must follow the page it pointed at, not silently clamp.
    assert session.page is third
