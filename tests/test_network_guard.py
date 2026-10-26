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
