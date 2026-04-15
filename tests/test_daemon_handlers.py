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
    session._codegen = None
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


# ---------------------------------------------------------------------------
# New tests for v0.4.1 features
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_eval_with_ref(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    registry = MagicMock()
    locator = MagicMock()
    locator.evaluate = AsyncMock(return_value="el-result")
    registry.resolve.return_value = locator
    mock_session.ref_registry = registry

    response = await handle_command(
        mock_state,
        {"command": "eval", "args": ["el => el.textContent", "e1"]},
    )
    assert response["success"] is True
    assert "el-result" in response["output"]
    registry.resolve.assert_called_once_with(mock_session.page, "e1")
    locator.evaluate.assert_awaited_once_with("el => el.textContent")


@pytest.mark.asyncio
async def test_snapshot_with_depth(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    registry = MagicMock()
    with patch("patchright_cli.daemon.take_snapshot", new_callable=AsyncMock) as mock_snap:
        mock_snap.return_value = ("snapshot-text", registry)
        with patch("patchright_cli.daemon.save_snapshot", return_value="/tmp/snap.yml"):
            response = await handle_command(
                mock_state,
                {"command": "snapshot", "args": [], "options": {"depth": "2"}},
            )
    assert response["success"] is True
    mock_snap.assert_awaited_once()
    _, kwargs = mock_snap.call_args
    assert kwargs.get("max_depth") == 2


@pytest.mark.asyncio
async def test_timeouts_applied(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session.page.set_default_timeout = MagicMock()
    mock_session.page.set_default_navigation_timeout = MagicMock()

    response = await handle_command(
        mock_state,
        {
            "command": "url",
            "args": [],
            "options": {"timeout-action": "10000", "timeout-navigation": "30000"},
        },
    )
    assert response["success"] is True
    mock_session.page.set_default_timeout.assert_called_once_with(10000.0)
    mock_session.page.set_default_navigation_timeout.assert_called_once_with(30000.0)


@pytest.mark.asyncio
async def test_video_chapter(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session._video_recording = True
    mock_session._video_frames = [b"frame1", b"frame2"]
    mock_session._video_chapters = []

    response = await handle_command(
        mock_state,
        {"command": "video-chapter", "args": ["Login page"]},
    )
    assert response["success"] is True
    assert mock_session._video_chapters == [(2, "Login page")]


@pytest.mark.asyncio
async def test_video_chapter_no_recording(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session._video_recording = False

    response = await handle_command(
        mock_state,
        {"command": "video-chapter", "args": ["title"]},
    )
    assert response["success"] is False
    assert "No video recording" in response["output"]


@pytest.mark.asyncio
async def test_grant_permissions(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session.context.grant_permissions = AsyncMock()

    response = await handle_command(
        mock_state,
        {
            "command": "grant-permissions",
            "args": ["geolocation,camera"],
            "options": {"origin": "https://example.com"},
        },
    )
    assert response["success"] is True
    mock_session.context.grant_permissions.assert_awaited_once_with(
        permissions=["geolocation", "camera"],
        origin="https://example.com",
    )


@pytest.mark.asyncio
async def test_codegen_records_commands(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session._codegen = None

    # Start codegen
    response = await handle_command(mock_state, {"command": "codegen", "args": []})
    assert response["success"] is True
    assert isinstance(mock_session._codegen, list)

    # Run a goto — should get appended
    registry = MagicMock()
    with patch("patchright_cli.daemon.take_snapshot", new_callable=AsyncMock) as mock_snap:
        mock_snap.return_value = ("snapshot-text", registry)
        with patch("patchright_cli.daemon.save_snapshot", return_value="/tmp/snap.yml"):
            response = await handle_command(
                mock_state,
                {"command": "goto", "args": ["https://example.com"]},
            )
    assert response["success"] is True
    assert any("goto" in entry and "https://example.com" in entry for entry in mock_session._codegen)


@pytest.mark.asyncio
async def test_codegen_stop_writes_script(mock_state, mock_session):
    import tempfile

    mock_state.sessions = {"default": mock_session}
    mock_session._codegen = [
        'patchright-cli open "https://example.com"',
        "patchright-cli goto https://test.com",
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        response = await handle_command(
            mock_state,
            {"command": "codegen-stop", "args": [], "cwd": tmpdir},
        )
    assert response["success"] is True
    assert "2 command(s)" in response["output"]


@pytest.mark.asyncio
async def test_codegen_stop_no_recording(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session._codegen = None

    response = await handle_command(mock_state, {"command": "codegen-stop", "args": []})
    assert response["success"] is False


@pytest.mark.asyncio
async def test_show_dashboard(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_runner = MagicMock()
    mock_url = "http://localhost:9322"

    mock_dashboard_module = MagicMock()
    mock_dashboard_module.start_dashboard_server = AsyncMock(return_value=(mock_runner, mock_url))

    import patchright_cli.daemon as daemon_mod

    daemon_mod._dashboard_runners.clear()

    with patch.dict("sys.modules", {"patchright_cli.dashboard": mock_dashboard_module}):
        response = await handle_command(mock_state, {"command": "show", "args": []})

    assert response["success"] is True
    assert "Dashboard running at" in response["output"]
