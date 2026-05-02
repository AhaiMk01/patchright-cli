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


@pytest.mark.asyncio
async def test_drop_requires_path_or_data(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    registry = MagicMock()
    locator = MagicMock()
    locator.evaluate = AsyncMock()
    registry.resolve.return_value = locator
    mock_session.ref_registry = registry

    response = await handle_command(mock_state, {"command": "drop", "args": ["e1"]})
    assert response["success"] is False
    assert "--path" in response["output"]


@pytest.mark.asyncio
async def test_drop_with_data_dispatches_drop_event(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    registry = MagicMock()
    locator = MagicMock()
    locator.evaluate = AsyncMock(return_value=None)
    registry.resolve.return_value = locator
    mock_session.ref_registry = registry

    with patch("patchright_cli.daemon.take_snapshot", new_callable=AsyncMock) as mock_snap:
        mock_snap.return_value = ("snapshot-text", registry)
        with patch("patchright_cli.daemon.save_snapshot", return_value="/tmp/snap.yml"):
            response = await handle_command(
                mock_state,
                {"command": "drop", "args": ["e1"], "options": {"data": "text/plain=hello"}},
            )

    assert response["success"] is True
    locator.evaluate.assert_awaited_once()
    js, payload = locator.evaluate.call_args.args
    assert "DataTransfer" in js
    assert payload["dataEntries"] == [("text/plain", "hello")]
    assert payload["files"] == []


@pytest.mark.asyncio
async def test_drop_with_missing_file(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    registry = MagicMock()
    locator = MagicMock()
    locator.evaluate = AsyncMock()
    registry.resolve.return_value = locator
    mock_session.ref_registry = registry

    response = await handle_command(
        mock_state,
        {"command": "drop", "args": ["e1"], "options": {"path": "/no/such/file"}},
    )
    assert response["success"] is False
    assert "File not found" in response["output"]


@pytest.mark.asyncio
async def test_request_unknown_id(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session.network_log = []
    response = await handle_command(mock_state, {"command": "request", "args": ["7"]})
    assert response["success"] is False
    assert "No request" in response["output"]


@pytest.mark.asyncio
async def test_request_renders_entry(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session.network_log = [
        {
            "id": 0,
            "method": "POST",
            "url": "https://api.example.com/v1/x",
            "resource": "fetch",
            "ts": 1.0,
            "request_headers": {"Content-Type": "application/json"},
            "post_data": '{"a":1}',
            "status": 200,
            "status_text": "OK",
            "response_headers": {"X-Trace": "abc"},
            "_request": object(),
        }
    ]
    response = await handle_command(mock_state, {"command": "request", "args": ["0"]})
    assert response["success"] is True
    assert "POST" in response["output"]
    assert "Status: 200 OK" in response["output"]
    assert "Content-Type" in response["output"]
    assert '"a":1' in response["output"]
    assert "X-Trace" in response["output"]


@pytest.mark.asyncio
async def test_detach_rejects_non_attached(mock_state, mock_session):
    mock_session.is_attached = False
    mock_state.sessions = {"default": mock_session}
    response = await handle_command(mock_state, {"command": "detach", "args": []})
    assert response["success"] is False
    assert "not attached" in response["output"]


@pytest.mark.asyncio
async def test_detach_disconnects_attached(mock_state, mock_session):
    mock_session.is_attached = True
    mock_session.browser = MagicMock()
    mock_session.browser.close = AsyncMock()
    mock_state.sessions = {"default": mock_session}
    response = await handle_command(mock_state, {"command": "detach", "args": []})
    assert response["success"] is True
    assert "Detached" in response["output"]
    mock_session.browser.close.assert_awaited_once()
    assert "default" not in mock_state.sessions


@pytest.mark.asyncio
async def test_requests_aliases_to_network(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session.network_log = [
        {"id": 0, "method": "GET", "url": "https://x/", "resource": "document", "ts": 1.0, "status": 200},
    ]
    response = await handle_command(mock_state, {"command": "requests", "args": []})
    assert response["success"] is True
    assert "#0 GET 200" in response["output"]


@pytest.mark.asyncio
async def test_generate_locator_no_snapshot(mock_state, mock_session):
    mock_session.ref_registry = None
    mock_state.sessions = {"default": mock_session}
    response = await handle_command(mock_state, {"command": "generate-locator", "args": ["e1"]})
    assert response["success"] is False
    assert "snapshot" in response["output"].lower()


@pytest.mark.asyncio
async def test_generate_locator_emits_role_name(mock_state, mock_session):
    from patchright_cli.ref_registry import AriaRefEntry, RefRegistry

    registry = RefRegistry()
    registry.entries = {
        "e1": AriaRefEntry(ref="e1", role="button", name="Sign in", nth=0),
        "e2": AriaRefEntry(ref="e2", role="link", name="", nth=2),
    }
    mock_session.ref_registry = registry
    mock_state.sessions = {"default": mock_session}

    r1 = await handle_command(mock_state, {"command": "generate-locator", "args": ["e1"]})
    assert r1["success"] is True
    assert r1["output"] == "getByRole('button', { name: 'Sign in', exact: true })"

    r2 = await handle_command(mock_state, {"command": "generate-locator", "args": ["e2"]})
    assert r2["success"] is True
    assert r2["output"] == "getByRole('link').nth(2)"


@pytest.mark.asyncio
async def test_generate_locator_escapes_quote(mock_state, mock_session):
    from patchright_cli.ref_registry import AriaRefEntry, RefRegistry

    registry = RefRegistry()
    registry.entries = {"e1": AriaRefEntry(ref="e1", role="button", name="It's me", nth=0)}
    mock_session.ref_registry = registry
    mock_state.sessions = {"default": mock_session}

    r = await handle_command(mock_state, {"command": "generate-locator", "args": ["e1"]})
    assert r["success"] is True
    assert "It\\'s me" in r["output"]


@pytest.mark.asyncio
async def test_highlight_clear_all(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session.page.evaluate = AsyncMock()
    response = await handle_command(mock_state, {"command": "highlight", "args": [], "options": {"hide": True}})
    assert response["success"] is True
    assert "cleared" in response["output"].lower()
    mock_session.page.evaluate.assert_awaited_once()


@pytest.mark.asyncio
async def test_highlight_show(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    registry = MagicMock()
    locator = MagicMock()
    locator.evaluate = AsyncMock()
    registry.resolve.return_value = locator
    mock_session.ref_registry = registry

    response = await handle_command(
        mock_state, {"command": "highlight", "args": ["e1"], "options": {"style": "outline: 4px solid red"}}
    )
    assert response["success"] is True
    locator.evaluate.assert_awaited_once()
    js, payload = locator.evaluate.call_args.args
    assert "data-patchright-highlight" in js
    assert payload["style"] == "outline: 4px solid red"


@pytest.mark.asyncio
async def test_snapshot_with_boxes(mock_state, mock_session):
    from patchright_cli.ref_registry import AriaRefEntry, RefRegistry

    registry = RefRegistry()
    registry.entries = {"e1": AriaRefEntry(ref="e1", role="button", name="Go", nth=0)}
    locator = MagicMock()
    locator.bounding_box = AsyncMock(return_value={"x": 10, "y": 20, "width": 100, "height": 30})

    mock_state.sessions = {"default": mock_session}

    snap = '- button "Go" [ref=e1]'
    with (
        patch("patchright_cli.daemon.take_snapshot", new_callable=AsyncMock) as mock_snap,
        patch.object(registry, "resolve", return_value=locator),
        patch("patchright_cli.daemon.save_snapshot", return_value="/tmp/snap.yml"),
    ):
        mock_snap.return_value = (snap, registry)
        response = await handle_command(
            mock_state,
            {"command": "snapshot", "args": [], "options": {"boxes": True}},
        )
    assert response["success"] is True
    assert response["snapshot_path"] == "/tmp/snap.yml"


@pytest.mark.asyncio
async def test_network_includes_id(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session.network_log = [
        {"id": 0, "method": "GET", "url": "https://x/", "resource": "document", "ts": 1.0, "status": 200},
        {"id": 1, "method": "POST", "url": "https://x/api", "resource": "fetch", "ts": 1.1},
    ]
    response = await handle_command(mock_state, {"command": "network", "args": []})
    assert response["success"] is True
    assert "#0 GET 200" in response["output"]
    assert "#1 POST" in response["output"]
