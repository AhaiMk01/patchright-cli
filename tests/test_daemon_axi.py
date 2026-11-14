"""Unit tests for AXI daemon behaviours: idempotent no-ops, explicit empty states, structured `data`."""

import re
from functools import partial
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from patchright_cli.daemon import DaemonState, Session, handle_command
from tests.ref_helpers import use_refs


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
    session.last_raw = None
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
    session.highlights = {}
    session._video_recording = False
    session._video_cdp = None
    session._video_show_actions = None
    session._tracing = False
    session._routes = {}
    session.console_messages = []
    session.start_console_capture = AsyncMock(return_value=True)
    session._console_capture_enabled = True
    session.network_log = []
    session.is_attached = False
    session.tabs = {"default": MagicMock()}
    session.activate_tab = MagicMock()
    session.uses_host_context = False
    session.network_guard = None
    session.goto = partial(Session.goto, session)
    session.check_navigation = partial(Session.check_navigation, session)
    return session


def _web_storage(session, store):
    """The mocked native `WebStorage` behind `page.local_storage` / `page.session_storage`."""
    attr = "local_storage" if store == "localStorage" else "session_storage"
    storage = getattr(session.page, attr)
    for method in ("items", "get_item", "set_item", "remove_item", "clear"):
        if not isinstance(getattr(storage, method), AsyncMock):
            setattr(storage, method, AsyncMock())
    return storage


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


async def _fulfilled(mock_state, mock_session, **options) -> dict:
    """What a route added with `options` answers a request with."""
    mock_state.sessions = {"default": mock_session}
    reply = await _run(mock_state, "route", ["**/api"], **options)
    assert reply["success"] is True, reply
    request = MagicMock()
    request.fulfill = AsyncMock()
    await mock_session._routes["**/api"][0](request)
    return request.fulfill.await_args.kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("options", "content_type", "headers"),
    [
        ({}, "text/plain", None),
        ({"header": "X-One:1"}, "text/plain", {"X-One": "1"}),
        ({"header": ["X-One:1", "X-Two: 2"]}, "text/plain", {"X-One": "1", "X-Two": "2"}),
        ({"header": ["X-A:1", "x-a:2"]}, "text/plain", {"X-A": "1, 2"}),
        ({"header": "content-type:application/json"}, "application/json", None),
        ({"content-type": "text/html", "header": "X-One:1"}, "text/html", {"X-One": "1"}),
        ({"header": ["X-Test:1", "X-Keep:2"], "remove-header": "x-test"}, "text/plain", {"X-Keep": "2"}),
        ({"header": ["X-Test:1", "X-Keep:2"], "remove-header": "X-TEST, x-keep"}, "text/plain", None),
        ({"header": "Content-Type:application/json", "remove-header": "content-type"}, "text/plain", None),
    ],
)
async def test_route_mock_headers(mock_state, mock_session, options, content_type, headers):
    sent = await _fulfilled(mock_state, mock_session, **options)
    assert (sent["content_type"], sent["headers"]) == (content_type, headers)


@pytest.mark.asyncio
async def test_route_rejects_the_content_type_given_twice(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    reply = await _run(
        mock_state, "route", ["**/api"], **{"content-type": "text/html", "header": ["X-One:1", "Content-Type:a/b"]}
    )
    assert (reply["success"], reply["exit_code"]) == (False, 2)
    assert "route got the Content-Type twice" in reply["output"]
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
    # Only that name is removed: the other cookies are never cleared and re-added.
    mock_session.context.clear_cookies.assert_awaited_once_with(name="sid")
    mock_session.context.add_cookies.assert_not_awaited()


def _cookie_jar(context):
    """Back the mocked context with a jar that clear_cookies filters as the browser does."""
    jar = [
        {"name": "_t", "value": "1", "domain": "www.a.test"},
        {"name": "_t", "value": "2", "domain": "www.b.test"},
        {"name": "theme", "value": "3", "domain": ".a.test"},
    ]

    def matches(cookie, name=None, domain=None):
        if name is not None and cookie["name"] != name:
            return False
        if domain is None:
            return True
        return bool(domain.search(cookie["domain"])) if isinstance(domain, re.Pattern) else domain == cookie["domain"]

    async def clear_cookies(**filters):
        jar[:] = [c for c in jar if not matches(c, **filters)]

    context.cookies = AsyncMock(side_effect=lambda: list(jar))
    context.clear_cookies = AsyncMock(side_effect=clear_cookies)
    return jar


def _left(jar):
    return [(c["name"], c["domain"]) for c in jar]


@pytest.mark.asyncio
async def test_cookie_delete_domain_removes_only_that_site(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    jar = _cookie_jar(mock_session.context)
    response = await _run(mock_state, "cookie-delete", ["_t"], domain="www.a.test")
    assert response == {"success": True, "output": "Cookie '_t' deleted for domain 'www.a.test'."}
    mock_session.context.clear_cookies.assert_awaited_once_with(name="_t", domain="www.a.test")
    assert _left(jar) == [("_t", "www.b.test"), ("theme", ".a.test")]
    mock_session.context.add_cookies.assert_not_awaited()


@pytest.mark.asyncio
async def test_cookie_delete_domain_regex(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    jar = _cookie_jar(mock_session.context)
    response = await _run(mock_state, "cookie-delete", ["_t"], domain=r"/\.test$/")
    assert response["output"] == r"Cookie '_t' deleted for domain '/\.test$/'."
    assert _left(jar) == [("theme", ".a.test")]


@pytest.mark.asyncio
async def test_cookie_delete_domain_without_that_cookie_is_a_noop(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    jar = _cookie_jar(mock_session.context)
    # `theme` exists, but not on this domain.
    _assert_noop(await _run(mock_state, "cookie-delete", ["theme"], domain="www.b.test"))
    assert len(jar) == 3


@pytest.mark.asyncio
async def test_cookie_clear_without_domain_clears_everything(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    response = await _run(mock_state, "cookie-clear")
    assert response == {"success": True, "output": "All cookies cleared."}
    mock_session.context.clear_cookies.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_cookie_clear_domain_is_exact(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    jar = _cookie_jar(mock_session.context)
    response = await _run(mock_state, "cookie-clear", domain="www.a.test")
    assert response == {"success": True, "output": "Cleared 1 cookie for domain 'www.a.test'."}
    mock_session.context.clear_cookies.assert_awaited_once_with(domain="www.a.test")
    assert _left(jar) == [("_t", "www.b.test"), ("theme", ".a.test")]


@pytest.mark.asyncio
async def test_cookie_clear_domain_regex(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    jar = _cookie_jar(mock_session.context)
    response = await _run(mock_state, "cookie-clear", domain=r"/A\.test$/i")
    assert response["output"] == r"Cleared 2 cookies for domain '/A\.test$/i'."
    mock_session.context.clear_cookies.assert_awaited_once_with(domain=re.compile(r"A\.test$", re.IGNORECASE))
    assert _left(jar) == [("_t", "www.b.test")]


@pytest.mark.asyncio
async def test_cookie_clear_domain_reports_what_the_browser_removed(mock_state, mock_session):
    # The browser runs the regex, so a pattern it matches nothing with is a no-op
    # whatever the same pattern would match in Python.
    mock_state.sessions = {"default": mock_session}
    jar = _cookie_jar(mock_session.context)
    mock_session.context.clear_cookies = AsyncMock()
    _assert_noop(await _run(mock_state, "cookie-clear", domain=r"/\Awww\.a\.test\Z/"))
    assert len(jar) == 3


@pytest.mark.asyncio
async def test_cookie_clear_domain_no_match_is_a_noop_naming_near_domains(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    jar = _cookie_jar(mock_session.context)
    response = await _run(mock_state, "cookie-clear", domain="a.test")
    _assert_noop(response)
    assert ".a.test, www.a.test" in response["output"]
    assert len(jar) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("domain", "message"),
    [("/(/", "Invalid --domain regex '/(/'"), ("", "invalid --domain value"), (True, "--domain takes a value")],
)
async def test_cookie_clear_bad_domain_fails(mock_state, mock_session, domain, message):
    mock_state.sessions = {"default": mock_session}
    response = await _run(mock_state, "cookie-clear", domain=domain)
    assert response["success"] is False
    assert message in response["output"]
    mock_session.context.clear_cookies.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "store"), [("localstorage-delete", "localStorage"), ("sessionstorage-delete", "sessionStorage")]
)
async def test_storage_delete_absent_key_is_a_noop(mock_state, mock_session, command, store):
    mock_state.sessions = {"default": mock_session}
    storage = _web_storage(mock_session, store)
    storage.get_item.return_value = None
    response = await _run(mock_state, command, ["token"])
    _assert_noop(response)
    assert store in response["output"]
    storage.remove_item.assert_not_awaited()


@pytest.mark.asyncio
async def test_storage_delete_present_key(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    storage = _web_storage(mock_session, "localStorage")
    storage.get_item.return_value = ""  # an empty value still counts as present
    response = await _run(mock_state, "localstorage-delete", ["token"])
    assert response == {"success": True, "output": "localStorage['token'] deleted."}
    storage.remove_item.assert_awaited_once_with("token")


@pytest.mark.asyncio
@pytest.mark.parametrize("store", ["localStorage", "sessionStorage"])
async def test_storage_get_set_clear_use_native_web_storage(mock_state, mock_session, store):
    mock_state.sessions = {"default": mock_session}
    prefix = store.lower()
    storage = _web_storage(mock_session, store)
    storage.get_item.return_value = "v1"
    assert await _run(mock_state, f"{prefix}-get", ["k1"]) == {"success": True, "output": '"v1"'}
    storage.get_item.assert_awaited_once_with("k1")
    storage.get_item.return_value = None
    assert (await _run(mock_state, f"{prefix}-get", ["nope"]))["output"] == "null"
    assert await _run(mock_state, f"{prefix}-set", ["k1", "v1"]) == {"success": True, "output": f"{store}['k1'] set."}
    storage.set_item.assert_awaited_once_with("k1", "v1")
    assert await _run(mock_state, f"{prefix}-clear") == {"success": True, "output": f"{store} cleared."}
    storage.clear.assert_awaited_once()
    mock_session.page.evaluate.assert_not_called()


@pytest.mark.asyncio
async def test_highlight_hide_with_nothing_highlighted_is_a_noop(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session.page.hide_highlight = AsyncMock()
    _assert_noop(await _run(mock_state, "highlight", hide=True))


@pytest.mark.asyncio
async def test_highlight_hide_on_unhighlighted_ref_is_a_noop(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    locator = MagicMock()
    locator.hide_highlight = AsyncMock()
    use_refs(mock_session, locator)
    _assert_noop(await _run(mock_state, "highlight", ["e3"], hide=True))
    locator.hide_highlight.assert_not_awaited()


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
    _web_storage(mock_session, store).items.return_value = []
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
    unfiltered = await _run(mock_state, "console")
    assert unfiltered["output"] == "0 console messages captured"
    filtered = await _run(mock_state, "console", ["error"])
    assert filtered["output"] == "0 console messages captured at level 'error'"
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
    _web_storage(mock_session, "localStorage").items.return_value = [
        {"name": "a", "value": "1"},
        {"name": "b", "value": 'x"\u00fc'},
    ]
    mock_state.sessions = {"default": mock_session}
    response = await _run(mock_state, "localstorage-list")
    # Same compact text JSON.stringify(localStorage) printed: no spaces, non-ASCII kept as is.
    assert response["output"] == '{"a":"1","b":"x\\"\u00fc"}'
    assert response["data"] == [{"key": "a", "value": "1"}, {"key": "b", "value": 'x"\u00fc'}]


@pytest.mark.asyncio
async def test_route_list_data(mock_state, mock_session):
    mock_session._routes = {"**/api": (MagicMock(), 404)}
    mock_state.sessions = {"default": mock_session}
    response = await _run(mock_state, "route-list")
    assert response["output"] == "### Active Routes\n  - **/api"
    assert response["data"] == [{"pattern": "**/api", "status": 404}]


@pytest.mark.asyncio
async def test_console_data_and_total_when_truncated(mock_state, mock_session):
    mock_session.console_messages = [{"type": "log", "text": f"m{i}", "timestamp": 1000.0 + i} for i in range(60)]
    mock_state.sessions = {"default": mock_session}
    response = await _run(mock_state, "console")
    assert len(response["data"]) == 50
    assert response["data"][0] == {"level": "log", "text": "m10", "timestamp": 1010.0}
    assert response["total"] == 60


@pytest.mark.asyncio
async def test_console_without_truncation_has_no_total(mock_state, mock_session):
    mock_session.console_messages = [{"type": "warning", "text": "careful", "timestamp": 1762000000123.5}]
    mock_state.sessions = {"default": mock_session}
    response = await _run(mock_state, "console", ["warn"])
    assert response["output"] == "[warning] careful"
    assert response["data"] == [{"level": "warning", "text": "careful", "timestamp": 1762000000123.5}]
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


FIND_RAW = '- banner [ref=e1]:\n  - link "Sign in" [ref=e2]\n  - link "Sign up" [ref=e3]\n'


@pytest.mark.asyncio
async def test_find_data_and_total(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    with patch("patchright_cli.daemon.ai_snapshot", AsyncMock(return_value=FIND_RAW)):
        response = await _run(mock_state, "find", ["Sign"], limit="1")
    assert response["data"] == [{"ref": "e2", "role": "link", "name": "Sign in"}]
    assert response["total"] == 2


@pytest.mark.asyncio
async def test_find_empty_page(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    with patch("patchright_cli.daemon.ai_snapshot", AsyncMock(return_value="")):
        response = await _run(mock_state, "find", ["x"])
    assert response["success"] is True
    assert "0 accessible elements" in response["output"]
    assert response["data"] == []


def test_responses_encode_as_toon():
    from patchright_cli.toon import encode

    response = {"success": True, "output": "0 active routes in this session", "data": []}
    assert encode(response) == "success: true\noutput: 0 active routes in this session\ndata: []"
