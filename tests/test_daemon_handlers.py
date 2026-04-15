"""Unit tests for daemon command dispatch."""

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
    session.page = MagicMock()
    session.page.goto = AsyncMock()
    session.page.url = "https://example.com"
    session.page.title = AsyncMock(return_value="Example")
    return session


@pytest.mark.asyncio
async def test_unknown_command(mock_state):
    mock_state.sessions = {"default": MagicMock()}
    response = await handle_command(mock_state, {"command": "not-a-cmd", "args": []})
    assert response["success"] is False
    assert "Unknown command" in response["output"]


@pytest.mark.asyncio
async def test_list_sessions(mock_state):
    s1 = MagicMock()
    s1.pages = [MagicMock()]
    mock_state.sessions = {"default": s1, "work": s1}
    response = await handle_command(mock_state, {"command": "list", "args": []})
    assert response["success"] is True
    assert "default *" in response["output"]


@pytest.mark.asyncio
async def test_goto_calls_page_goto(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    registry = MagicMock()
    with patch("patchright_cli.daemon.take_snapshot", new_callable=AsyncMock) as mock_snap:
        mock_snap.return_value = ("snapshot-text", registry)
        with patch("patchright_cli.daemon.save_snapshot", return_value="/tmp/snap.yml"):
            response = await handle_command(mock_state, {"command": "goto", "args": ["https://example.com"]})
    assert response["success"] is True
    mock_session.page.goto.assert_awaited_once_with("https://example.com")


@pytest.mark.asyncio
async def test_click_resolves_ref_and_clicks(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    registry = MagicMock()
    locator = MagicMock()
    locator.click = AsyncMock()
    locator.evaluate = AsyncMock(return_value=None)
    registry.resolve.return_value = locator
    mock_session.ref_registry = registry

    with patch("patchright_cli.daemon.take_snapshot", new_callable=AsyncMock) as mock_snap:
        mock_snap.return_value = ("snapshot-text", registry)
        with patch("patchright_cli.daemon.save_snapshot", return_value="/tmp/snap.yml"):
            response = await handle_command(mock_state, {"command": "click", "args": ["e1"]})

    assert response["success"] is True
    registry.resolve.assert_called_once_with(mock_session.page, "e1")
    locator.click.assert_awaited_once_with(button="left", modifiers=None)


@pytest.mark.asyncio
async def test_cookie_export(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session.context.cookies = AsyncMock(return_value=[{"name": "a", "value": "1"}])
    response = await handle_command(mock_state, {"command": "cookie-export", "args": []})
    assert response["success"] is True
    assert "Exported 1 cookie" in response["output"]


@pytest.mark.asyncio
async def test_cookie_import(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session.context.add_cookies = AsyncMock()
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write('[{"name": "b", "value": "2"}]')
        path = f.name
    response = await handle_command(mock_state, {"command": "cookie-import", "args": [path]})
    assert response["success"] is True
    assert "Imported 1 cookie" in response["output"]
    mock_session.context.add_cookies.assert_awaited_once()
    import os
    os.unlink(path)


@pytest.mark.asyncio
async def test_scroll_command(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session.page.mouse.wheel = AsyncMock()
    response = await handle_command(mock_state, {"command": "scroll", "args": ["0", "200"]})
    assert response["success"] is True
    mock_session.page.mouse.wheel.assert_awaited_once_with(0.0, 200.0)


@pytest.mark.asyncio
async def test_wait_command(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    with patch("patchright_cli.daemon.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        response = await handle_command(mock_state, {"command": "wait", "args": ["500"]})
    assert response["success"] is True
    mock_sleep.assert_awaited_once_with(0.5)
