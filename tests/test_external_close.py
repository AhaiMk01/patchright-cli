"""Issue #44: pages that close or crash outside the daemon leave the session."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from patchright_cli.daemon import DaemonState, Session, handle_command


class _Page:
    """A page whose `close` and `crash` events the test fires, as Chrome would."""

    def __init__(self, url: str):
        self.url = url
        self._handlers: dict[str, list] = {}
        self.title = AsyncMock(return_value=url)
        self.bring_to_front = AsyncMock()
        self.close = AsyncMock()
        self.aria_snapshot = AsyncMock(return_value=f'- heading "{url}" [ref=e1]')

    def on(self, event, handler):
        self._handlers.setdefault(event, []).append(handler)

    def remove_listener(self, event, handler):
        self._handlers.get(event, []).remove(handler)

    def is_closed(self) -> bool:
        return False

    def fire(self, event: str) -> None:
        for handler in list(self._handlers.get(event, [])):
            handler(self)


async def _state(*urls: str) -> tuple[DaemonState, Session, list[_Page]]:
    pages = [_Page(u) for u in urls]
    session = Session("default", MagicMock(), list(pages))
    await session.setup_listeners()
    state = MagicMock(spec=DaemonState)
    state.sessions = {"default": session}
    state.shutdown_event = None
    state.get_or_create_session = AsyncMock(return_value=session)
    return state, session, pages


async def _run(state, command, args=None, **options):
    return await handle_command(state, {"command": command, "args": args or [], "options": options})


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["close", "crash"])
async def test_a_page_gone_outside_the_daemon_leaves_tab_list(event):
    state, session, (a, b, c) = await _state("https://a.test", "https://b.test", "https://c.test")
    session.current_tab = 2
    a.fire(event)
    listed = (await _run(state, "tab-list"))["data"]
    assert [t["url"] for t in listed] == ["https://b.test", "https://c.test"]
    assert (await _run(state, "title"))["output"] == "https://c.test"


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["close", "crash"])
async def test_the_default_tab_does_not_slide_onto_a_neighbour(event):
    state, session, (a, b) = await _state("https://a.test", "https://b.test")
    session.current_tab = 1
    b.fire(event)
    reply = await _run(state, "title")
    assert reply["success"] is False and reply["code"] == "target_closed"
    a.title.assert_not_awaited()
    assert (await _run(state, "tab-select", ["0"]))["success"] is True
    assert (await _run(state, "title"))["output"] == "https://a.test"


@pytest.mark.asyncio
async def test_a_named_tab_whose_page_closed_outside_is_not_open():
    state, session, _ = await _state("https://a.test")
    session.context.new_page = AsyncMock(return_value=_Page("https://inbox.test"))
    tab = await session.open_tab("inbox")
    await session._attach_page_listeners(tab.page)
    tab.page.fire("close")
    reply = await _run(state, "title", tab="inbox")
    assert reply["success"] is False and reply["code"] == "tab_not_open"
    assert (await _run(state, "title"))["output"] == "https://a.test"


@pytest.mark.asyncio
async def test_the_last_page_gone_outside_leaves_an_open_session_without_pages():
    state, session, (a,) = await _state("https://a.test")
    a.fire("close")
    assert state.sessions["default"] is session
    assert (await _run(state, "title"))["code"] == "no_page"
    assert (await _run(state, "tab-list"))["data"] == []


@pytest.mark.asyncio
async def test_tab_close_still_moves_the_default_tab_to_the_next_page():
    state, session, (a, b) = await _state("https://a.test", "https://b.test")
    session.current_tab = 1
    assert (await _run(state, "tab-close", ["1"]))["success"] is True
    b.fire("close")
    assert (await _run(state, "title"))["output"] == "https://a.test"


def _page_event_handler(session):
    """The context `page` handler setup_listeners registered, as Chrome's page event calls it."""
    return next(call.args[1] for call in session.context.on.call_args_list if call.args[0] == "page")


@pytest.mark.asyncio
async def test_opening_a_named_tab_keeps_the_default_tab_off_a_neighbour():
    state, session, (a, b) = await _state("https://a.test", "https://b.test")
    session.current_tab = 1
    b.fire("close")
    inbox = _Page("https://inbox.test")

    async def new_page():
        _page_event_handler(session)(inbox)  # Chrome emits the page event before new_page resolves
        await asyncio.sleep(0)
        return inbox

    session.context.new_page = new_page
    assert (await _run(state, "open", tab="inbox"))["success"] is True
    reply = await _run(state, "title")
    assert reply["code"] == "target_closed"
    a.title.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_popup_does_not_become_the_default_tab_while_its_page_is_gone():
    state, session, (a, b) = await _state("https://a.test", "https://b.test")
    session.current_tab = 1
    b.fire("close")
    _page_event_handler(session)(_Page("https://popup.test"))
    await asyncio.sleep(0)
    assert (await _run(state, "title"))["code"] == "target_closed"


@pytest.mark.asyncio
async def test_a_running_command_stays_on_its_tab_when_another_request_drops_it():
    state, session, (a,) = await _state("https://a.test")
    inbox = _Page("https://inbox.test")
    session.context.new_page = AsyncMock(return_value=inbox)
    await _run(state, "open", tab="inbox")
    await session._attach_page_listeners(inbox)
    release = asyncio.Event()

    async def slow_reload():
        await release.wait()

    inbox.reload = slow_reload
    running = asyncio.ensure_future(_run(state, "reload", tab="inbox"))
    await asyncio.sleep(0)
    inbox.fire("close")
    assert (await _run(state, "title", tab="inbox"))["code"] == "tab_not_open"
    release.set()
    reply = await running
    assert "https://inbox.test" in reply["output"]
    assert "https://a.test" not in reply["output"]


@pytest.mark.asyncio
async def test_a_gone_named_tab_does_not_keep_the_browser_open():
    _, session, _ = await _state("https://a.test")
    session.default_in_use = False
    session.context.new_page = AsyncMock(side_effect=[_Page("https://one.test"), _Page("https://two.test")])
    one, two = await session.open_tab("one"), await session.open_tab("two")
    await session._attach_page_listeners(two.page)
    two.page.fire("close")
    assert await session.close_tab("one") is False


@pytest.mark.asyncio
async def test_tab_close_by_index_works_while_the_default_page_is_gone():
    state, session, (a, b, c) = await _state("https://a.test", "https://b.test", "https://c.test")
    session.current_tab = 2
    c.fire("close")
    refused = await _run(state, "tab-close")
    assert refused["success"] is False and refused["code"] == "target_closed"
    b.close.assert_not_awaited()
    assert (await _run(state, "tab-close", ["0"]))["success"] is True
    a.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_crashed_page_is_closed_when_the_session_may_close_it():
    _, session, (a, b) = await _state("https://a.test", "https://b.test")
    a.fire("crash")
    await asyncio.sleep(0)
    a.close.assert_awaited_once()
    assert session.pages == [b]


@pytest.mark.asyncio
async def test_a_page_that_closed_before_the_session_saw_it_is_not_tracked():
    _, session, (a,) = await _state("https://a.test")
    gone = _Page("https://popup.test")
    gone.is_closed = lambda: True
    _page_event_handler(session)(gone)
    await asyncio.sleep(0)
    assert session.pages == [a]


@pytest.mark.asyncio
async def test_open_on_a_default_tab_whose_page_is_gone_says_so():
    state, session, (a, b) = await _state("https://a.test", "https://b.test")
    session.current_tab = 1
    b.fire("close")
    reply = await _run(state, "open")
    assert reply["success"] is False and reply["code"] == "target_closed"
