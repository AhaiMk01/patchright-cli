"""Unit tests for AXI daemon behaviours: idempotent no-ops, explicit empty states, structured `data`."""

from functools import partial
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from patchright_cli.daemon import DaemonState, Session, handle_command


@pytest.fixture
def mock_state():
    state = MagicMock(spec=DaemonState)
    state.sessions = {}
    state.profile_dirs = {}
    state.shutdown_event = None
    return state


@pytest.fixture
def mock_session():
    session = MagicMock(spec=Session)
    session.name = "default"
    session.pages = []
    session.current_tab = 0
    session.ref_registry = None
    session.context = MagicMock()
    session.context.cookies = AsyncMock(return_value=[])
    session.context.clear_cookies = AsyncMock()
    session.context.add_cookies = AsyncMock()
    session.context.tracing.start = AsyncMock()
    session.context.tracing.stop = AsyncMock()
    session.page = MagicMock()
    session.page.url = "https://example.com/"
    session.page.title = AsyncMock(return_value="Example")
    session.page.evaluate = AsyncMock()
    session.page.route = AsyncMock()
    session.page.unroute = AsyncMock()
    session._codegen = None
    session._video_recording = False
    session._video_cdp = None
    session._video_show_actions = None
    session._tracing = False
    session._routes = {}
    session.console_messages = []
    session.network_log = []
    session.is_attached = False
    session.tabs = {"default": MagicMock()}
    session.activate_tab = MagicMock()
    session.uses_host_context = False
    session.network_guard = None
    session.goto = partial(Session.goto, session)
    session.check_navigation = partial(Session.check_navigation, session)
    return session


async def _run(state, command, args=None, **options):
    return await handle_command(state, {"command": command, "args": args or [], "options": options})


def _assert_noop(response):
    assert response["success"] is True
    assert response["output"].endswith(" (no-op)")


# -- Idempotent mutations ------------------------------------------------------


@pytest.mark.asyncio
async def test_video_stop_without_recording_is_a_noop(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    _assert_noop(await _run(mock_state, "video-stop"))


@pytest.mark.asyncio
async def test_video_start_while_recording_is_a_noop(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session._video_recording = True
    _assert_noop(await _run(mock_state, "video-start"))


@pytest.mark.asyncio
async def test_video_hide_actions_when_off_is_a_noop(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    _assert_noop(await _run(mock_state, "video-hide-actions"))


@pytest.mark.asyncio
async def test_tracing_stop_without_tracing_is_a_noop(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    _assert_noop(await _run(mock_state, "tracing-stop"))
    mock_session.context.tracing.stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_tracing_start_twice_is_a_noop(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    first = await _run(mock_state, "tracing-start")
    assert first == {"success": True, "output": "Tracing started."}
    assert mock_session._tracing is True
    _assert_noop(await _run(mock_state, "tracing-start"))
    mock_session.context.tracing.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_tracing_stop_saves_and_resets(mock_state, mock_session, tmp_path):
    mock_state.sessions = {"default": mock_session}
    mock_session._tracing = True
    response = await handle_command(mock_state, {"command": "tracing-stop", "args": [], "cwd": str(tmp_path)})
    assert response["success"] is True
    assert "Tracing saved to" in response["output"]
    mock_session.context.tracing.stop.assert_awaited_once()
    assert mock_session._tracing is False


@pytest.mark.asyncio
async def test_unroute_unknown_pattern_is_a_noop(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    _assert_noop(await _run(mock_state, "unroute", ["**/*.png"]))
    _assert_noop(await _run(mock_state, "unroute"))
    mock_session.page.unroute.assert_not_awaited()


@pytest.mark.asyncio
async def test_route_then_unroute(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    await _run(mock_state, "route", ["**/api"], status="404")
    assert mock_session._routes["**/api"][1] == 404
    response = await _run(mock_state, "unroute", ["**/api"])
    assert response == {"success": True, "output": "Route removed: **/api"}
    mock_session.page.unroute.assert_awaited_once()
    assert mock_session._routes == {}


@pytest.mark.asyncio
async def test_route_same_pattern_replaces_previous_handler(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    await _run(mock_state, "route", ["**/api"])
    first_handler = mock_session._routes["**/api"][0]
    await _run(mock_state, "route", ["**/api"], status="500")
    mock_session.page.unroute.assert_awaited_once_with("**/api", first_handler)
    assert mock_session._routes["**/api"][1] == 500


@pytest.mark.asyncio
async def test_cookie_delete_absent_is_a_noop(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session.context.cookies.return_value = [{"name": "other", "value": "1", "domain": "x"}]
    _assert_noop(await _run(mock_state, "cookie-delete", ["sid"]))
    mock_session.context.clear_cookies.assert_not_awaited()


@pytest.mark.asyncio
async def test_cookie_delete_present_removes_it(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    keep = {"name": "other", "value": "1", "domain": "x"}
    mock_session.context.cookies.return_value = [{"name": "sid", "value": "2", "domain": "x"}, keep]
    response = await _run(mock_state, "cookie-delete", ["sid"])
    assert response == {"success": True, "output": "Cookie 'sid' deleted."}
    mock_session.context.add_cookies.assert_awaited_once_with([keep])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "store"), [("localstorage-delete", "localStorage"), ("sessionstorage-delete", "sessionStorage")]
)
async def test_storage_delete_absent_key_is_a_noop(mock_state, mock_session, command, store):
    mock_state.sessions = {"default": mock_session}
    mock_session.page.evaluate.return_value = False
    response = await _run(mock_state, command, ["token"])
    _assert_noop(response)
    assert store in response["output"]


@pytest.mark.asyncio
async def test_storage_delete_present_key(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session.page.evaluate.return_value = True
    response = await _run(mock_state, "localstorage-delete", ["token"])
    assert response == {"success": True, "output": "localStorage['token'] deleted."}


@pytest.mark.asyncio
async def test_highlight_hide_with_nothing_highlighted_is_a_noop(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session.page.evaluate.return_value = 0
    _assert_noop(await _run(mock_state, "highlight", hide=True))


@pytest.mark.asyncio
async def test_highlight_hide_on_unhighlighted_ref_is_a_noop(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    locator = MagicMock()
    locator.evaluate = AsyncMock(return_value=False)
    mock_session.ref_registry = MagicMock()
    mock_session.ref_registry.resolve.return_value = locator
    _assert_noop(await _run(mock_state, "highlight", ["e3"], hide=True))


@pytest.mark.asyncio
async def test_close_absent_session_is_a_noop(mock_state):
    response = await _run(mock_state, "close", session="ghost")
    _assert_noop(response)
    assert "Session 'ghost' is not open" in response["output"]


@pytest.mark.asyncio
async def test_close_absent_tab_is_a_noop(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    _assert_noop(await _run(mock_state, "close", tab="inbox"))


@pytest.mark.asyncio
async def test_detach_absent_session_is_a_noop(mock_state):
    _assert_noop(await _run(mock_state, "detach", session="ghost"))


@pytest.mark.asyncio
async def test_other_commands_on_absent_session_still_fail(mock_state):
    response = await _run(mock_state, "snapshot", session="ghost")
    assert response["success"] is False
    assert "Session 'ghost' is not open" in response["output"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("command", "method"), [("go-back", "go_back"), ("go-forward", "go_forward")])
async def test_history_edge_fails_with_actionable_message(mock_state, mock_session, command, method):
    mock_state.sessions = {"default": mock_session}
    setattr(mock_session, method, AsyncMock(return_value=None))
    response = await _run(mock_state, command)
    assert response["success"] is False
    assert "goto <url>" in response["output"]


# -- Empty states ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_without_sessions(mock_state):
    response = await _run(mock_state, "list")
    assert response["output"] == "### Sessions\n  0 sessions open (`open [url]` starts one)"
    assert response["data"] == []


@pytest.mark.asyncio
async def test_tab_list_without_pages(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    response = await _run(mock_state, "tab-list")
    assert "0 tabs in this session" in response["output"]
    assert response["data"] == []


@pytest.mark.asyncio
async def test_cookie_list_empty(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    response = await _run(mock_state, "cookie-list")
    assert response["output"] == "0 cookies in this session"
    assert response["data"] == []
    filtered = await _run(mock_state, "cookie-list", domain="example.com")
    assert filtered["output"] == "0 cookies in this session matching --domain=example.com"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "store"), [("localstorage-list", "localStorage"), ("sessionstorage-list", "sessionStorage")]
)
async def test_storage_list_empty(mock_state, mock_session, command, store):
    mock_state.sessions = {"default": mock_session}
    mock_session.page.evaluate.return_value = "{}"
    response = await _run(mock_state, command)
    assert response == {"success": True, "output": f"0 {store} keys on this page", "data": []}


@pytest.mark.asyncio
async def test_route_list_empty(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    response = await _run(mock_state, "route-list")
    assert response == {"success": True, "output": "0 active routes in this session", "data": []}


@pytest.mark.asyncio
async def test_console_empty(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    assert (await _run(mock_state, "console"))["output"] == "0 console messages in this session"
    filtered = await _run(mock_state, "console", ["error"])
    assert filtered["output"] == "0 console messages in this session at level 'error'"
    assert filtered["data"] == []


@pytest.mark.asyncio
async def test_network_empty_mentions_hidden_static(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session.network_log = [{"id": 0, "method": "GET", "url": "https://x/a.png", "resource": "image"}]
    response = await _run(mock_state, "network")
    assert response["output"].startswith("0 network requests in this session")
    assert "--static" in response["output"]
    assert response["data"] == []
    with_static = await _run(mock_state, "network", static=True)
    assert with_static["data"] == [{"id": 0, "method": "GET", "status": None, "url": "https://x/a.png"}]


# -- Structured data --------------------------------------------------------------


@pytest.mark.asyncio
async def test_url_and_title_data(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    assert (await _run(mock_state, "url"))["data"] == {"url": "https://example.com/"}
    assert (await _run(mock_state, "title"))["data"] == {"title": "Example"}


@pytest.mark.asyncio
async def test_list_data(mock_state):
    s = MagicMock()
    s.pages = [MagicMock()]
    s.page.url = "https://a.test/"
    s.is_attached = False
    attached = MagicMock()
    attached.pages = []
    attached.page = None
    attached.is_attached = True
    mock_state.sessions = {"default": s, "remote": attached}
    response = await _run(mock_state, "list")
    assert response["data"] == [
        {"name": "default", "status": "open", "url": "https://a.test/"},
        {"name": "remote", "status": "attached", "url": ""},
    ]
    assert "default *: 1 tab(s)" in response["output"]


@pytest.mark.asyncio
async def test_tab_list_data(mock_state, mock_session):
    other = MagicMock()
    other.url = "https://b.test/"
    other.title = AsyncMock(return_value="B")
    mock_session.pages = [mock_session.page, other]
    mock_session.tabs = {"default": MagicMock(page=None)}
    mock_state.sessions = {"default": mock_session}
    response = await _run(mock_state, "tab-list")
    assert response["data"] == [
        {"index": 0, "title": "Example", "url": "https://example.com/", "active": True},
        {"index": 1, "title": "B", "url": "https://b.test/", "active": False},
    ]
    assert response["output"].startswith("### Tabs\n  [0] * https://example.com/")


@pytest.mark.asyncio
async def test_cookie_list_data_keeps_json_output(mock_state, mock_session):
    import json

    cookies = [{"name": "sid", "value": "s3cret", "domain": ".a.test", "path": "/", "expires": -1}]
    mock_session.context.cookies.return_value = cookies
    mock_state.sessions = {"default": mock_session}
    response = await _run(mock_state, "cookie-list")
    assert json.loads(response["output"]) == cookies
    assert response["data"] == [{"name": "sid", "domain": ".a.test", "expires": -1}]


@pytest.mark.asyncio
async def test_storage_list_data_keeps_json_output(mock_state, mock_session):
    mock_session.page.evaluate.return_value = '{"a":"1","b":"x"}'
    mock_state.sessions = {"default": mock_session}
    response = await _run(mock_state, "localstorage-list")
    assert response["output"] == '{"a":"1","b":"x"}'
    assert response["data"] == [{"key": "a", "value": "1"}, {"key": "b", "value": "x"}]


@pytest.mark.asyncio
async def test_route_list_data(mock_state, mock_session):
    mock_session._routes = {"**/api": (MagicMock(), 404)}
    mock_state.sessions = {"default": mock_session}
    response = await _run(mock_state, "route-list")
    assert response["output"] == "### Active Routes\n  - **/api"
    assert response["data"] == [{"pattern": "**/api", "status": 404}]


@pytest.mark.asyncio
async def test_console_data_and_total_when_truncated(mock_state, mock_session):
    mock_session.console_messages = [{"type": "log", "text": f"m{i}"} for i in range(60)]
    mock_state.sessions = {"default": mock_session}
    response = await _run(mock_state, "console")
    assert len(response["data"]) == 50
    assert response["data"][0] == {"level": "log", "text": "m10"}
    assert response["total"] == 60


@pytest.mark.asyncio
async def test_console_without_truncation_has_no_total(mock_state, mock_session):
    mock_session.console_messages = [{"type": "warning", "text": "careful"}]
    mock_state.sessions = {"default": mock_session}
    response = await _run(mock_state, "console", ["warn"])
    assert response["output"] == "[warning] careful"
    assert response["data"] == [{"level": "warning", "text": "careful"}]
    assert "total" not in response


@pytest.mark.asyncio
async def test_network_data_and_total(mock_state, mock_session):
    mock_session.network_log = [
        {"id": i, "method": "GET", "url": f"https://x/{i}", "resource": "fetch", "status": 200} for i in range(55)
    ]
    mock_state.sessions = {"default": mock_session}
    response = await _run(mock_state, "network")
    assert response["data"][-1] == {"id": 54, "method": "GET", "status": 200, "url": "https://x/54"}
    assert len(response["data"]) == 50
    assert response["total"] == 55


@pytest.fixture
def find_session(mock_session):
    from patchright_cli.ref_registry import RefRegistry

    registry = RefRegistry()
    registry.parse('- banner:\n  - link "Sign in"\n  - link "Sign up"\n')
    mock_session.ref_registry = registry
    return mock_session


@pytest.mark.asyncio
async def test_find_data_and_total(mock_state, find_session):
    mock_state.sessions = {"default": find_session}
    registry = find_session.ref_registry
    with patch("patchright_cli.daemon.take_snapshot", AsyncMock(return_value=("ignored", registry))):
        response = await _run(mock_state, "find", ["Sign"], limit="1")
    assert response["data"] == [{"ref": "e2", "role": "link", "name": "Sign in"}]
    assert response["total"] == 2


@pytest.mark.asyncio
async def test_find_empty_page(mock_state, find_session):
    from patchright_cli.ref_registry import RefRegistry

    mock_state.sessions = {"default": find_session}
    empty = RefRegistry()
    with patch("patchright_cli.daemon.take_snapshot", AsyncMock(return_value=("", empty))):
        response = await _run(mock_state, "find", ["x"])
    assert response["success"] is True
    assert "0 accessible elements" in response["output"]
    assert response["data"] == []


def test_responses_encode_as_toon():
    from patchright_cli.toon import encode

    response = {"success": True, "output": "0 active routes in this session", "data": []}
    assert encode(response) == "success: true\noutput: 0 active routes in this session\ndata: []"


# ---------------------------------------------------------------------------
# Daemon spawn: no visible console window on Windows
# ---------------------------------------------------------------------------


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="Windows creation flags")
def test_daemon_spawn_uses_a_hidden_console_on_windows():
    # DETACHED_PROCESS leaves a venv launcher console-less, so the real interpreter it
    # starts opens a visible window; CREATE_NO_WINDOW gives a hidden console instead.
    import socket
    import subprocess

    from patchright_cli import daemon

    started = []

    class FakeSocket:
        def settimeout(self, _):
            pass

        def connect(self, _):
            if not started:
                raise ConnectionRefusedError

        def close(self):
            pass

    with (
        patch.object(socket, "socket", lambda *a: FakeSocket()),
        patch.object(subprocess, "Popen", side_effect=lambda *a, **k: started.append(k)),
        patch("time.sleep"),
    ):
        assert daemon.ensure_daemon_running(port=1, headless=True) is True

    flags = started[0]["creationflags"]
    assert flags & subprocess.CREATE_NO_WINDOW
    assert not flags & subprocess.DETACHED_PROCESS
