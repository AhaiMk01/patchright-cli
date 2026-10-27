"""Browserless tests for `--web-only` and the navigations the daemon starts itself."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from patchright_cli import daemon
from patchright_cli.daemon import NetworkGuard, Session, handle_command


def _page(url="http://example.test/"):
    return MagicMock(url=url, goto=AsyncMock())


def _session(guard=None, page=None):
    session = Session("s", MagicMock(), [page or _page()])
    session.network_guard = guard
    return session


@pytest.mark.parametrize("url", ["http://example.test/", "HTTPS://example.test/a?b", "about:blank"])
async def test_web_only_allows_web_urls(url):
    await NetworkGuard("none", web_only=True).check_navigation(url)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "FILE:///C:/Users/secret.txt",
        " file:///etc/passwd",
        "fi\tle:///etc/passwd",
        "chrome://settings",
        "view-source:file:///etc/passwd",
        "data:text/html,x",
        "javascript:1",
        "about:version",
        "/relative/path",
    ],
)
async def test_web_only_refuses_other_schemes(url):
    with pytest.raises(ValueError, match="--web-only"):
        await NetworkGuard("none", web_only=True).check_navigation(url)


async def test_web_only_still_applies_the_network_guard():
    guard = NetworkGuard("link-local", web_only=True)
    with pytest.raises(ValueError, match="block-networks"):
        await guard.check_navigation("http://169.254.169.254/latest/meta-data/")


async def test_network_guard_alone_leaves_file_urls_alone():
    await NetworkGuard("link-local").check_navigation("file:///tmp/page.html")


async def test_session_goto_refuses_before_navigating():
    page = _page()
    session = _session(NetworkGuard("none", web_only=True), page)
    with pytest.raises(ValueError, match="--web-only"):
        await session.goto(page, "file:///etc/passwd")
    page.goto.assert_not_awaited()
    await session.goto(page, "https://example.test/")
    page.goto.assert_awaited_once_with("https://example.test/")


async def test_unrestricted_session_goes_anywhere():
    page = _page()
    await _session(None, page).goto(page, "file:///etc/passwd")
    page.goto.assert_awaited_once_with("file:///etc/passwd")


@pytest.mark.parametrize("direction", ["go_back", "go_forward"])
async def test_history_replay_is_checked(direction):
    page = _page()
    session = _session(NetworkGuard("none", web_only=True), page)
    session.push_history("file:///etc/passwd")
    session.push_history("http://example.test/")
    session.push_history("file:///etc/shadow")
    session._history_index = 1
    with pytest.raises(ValueError, match="--web-only"):
        await getattr(session, direction)()
    page.goto.assert_not_awaited()
    assert session._history_index == 1  # a refused step leaves the position alone


async def test_history_replay_navigates_when_allowed():
    page = _page()
    session = _session(NetworkGuard("none", web_only=True), page)
    session.push_history("http://a.test/")
    session.push_history("http://b.test/")
    assert await session.go_back() == "http://a.test/"
    assert await session.go_forward() == "http://b.test/"
    assert [c.args[0] for c in page.goto.await_args_list] == ["http://a.test/", "http://b.test/"]


async def _click_link(href, guard):
    page = _page()
    session = _session(guard, page)
    session.network_guard = guard
    elem = MagicMock(evaluate=AsyncMock(return_value=href), click=AsyncMock())
    registry = MagicMock()
    registry.resolve.return_value = elem
    session.ref_registry = registry
    state = daemon.DaemonState()
    state.sessions["s"] = session
    with patch("patchright_cli.daemon.take_snapshot", new_callable=AsyncMock) as snap:
        snap.return_value = ("snapshot", registry)
        with patch("patchright_cli.daemon.save_snapshot", return_value="/tmp/snap.yml"):
            response = await handle_command(state, {"command": "click", "args": ["e1"], "options": {"session": "s"}})
    return response, page, elem


async def test_click_does_not_follow_a_file_link_in_a_web_only_session():
    response, page, elem = await _click_link("file:///etc/passwd", NetworkGuard("none", web_only=True))
    assert response["success"] is False
    assert "--web-only" in response["output"]
    page.goto.assert_not_awaited()
    elem.click.assert_not_awaited()


async def test_click_checks_the_link_against_the_network_guard():
    response, page, _ = await _click_link("http://169.254.169.254/", NetworkGuard("link-local"))
    assert response["success"] is False
    page.goto.assert_not_awaited()


async def test_click_follows_a_file_link_without_the_option():
    response, page, _ = await _click_link("file:///tmp/page.html", None)
    assert response["success"] is True
    page.goto.assert_awaited_once_with("file:///tmp/page.html")


async def test_frames_landing_off_the_web_go_blank():
    context = MagicMock(pages=[MagicMock()])
    NetworkGuard("none", web_only=True).install(context)
    _, enforce = context.pages[0].on.call_args.args
    assert context.route.call_count == 0  # a route would turn the HTTP cache off

    for url in ["file:///etc/passwd", "chrome://settings/", "view-source:http://a.test/", "filesystem:http://a/x"]:
        frame = MagicMock(url=url, goto=AsyncMock())
        await enforce(frame)
        frame.goto.assert_awaited_once_with("about:blank")

    for url in [
        "https://example.test/",
        "about:blank",
        "about:srcdoc",
        "data:text/html,x",
        "blob:https://example.test/1234",
        "chrome-error://chromewebdata/",
        "",
    ]:
        frame = MagicMock(url=url, goto=AsyncMock())
        await enforce(frame)
        frame.goto.assert_not_awaited()


async def test_backstop_ignores_schemes_without_the_option():
    frame = MagicMock(url="file:///tmp/page.html", goto=AsyncMock())
    await NetworkGuard("private")._enforce(frame)
    frame.goto.assert_not_awaited()


async def test_web_only_alone_creates_a_guard(monkeypatch):
    state = daemon.DaemonState()
    state.playwright = MagicMock(devices={})
    page = _page("about:blank")
    page.opener = AsyncMock(return_value=None)
    context = MagicMock(pages=[page])
    context.new_cdp_session = AsyncMock(side_effect=RuntimeError("no CDP in unit tests"))
    monkeypatch.setattr(state, "_launch", AsyncMock(return_value=context))
    monkeypatch.setattr(state, "_claim_profile", MagicMock())
    with pytest.raises(ValueError, match="--web-only"):
        await state.get_or_create_session("s", url="file:///etc/passwd", web_only=True)
    session = await state.get_or_create_session("s", web_only=True)
    assert session.network_guard is not None and session.network_guard.web_only
    assert session.network_guard.mode == "none"
    assert any(c.args[0] == "framenavigated" for c in page.on.call_args_list)


async def test_plain_session_has_no_guard(monkeypatch):
    state = daemon.DaemonState()
    state.playwright = MagicMock(devices={})
    context = MagicMock(pages=[_page("about:blank")])
    context.new_cdp_session = AsyncMock(side_effect=RuntimeError("no CDP in unit tests"))
    monkeypatch.setattr(state, "_launch", AsyncMock(return_value=context))
    monkeypatch.setattr(state, "_claim_profile", MagicMock())
    session = await state.get_or_create_session("s", url="file:///tmp/page.html")
    assert session.network_guard is None
    context.pages[0].goto.assert_awaited_once_with("file:///tmp/page.html")


async def test_reopening_with_another_policy_is_refused():
    state = daemon.DaemonState()
    existing = MagicMock(browser=None, profile=None, uses_host_context=False)
    existing.network_guard = NetworkGuard("link-local", web_only=True)
    state.sessions["s"] = existing
    assert await state.get_or_create_session("s", block_networks="link-local", web_only=True) is existing
    with pytest.raises(ValueError, match="--block-networks=link-local --web-only"):
        await state.get_or_create_session("s", block_networks="link-local")


async def test_host_context_web_only_checks_urls_without_a_listener(monkeypatch):
    async def host_key(endpoint, headers):
        return endpoint

    monkeypatch.setattr(daemon, "_resolve_host_key", host_key)
    host_page = _page("file:///host/own/page.html")
    host_page.opener = AsyncMock(return_value=None)
    context = MagicMock(pages=[host_page])
    context.new_cdp_session = AsyncMock(side_effect=RuntimeError("no CDP in unit tests"))
    browser = MagicMock(contexts=[context])
    state = daemon.DaemonState()
    state.playwright = MagicMock(devices={})
    state.playwright.chromium.connect_over_cdp = AsyncMock(return_value=browser)
    session = await state.get_or_create_session(
        "s", cdp_endpoint="ws://127.0.0.1:9222/devtools/browser/x", context_mode="host", web_only=True
    )
    assert all(c.args[0] != "framenavigated" for c in host_page.on.call_args_list)
    host_page.goto.assert_not_awaited()
    with pytest.raises(ValueError, match="--web-only"):
        await session.goto(host_page, "file:///etc/passwd")


@pytest.mark.parametrize("command", ["open", "attach"])
async def test_open_and_attach_pass_the_option_on(command):
    state = MagicMock(spec=daemon.DaemonState)
    state.sessions = {}
    session = MagicMock(page=None, attach_notices=[])
    state.get_or_create_session = AsyncMock(return_value=session)
    options = {"web-only": True, "cdp": "http://127.0.0.1:9222"}
    with patch("patchright_cli.daemon._page_info", new=AsyncMock(return_value={"success": True, "output": ""})):
        await handle_command(state, {"command": command, "args": [], "options": options})
    assert state.get_or_create_session.await_args.kwargs["web_only"] is True


async def test_named_tab_refuses_before_opening_a_page():
    state = MagicMock(spec=daemon.DaemonState)
    state.sessions = {}
    session = _session(NetworkGuard("none", web_only=True))
    session.open_tab = AsyncMock()
    state.get_or_create_session = AsyncMock(return_value=session)
    response = await handle_command(
        state, {"command": "open", "args": ["file:///etc/passwd"], "options": {"tab": "agent", "web-only": True}}
    )
    assert response["success"] is False
    session.open_tab.assert_not_awaited()


async def test_tab_new_refuses_before_opening_a_page():
    session = _session(NetworkGuard("none", web_only=True))
    session.new_page = AsyncMock()
    state = daemon.DaemonState()
    state.sessions["s"] = session
    response = await handle_command(
        state, {"command": "tab-new", "args": ["file:///etc/passwd"], "options": {"session": "s"}}
    )
    assert response["success"] is False
    session.new_page.assert_not_awaited()
