"""Issue #44: pages that close or crash outside the daemon leave the session."""

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
