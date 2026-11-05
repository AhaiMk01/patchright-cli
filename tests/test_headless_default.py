"""A daemon started with --headless keeps that default for opens that don't say (issue #49)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from patchright_cli import daemon
from patchright_cli.daemon import DaemonState, handle_command
from tests.test_profiles import _fake_context


@pytest.fixture
def launch_state(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon, "PROFILES_ROOT", tmp_path / "profiles")
    monkeypatch.setattr(daemon, "_page_info", AsyncMock(return_value={"success": True, "output": ""}))
    st = DaemonState()
    st.playwright = MagicMock()
    st.playwright.devices = {}
    st.playwright.chromium.launch_persistent_context = AsyncMock(side_effect=lambda *a, **k: _fake_context())
    return st


async def _open_headless(state, default_headless, **options):
    state.default_headless = default_headless
    reply = await handle_command(state, {"command": "open", "args": [], "options": {"session": "s", **options}})
    assert reply["success"], reply
    return state.playwright.chromium.launch_persistent_context.await_args.kwargs["headless"]


@pytest.mark.asyncio
@pytest.mark.parametrize("default", [True, False])
async def test_open_without_headless_uses_the_daemon_default(launch_state, default):
    assert await _open_headless(launch_state, default) is default


@pytest.mark.asyncio
async def test_open_headed_overrides_a_headless_daemon(launch_state):
    assert await _open_headless(launch_state, True, headed=True) is False


@pytest.mark.asyncio
async def test_open_headless_on_a_headed_daemon(launch_state):
    assert await _open_headless(launch_state, False, headless=True) is True


@pytest.mark.asyncio
async def test_explicit_headless_false_beats_a_headless_daemon(launch_state):
    # A config file can send "headless": false; that is a choice, not an absence.
    assert await _open_headless(launch_state, True, headless=False) is False
