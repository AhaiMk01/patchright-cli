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
    session._video_recording = False
    session._video_show_actions = None
    session.tabs = {"default": MagicMock()}
    session.activate_tab = MagicMock()
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
    mock_dashboard_module.start_dashboard_server = AsyncMock(return_value=(mock_runner, mock_url, MagicMock()))

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


@pytest.fixture
def find_session(mock_session):
    from patchright_cli.ref_registry import RefRegistry

    registry = RefRegistry()
    registry.parse('- banner:\n  - link "Sign in"\n  - link "Sign up"\n  - text: Signal strength\n')
    mock_session.ref_registry = registry
    return mock_session


def _patch_snapshot(registry):
    """take_snapshot is patched to return a pre-parsed registry, so no browser is needed."""
    return patch(
        "patchright_cli.daemon.take_snapshot",
        AsyncMock(return_value=("ignored", registry)),
    )


@pytest.mark.asyncio
async def test_find_returns_matches(mock_state, find_session):
    mock_state.sessions = {"default": find_session}
    with _patch_snapshot(find_session.ref_registry):
        response = await handle_command(mock_state, {"command": "find", "args": ["Sign"], "options": {}})
    assert response["success"] is True
    assert "Sign in" in response["output"]
    assert "Sign up" in response["output"]


@pytest.mark.asyncio
async def test_find_excludes_text_nodes_by_default(mock_state, find_session):
    mock_state.sessions = {"default": find_session}
    with _patch_snapshot(find_session.ref_registry):
        response = await handle_command(mock_state, {"command": "find", "args": ["Sign"], "options": {}})
    assert "Signal strength" not in response["output"]


@pytest.mark.asyncio
async def test_find_all_flag_includes_text_nodes(mock_state, find_session):
    mock_state.sessions = {"default": find_session}
    with _patch_snapshot(find_session.ref_registry):
        response = await handle_command(mock_state, {"command": "find", "args": ["Sign"], "options": {"all": True}})
    assert "Signal strength" in response["output"]


@pytest.mark.asyncio
async def test_find_without_query_fails(mock_state, find_session):
    mock_state.sessions = {"default": find_session}
    response = await handle_command(mock_state, {"command": "find", "args": [], "options": {}})
    assert response["success"] is False
    assert "requires a search term" in response["output"]


@pytest.mark.asyncio
async def test_find_invalid_regex_fails(mock_state, find_session):
    mock_state.sessions = {"default": find_session}
    with _patch_snapshot(find_session.ref_registry):
        response = await handle_command(
            mock_state, {"command": "find", "args": ["Sign (unclosed"], "options": {"regex": True}}
        )
    assert response["success"] is False
    assert "Invalid regex" in response["output"]


@pytest.mark.asyncio
async def test_find_invalid_limit_fails(mock_state, find_session):
    mock_state.sessions = {"default": find_session}
    response = await handle_command(mock_state, {"command": "find", "args": ["Sign"], "options": {"limit": "abc"}})
    assert response["success"] is False
    assert "Invalid --limit" in response["output"]


@pytest.mark.asyncio
async def test_find_zero_limit_fails(mock_state, find_session):
    mock_state.sessions = {"default": find_session}
    response = await handle_command(mock_state, {"command": "find", "args": ["Sign"], "options": {"limit": "0"}})
    assert response["success"] is False
    assert "Invalid --limit" in response["output"]


@pytest.mark.asyncio
async def test_find_bare_limit_flag_fails(mock_state, find_session):
    mock_state.sessions = {"default": find_session}
    response = await handle_command(mock_state, {"command": "find", "args": ["Sign"], "options": {"limit": True}})
    assert response["success"] is False
    assert "Invalid --limit" in response["output"]


@pytest.mark.asyncio
async def test_find_rejects_pattern_given_twice(mock_state, find_session):
    mock_state.sessions = {"default": find_session}
    response = await handle_command(mock_state, {"command": "find", "args": ["Sign"], "options": {"regex": "Sign"}})
    assert response["success"] is False
    assert "not both" in response["output"]


@pytest.mark.asyncio
async def test_find_no_matches_succeeds(mock_state, find_session):
    mock_state.sessions = {"default": find_session}
    with _patch_snapshot(find_session.ref_registry):
        response = await handle_command(mock_state, {"command": "find", "args": ["absent"], "options": {}})
    assert response["success"] is True
    assert "No matches" in response["output"]


# -- Device / mobile emulation -----------------------------------------------


FAKE_DEVICES = {
    "Pixel 7": {
        "user_agent": "Mozilla/5.0 (Linux; Android 14; Pixel 7) Mobile Safari/537.36",
        "viewport": {"width": 412, "height": 839},
        "device_scale_factor": 2.625,
        "is_mobile": True,
        "has_touch": True,
        "default_browser_type": "chromium",
    },
    "iPhone 15": {
        "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) Mobile/15E148",
        "viewport": {"width": 393, "height": 659},
        "device_scale_factor": 3,
        "is_mobile": True,
        "has_touch": True,
        "default_browser_type": "webkit",
    },
}


def test_resolve_device_options_drops_default_browser_type():
    from patchright_cli.daemon import resolve_device_options

    opts = resolve_device_options(FAKE_DEVICES, "iPhone 15", False)
    assert "default_browser_type" not in opts
    assert opts["viewport"] == {"width": 393, "height": 659}
    assert opts["is_mobile"] is True


def test_resolve_device_options_mobile_uses_default_device():
    from patchright_cli.daemon import DEFAULT_MOBILE_DEVICE, resolve_device_options

    assert DEFAULT_MOBILE_DEVICE in FAKE_DEVICES
    opts = resolve_device_options(FAKE_DEVICES, None, True)
    assert opts["user_agent"] == FAKE_DEVICES[DEFAULT_MOBILE_DEVICE]["user_agent"]


def test_resolve_device_options_explicit_device_wins_over_mobile():
    from patchright_cli.daemon import resolve_device_options

    opts = resolve_device_options(FAKE_DEVICES, "iPhone 15", True)
    assert opts["viewport"] == {"width": 393, "height": 659}


def test_resolve_device_options_no_device_is_empty():
    from patchright_cli.daemon import resolve_device_options

    assert resolve_device_options(FAKE_DEVICES, None, False) == {}


def test_resolve_device_options_unknown_device_raises():
    from patchright_cli.daemon import resolve_device_options

    with pytest.raises(ValueError, match="Unknown device 'Nokia 3310'"):
        resolve_device_options(FAKE_DEVICES, "Nokia 3310", False)


# -- screenshot --hires ------------------------------------------------------


@pytest.mark.asyncio
async def test_screenshot_defaults_to_css_scale(mock_state, mock_session, tmp_path):
    mock_state.sessions = {"default": mock_session}
    mock_session.page.screenshot = AsyncMock()

    response = await handle_command(
        mock_state, {"command": "screenshot", "args": [], "options": {}, "cwd": str(tmp_path)}
    )

    assert response["success"] is True
    assert mock_session.page.screenshot.await_args.kwargs["scale"] == "css"


@pytest.mark.asyncio
async def test_screenshot_hires_uses_device_scale(mock_state, mock_session, tmp_path):
    mock_state.sessions = {"default": mock_session}
    mock_session.page.screenshot = AsyncMock()

    response = await handle_command(
        mock_state, {"command": "screenshot", "args": [], "options": {"hires": True}, "cwd": str(tmp_path)}
    )

    assert response["success"] is True
    assert mock_session.page.screenshot.await_args.kwargs["scale"] == "device"


@pytest.mark.asyncio
async def test_element_screenshot_honours_hires(mock_state, mock_session, tmp_path):
    mock_state.sessions = {"default": mock_session}
    registry = MagicMock()
    locator = MagicMock()
    locator.screenshot = AsyncMock()
    registry.resolve.return_value = locator
    mock_session.ref_registry = registry

    response = await handle_command(
        mock_state, {"command": "screenshot", "args": ["e1"], "options": {"hires": True}, "cwd": str(tmp_path)}
    )

    assert response["success"] is True
    assert locator.screenshot.await_args.kwargs["scale"] == "device"


# -- video-show-actions / video-hide-actions ---------------------------------


@pytest.mark.asyncio
async def test_video_show_actions_defaults(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session._video_show_actions = None

    response = await handle_command(mock_state, {"command": "video-show-actions", "args": [], "options": {}})

    assert response["success"] is True
    assert mock_session._video_show_actions == {"duration": 600, "position": "top-right"}


@pytest.mark.asyncio
async def test_video_show_actions_accepts_duration_and_position(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session._video_show_actions = None

    response = await handle_command(
        mock_state,
        {"command": "video-show-actions", "args": [], "options": {"duration": "1200", "position": "bottom-left"}},
    )

    assert response["success"] is True
    assert mock_session._video_show_actions == {"duration": 1200, "position": "bottom-left"}


@pytest.mark.asyncio
async def test_video_show_actions_rejects_unknown_position(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session._video_show_actions = None

    response = await handle_command(
        mock_state, {"command": "video-show-actions", "args": [], "options": {"position": "middle"}}
    )

    assert response["success"] is False
    assert "middle" in response["output"]
    assert mock_session._video_show_actions is None


@pytest.mark.asyncio
async def test_video_show_actions_rejects_bare_duration_flag(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session._video_show_actions = None

    response = await handle_command(
        mock_state, {"command": "video-show-actions", "args": [], "options": {"duration": True}}
    )

    assert response["success"] is False
    assert mock_session._video_show_actions is None


@pytest.mark.asyncio
async def test_video_hide_actions_clears_the_flag(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session._video_show_actions = {"duration": 600, "position": "top-right"}
    mock_session.page.evaluate = AsyncMock()

    response = await handle_command(mock_state, {"command": "video-hide-actions", "args": [], "options": {}})

    assert response["success"] is True
    assert mock_session._video_show_actions is None


@pytest.mark.asyncio
async def test_action_callout_is_skipped_when_not_recording(mock_session):
    from patchright_cli.daemon import _draw_action_callout

    mock_session._video_show_actions = {"duration": 600, "position": "top-right"}
    mock_session._video_recording = False
    page = MagicMock()
    page.evaluate = AsyncMock()

    await _draw_action_callout(mock_session, page, "click", ["e1"])

    page.evaluate.assert_not_awaited()


@pytest.mark.asyncio
async def test_action_callout_is_skipped_when_disabled(mock_session):
    from patchright_cli.daemon import _draw_action_callout

    mock_session._video_show_actions = None
    mock_session._video_recording = True
    page = MagicMock()
    page.evaluate = AsyncMock()

    await _draw_action_callout(mock_session, page, "click", ["e1"])

    page.evaluate.assert_not_awaited()


@pytest.mark.asyncio
async def test_action_callout_names_the_target_element(mock_session):
    from patchright_cli.daemon import _draw_action_callout

    mock_session._video_show_actions = {"duration": 600, "position": "top-right"}
    mock_session._video_recording = True

    entry = MagicMock()
    entry.role = "button"
    entry.name = "Sign in"
    registry = MagicMock()
    registry.entries = {"e1": entry}
    locator = MagicMock()
    locator.bounding_box = AsyncMock(return_value={"x": 1, "y": 2, "width": 3, "height": 4})
    registry.resolve.return_value = locator
    mock_session.ref_registry = registry

    page = MagicMock()
    page.evaluate = AsyncMock()

    await _draw_action_callout(mock_session, page, "click", ["e1"])

    payload = page.evaluate.await_args.args[1]
    assert payload["label"] == 'click button "Sign in"'
    assert payload["box"] == {"x": 1, "y": 2, "width": 3, "height": 4}
    assert payload["duration"] == 600
    assert payload["position"] == "top-right"


@pytest.mark.asyncio
async def test_action_callout_ignores_non_action_commands(mock_session):
    from patchright_cli.daemon import _draw_action_callout

    mock_session._video_show_actions = {"duration": 600, "position": "top-right"}
    mock_session._video_recording = True
    page = MagicMock()
    page.evaluate = AsyncMock()

    await _draw_action_callout(mock_session, page, "snapshot", [])

    page.evaluate.assert_not_awaited()


@pytest.mark.asyncio
async def test_action_callout_never_breaks_the_action(mock_session):
    from patchright_cli.daemon import _draw_action_callout

    mock_session._video_show_actions = {"duration": 600, "position": "top-right"}
    mock_session._video_recording = True
    mock_session.ref_registry = None
    page = MagicMock()
    page.evaluate = AsyncMock(side_effect=RuntimeError("execution context destroyed"))

    await _draw_action_callout(mock_session, page, "press", ["Enter"])


# -- Daemon idle timeout -----------------------------------------------------


def test_parse_idle_timeout_accepts_seconds():
    from patchright_cli.daemon import _parse_idle_timeout

    assert _parse_idle_timeout("900") == 900.0
    assert _parse_idle_timeout(60) == 60.0


def test_parse_idle_timeout_rejects_a_bare_flag():
    from patchright_cli.daemon import _parse_idle_timeout

    with pytest.raises(ValueError):
        _parse_idle_timeout(True)


def test_parse_idle_timeout_rejects_non_positive_and_garbage():
    from patchright_cli.daemon import _parse_idle_timeout

    for bad in ("abc", 0, -5):
        with pytest.raises(ValueError):
            _parse_idle_timeout(bad)


def test_resolve_idle_timeout_prefers_the_environment():
    from patchright_cli.daemon import DEFAULT_IDLE_TIMEOUT, resolve_idle_timeout

    assert resolve_idle_timeout("900") == 900.0
    assert resolve_idle_timeout(None) == DEFAULT_IDLE_TIMEOUT
    assert resolve_idle_timeout("nonsense") == DEFAULT_IDLE_TIMEOUT


@pytest.mark.asyncio
async def test_timeout_option_updates_the_daemon(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_state.idle_timeout = 1800.0

    response = await handle_command(mock_state, {"command": "url", "args": [], "options": {"timeout": "900"}})

    assert response["success"] is True
    assert mock_state.idle_timeout == 900.0


@pytest.mark.asyncio
async def test_invalid_timeout_option_is_reported(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_state.idle_timeout = 1800.0

    response = await handle_command(mock_state, {"command": "url", "args": [], "options": {"timeout": True}})

    assert response["success"] is False
    assert mock_state.idle_timeout == 1800.0


@pytest.mark.asyncio
async def test_timeout_option_does_not_reach_the_handler(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_state.idle_timeout = 1800.0
    mock_session.page.screenshot = AsyncMock()

    response = await handle_command(
        mock_state, {"command": "screenshot", "args": [], "options": {"timeout": "900"}, "cwd": None}
    )

    assert response["success"] is True
    # cmd_screenshot never forwards **options, so asserting absence from its
    # kwargs could not fail. Assert the option was consumed for its real
    # purpose -- reconfiguring the daemon -- and that the command still ran.
    assert mock_state.idle_timeout == 900.0
    mock_session.page.screenshot.assert_awaited_once()


# -- wait --url --------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_for_url_pattern(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session.page.wait_for_url = AsyncMock()

    response = await handle_command(mock_state, {"command": "wait", "args": [], "options": {"url": "*/dashboard"}})

    assert response["success"] is True
    mock_session.page.wait_for_url.assert_awaited_once_with("*/dashboard")


@pytest.mark.asyncio
async def test_wait_rejects_a_bare_url_flag(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session.page.wait_for_url = AsyncMock()

    response = await handle_command(mock_state, {"command": "wait", "args": [], "options": {"url": True}})

    assert response["success"] is False
    mock_session.page.wait_for_url.assert_not_awaited()


@pytest.mark.asyncio
async def test_wait_rejects_url_together_with_a_duration(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session.page.wait_for_url = AsyncMock()

    response = await handle_command(mock_state, {"command": "wait", "args": ["500"], "options": {"url": "*/done"}})

    assert response["success"] is False
    mock_session.page.wait_for_url.assert_not_awaited()


# -- snapshot --selector -----------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_scoped_to_a_css_selector(mock_state, mock_session, tmp_path):
    mock_state.sessions = {"default": mock_session}
    scoped = MagicMock()
    scoped.count = AsyncMock(return_value=1)
    mock_session.page.locator = MagicMock(return_value=scoped)

    with patch("patchright_cli.daemon.take_snapshot", new_callable=AsyncMock) as mock_snap:
        mock_snap.return_value = ("scoped-text", MagicMock())
        response = await handle_command(
            mock_state,
            {"command": "snapshot", "args": [], "options": {"selector": "#main"}, "cwd": str(tmp_path)},
        )

    assert response["success"] is True
    mock_session.page.locator.assert_called_once_with("#main")
    assert mock_snap.await_args.kwargs["root_element"] is scoped


@pytest.mark.asyncio
async def test_snapshot_selector_matching_nothing_is_an_error(mock_state, mock_session, tmp_path):
    mock_state.sessions = {"default": mock_session}
    scoped = MagicMock()
    scoped.count = AsyncMock(return_value=0)
    mock_session.page.locator = MagicMock(return_value=scoped)

    response = await handle_command(
        mock_state,
        {"command": "snapshot", "args": [], "options": {"selector": "#nope"}, "cwd": str(tmp_path)},
    )

    assert response["success"] is False
    assert "#nope" in response["output"]


@pytest.mark.asyncio
async def test_snapshot_rejects_a_bare_selector_flag(mock_state, mock_session, tmp_path):
    mock_state.sessions = {"default": mock_session}

    response = await handle_command(
        mock_state,
        {"command": "snapshot", "args": [], "options": {"selector": True}, "cwd": str(tmp_path)},
    )

    assert response["success"] is False


@pytest.mark.asyncio
async def test_snapshot_rejects_selector_together_with_a_ref(mock_state, mock_session, tmp_path):
    mock_state.sessions = {"default": mock_session}

    response = await handle_command(
        mock_state,
        {"command": "snapshot", "args": ["e1"], "options": {"selector": "#main"}, "cwd": str(tmp_path)},
    )

    assert response["success"] is False


def test_url_pattern_passes_globs_through():
    from patchright_cli.daemon import _url_pattern

    assert _url_pattern("**/dashboard") == "**/dashboard"


def test_url_pattern_compiles_the_slash_form():
    import re as _re

    from patchright_cli.daemon import _url_pattern

    compiled = _url_pattern("/dash(board)?$/i")
    assert isinstance(compiled, _re.Pattern)
    assert compiled.search("https://app.example.com/DASHBOARD")


def test_url_pattern_rejects_a_broken_regex():
    from patchright_cli.daemon import _url_pattern

    with pytest.raises(ValueError):
        _url_pattern("/dash(/")


@pytest.mark.asyncio
async def test_wait_reports_a_broken_url_regex(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session.page.wait_for_url = AsyncMock()

    response = await handle_command(mock_state, {"command": "wait", "args": [], "options": {"url": "/dash(/"}})

    assert response["success"] is False
    mock_session.page.wait_for_url.assert_not_awaited()


# -- Idle watchdog vs long-running commands ----------------------------------


def test_daemon_state_tracks_commands_in_flight():
    from patchright_cli.daemon import DaemonState

    state = DaemonState()
    assert state.commands_in_flight == 0
    with state.command_running():
        assert state.commands_in_flight == 1
        with state.command_running():
            assert state.commands_in_flight == 2
    assert state.commands_in_flight == 0


def test_command_running_restamps_activity_on_the_way_out():
    import time

    from patchright_cli.daemon import DaemonState

    state = DaemonState()
    state.last_activity = time.monotonic() - 10_000
    with state.command_running():
        pass
    assert time.monotonic() - state.last_activity < 1


def test_command_running_restamps_even_when_the_command_raises():
    import time

    from patchright_cli.daemon import DaemonState

    state = DaemonState()
    state.last_activity = time.monotonic() - 10_000
    try:
        with state.command_running():
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert state.commands_in_flight == 0
    assert time.monotonic() - state.last_activity < 1


def test_daemon_is_not_idle_while_a_command_is_running():
    """show --annotate blocks for minutes; the watchdog must not kill it."""
    import time

    from patchright_cli.daemon import DaemonState

    state = DaemonState()
    state.idle_timeout = 1.0
    state.last_activity = time.monotonic() - 3600
    with state.command_running():
        assert state.is_idle() is False
    assert state.is_idle() is False  # just restamped


def test_daemon_is_idle_once_nothing_is_running():
    import time

    from patchright_cli.daemon import DaemonState

    state = DaemonState()
    state.idle_timeout = 1.0
    state.last_activity = time.monotonic() - 3600
    assert state.is_idle() is True


@pytest.mark.asyncio
async def test_snapshot_selector_matching_many_is_an_error(mock_state, mock_session, tmp_path):
    """aria_snapshot() is strict; snapshot.py swallows the violation into an
    empty tree, so a multi-match selector used to report success while wiping
    the ref registry."""
    mock_state.sessions = {"default": mock_session}
    registry = MagicMock()
    mock_session.ref_registry = registry
    scoped = MagicMock()
    scoped.count = AsyncMock(return_value=3)
    mock_session.page.locator = MagicMock(return_value=scoped)

    with patch("patchright_cli.daemon.take_snapshot", new_callable=AsyncMock) as mock_snap:
        response = await handle_command(
            mock_state,
            {"command": "snapshot", "args": [], "options": {"selector": ".card"}, "cwd": str(tmp_path)},
        )

    assert response["success"] is False
    assert ".card" in response["output"]
    assert "3" in response["output"]
    mock_snap.assert_not_awaited()
    # The previous snapshot's refs must survive a rejected command.
    assert mock_session.ref_registry is registry
