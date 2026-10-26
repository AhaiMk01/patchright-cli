"""Browserless tests for `--block-networks`."""

import ipaddress
from unittest.mock import AsyncMock, MagicMock

import pytest

from patchright_cli import daemon
from patchright_cli.daemon import NetworkGuard


@pytest.mark.parametrize(
    ("address", "link_local", "private"),
    [
        ("169.254.169.254", True, True),
        ("fe80::1", True, True),
        ("::ffff:169.254.169.254", True, True),
        ("127.0.0.1", False, True),
        ("::1", False, True),
        ("10.1.2.3", False, True),
        ("172.16.0.1", False, True),
        ("192.168.1.20", False, True),
        ("100.64.0.1", False, True),
        ("fd00::1", False, True),
        ("0.0.0.0", False, True),
        ("::ffff:10.0.0.1", False, True),
        ("64:ff9b::a9fe:a9fe", True, True),
        ("64:ff9b::5db8:d70e", False, False),
        ("93.184.215.14", False, False),
        ("2606:2800:21f:cb07:6820:80da:af6b:8b2c", False, False),
    ],
)
def test_blocked_address(address, link_local, private):
    ip = ipaddress.ip_address(address)
    assert daemon._blocked_address(ip, "link-local") is link_local
    assert daemon._blocked_address(ip, "private") is private


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError, match="block-networks"):
        NetworkGuard("everything")


async def test_guard_checks_every_resolved_address(monkeypatch):
    loop = MagicMock()
    loop.getaddrinfo = AsyncMock(
        return_value=[(None, None, None, "", ("93.184.215.14", 0)), (None, None, None, "", ("169.254.169.254", 0))]
    )
    monkeypatch.setattr(daemon.asyncio, "get_running_loop", lambda: loop)
    guard = NetworkGuard("link-local")
    assert await guard.blocks("metadata.example.test") is True
    assert await guard.blocks("metadata.example.test") is True
    loop.getaddrinfo.assert_awaited_once()


async def test_unresolvable_host_is_left_to_chrome(monkeypatch):
    loop = MagicMock()
    loop.getaddrinfo = AsyncMock(side_effect=OSError("no such host"))
    monkeypatch.setattr(daemon.asyncio, "get_running_loop", lambda: loop)
    assert await NetworkGuard("private").blocks("nowhere.invalid") is False


async def test_check_url_refuses_before_connecting():
    guard = NetworkGuard("link-local")
    with pytest.raises(ValueError, match="CDP endpoint"):
        await guard.check_url("http://169.254.169.254:9222", "CDP endpoint")
    await guard.check_url("http://127.0.0.1:9222", "CDP endpoint")


async def test_frames_landing_in_a_blocked_range_go_blank():
    context = MagicMock(pages=[MagicMock()])
    NetworkGuard("private").install(context)
    page = context.pages[0]
    event, enforce = page.on.call_args.args
    assert event == "framenavigated"
    context.on.assert_called_once()
    assert context.route.call_count == 0  # a route would turn the HTTP cache off

    blocked = MagicMock(url="http://10.0.0.5/admin", goto=AsyncMock())
    await enforce(blocked)
    blocked.goto.assert_awaited_once_with("about:blank")

    allowed = MagicMock(url="https://93.184.215.14/", goto=AsyncMock())
    await enforce(allowed)
    allowed.goto.assert_not_awaited()


async def test_none_mode_blocks_nothing():
    assert await NetworkGuard("none").blocks("169.254.169.254") is False


BACKSLASH = "\\"


@pytest.mark.parametrize(
    ("url", "host"),
    [
        # Spellings where urlparse and Chrome's WHATWG parser disagree.
        (f"http://169.254.169.254{BACKSLASH}@93.184.215.14/", "169.254.169.254"),
        ("http://%31%36%39.254.169.254/", "169.254.169.254"),
        ("http:169.254.169.254", "169.254.169.254"),
        (f"http:{BACKSLASH}{BACKSLASH}169.254.169.254/", "169.254.169.254"),
        ("http://169。254。169。254/", "169.254.169.254"),
        ("http://１６９.254.169.254/", "169.254.169.254"),
        ("http://0xA9FEA9FE/", "169.254.169.254"),
        ("http://2852039166/", "169.254.169.254"),
        ("http://0251.0376.0251.0376/", "169.254.169.254"),
        ("http://169.254.43518/", "169.254.169.254"),
        (" http://169.254.169.254/", "169.254.169.254"),
        # Ordinary URLs keep their host.
        ("HTTP://user:pw@Example.COM:8080/x", "example.com"),
        ("http://[::ffff:a9fe:a9fe]:80/", "::ffff:a9fe:a9fe"),
        ("http://münchen.de/", "xn--mnchen-3ya.de"),
        ("socks5://10.0.0.1:1080", "10.0.0.1"),
        ("about:blank", None),
        ("data:text/html,x", None),
    ],
)
def test_browser_host_matches_chrome(url, host):
    assert daemon._browser_host(url) == host


async def test_check_url_sees_through_parser_tricks():
    guard = NetworkGuard("link-local")
    for url in [f"http://169.254.169.254{BACKSLASH}@93.184.215.14/", "http:169.254.169.254", "http://0xA9FEA9FE/"]:
        with pytest.raises(ValueError, match="refused"):
            await guard.check_url(url, "URL")


async def test_failed_escape_is_logged(caplog):
    frame = MagicMock(url="http://10.0.0.5/", goto=AsyncMock(side_effect=RuntimeError("navigation interrupted")))
    await NetworkGuard("private")._enforce(frame)
    assert "Could not leave http://10.0.0.5/" in caplog.text


def _cdp_state(monkeypatch, ws_url, contexts=None):
    async def version(endpoint, headers, timeout=5):
        return {"webSocketDebuggerUrl": ws_url}

    async def host_key(endpoint, headers):
        return endpoint

    monkeypatch.setattr(daemon, "_fetch_cdp_version", version)
    monkeypatch.setattr(daemon, "_resolve_host_key", host_key)
    page = MagicMock(url="about:blank", goto=AsyncMock(), close=AsyncMock())
    page.opener = AsyncMock(return_value=None)
    context = MagicMock(pages=[page])
    context.new_cdp_session = AsyncMock(side_effect=RuntimeError("no CDP in unit tests"))
    context.close = AsyncMock()
    browser = MagicMock(contexts=contexts if contexts is not None else [context])
    browser.new_context = AsyncMock(return_value=context)
    browser.close = AsyncMock()
    browser.is_connected = MagicMock(return_value=True)
    state = daemon.DaemonState()
    state.playwright = MagicMock(devices={})
    state.playwright.chromium.connect_over_cdp = AsyncMock(return_value=browser)
    return state, context


async def test_guarded_attach_checks_the_discovered_websocket(monkeypatch):
    state, _ = _cdp_state(monkeypatch, "ws://169.254.169.254/devtools/browser/x")
    with pytest.raises(ValueError, match="CDP WebSocket"):
        await state.get_or_create_session("s", cdp_endpoint="http://127.0.0.1:9222", block_networks="link-local")
    state.playwright.chromium.connect_over_cdp.assert_not_awaited()


async def test_guarded_attach_connects_to_the_checked_websocket(monkeypatch):
    ws = "ws://127.0.0.1:9222/devtools/browser/x"
    state, _ = _cdp_state(monkeypatch, ws)
    await state.get_or_create_session("s", cdp_endpoint="http://127.0.0.1:9222", block_networks="link-local")
    assert state.playwright.chromium.connect_over_cdp.await_args.args[0] == ws


async def test_host_context_keeps_url_checks_without_a_listener(monkeypatch):
    state, context = _cdp_state(monkeypatch, "ws://127.0.0.1:9222/devtools/browser/x")
    session = await state.get_or_create_session(
        "s", cdp_endpoint="http://127.0.0.1:9222", context_mode="host", block_networks="link-local"
    )
    assert session.network_guard is not None
    assert all(c.args[0] != "framenavigated" for c in context.pages[0].on.call_args_list)
    with pytest.raises(ValueError, match="refused"):
        await session.network_guard.check_url("http://169.254.169.254/", "URL")


async def test_reopening_with_another_mode_is_refused():
    state = daemon.DaemonState()
    existing = MagicMock(browser=None, profile=None, uses_host_context=False)
    existing.network_guard = NetworkGuard("link-local")
    state.sessions["s"] = existing
    assert await state.get_or_create_session("s", block_networks="link-local") is existing
    with pytest.raises(ValueError, match="close"):
        await state.get_or_create_session("s", block_networks="private")
