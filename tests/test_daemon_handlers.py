"""Unit tests for daemon command dispatch."""

import asyncio
from functools import partial
from pathlib import Path
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
    session.page = MagicMock()
    session.page.goto = AsyncMock()
    session.page.url = "https://example.com"
    session.page.title = AsyncMock(return_value="Example")
    session._codegen = None
    session.highlights = {}
    session._video_recording = False
    session._video_show_actions = None
    session.tabs = {"default": MagicMock()}
    session.activate_tab = MagicMock()
    session.uses_host_context = False
    session.network_guard = None
    session.goto = partial(Session.goto, session)
    session.check_navigation = partial(Session.check_navigation, session)
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
    with patch("patchright_cli.daemon.ai_snapshot", new_callable=AsyncMock) as mock_snap:
        mock_snap.return_value = "snapshot-text"
        with patch("patchright_cli.daemon.save_snapshot", return_value="/tmp/snap.yml"):
            response = await handle_command(mock_state, {"command": "goto", "args": ["https://example.com"]})
    assert response["success"] is True
    mock_session.page.goto.assert_awaited_once_with("https://example.com")


@pytest.mark.asyncio
async def test_click_resolves_ref_and_clicks(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    locator = MagicMock()
    locator.click = AsyncMock()
    locator.evaluate = AsyncMock(return_value=None)
    use_refs(mock_session, locator)

    with patch("patchright_cli.daemon.ai_snapshot", new_callable=AsyncMock) as mock_snap:
        mock_snap.return_value = "snapshot-text"
        with patch("patchright_cli.daemon.save_snapshot", return_value="/tmp/snap.yml"):
            response = await handle_command(mock_state, {"command": "click", "args": ["e1"]})

    assert response["success"] is True
    mock_session.page.get_by_ref.assert_called_with("e1")
    locator.click.assert_awaited_once_with(button="left", modifiers=None, scroll=None)


def _action_locator(mock_session, href=None):
    locator = MagicMock()
    for method in ("click", "hover", "check", "drag_to"):
        setattr(locator, method, AsyncMock())
    locator.evaluate = AsyncMock(return_value=href)
    use_refs(mock_session, locator)
    return locator


async def _run_action(mock_state, command, args, options):
    with patch("patchright_cli.daemon.ai_snapshot", new_callable=AsyncMock) as mock_snap:
        mock_snap.return_value = "snapshot-text"
        with patch("patchright_cli.daemon.save_snapshot", return_value="/tmp/snap.yml"):
            return await handle_command(mock_state, {"command": command, "args": args, "options": options})


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["auto", "none"])
@pytest.mark.parametrize(
    ("command", "args", "method"),
    [
        ("click", ["e1"], "click"),
        ("hover", ["e1"], "hover"),
        ("check", ["e1"], "check"),
        ("drag", ["e1", "e2"], "drag_to"),
    ],
)
async def test_actions_pass_scroll_through(mock_state, mock_session, command, args, method, value):
    mock_state.sessions = {"default": mock_session}
    locator = _action_locator(mock_session)
    response = await _run_action(mock_state, command, args, {"scroll": value})
    assert response["success"] is True, response
    assert getattr(locator, method).await_args.kwargs["scroll"] == value


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["click", "hover", "check", "drag"])
@pytest.mark.parametrize("value", ["sideways", True])
async def test_actions_reject_bad_scroll_values(mock_state, mock_session, command, value):
    mock_state.sessions = {"default": mock_session}
    locator = _action_locator(mock_session)
    response = await _run_action(mock_state, command, ["e1", "e2"], {"scroll": value})
    assert response["success"] is False
    assert "--scroll" in response["output"] and "auto, none" in response["output"]
    for method in ("click", "hover", "check", "drag_to"):
        getattr(locator, method).assert_not_awaited()


VIEWPORT_ERROR = (
    "Locator.click: Timeout 1500ms exceeded.\nCall log:\n  - attempting click action\n"
    "      - element is outside of the viewport\n    - retrying click action"
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "args", "method"),
    [
        ("click", ["e1"], "click"),
        ("hover", ["e1"], "hover"),
        ("check", ["e1"], "check"),
        ("drag", ["e1", "e2"], "drag_to"),
    ],
)
async def test_scroll_none_viewport_timeout_gets_a_hint(mock_state, mock_session, command, args, method):
    mock_state.sessions = {"default": mock_session}
    locator = _action_locator(mock_session)
    getattr(locator, method).side_effect = Exception(VIEWPORT_ERROR)
    response = await _run_action(mock_state, command, args, {"scroll": "none"})
    assert response["success"] is False
    assert response["output"].splitlines() == [
        f"Error: {command} on e1 timed out after 1.5s: the element is outside of the viewport",
        "help: the element is off-screen; drop --scroll=none or scroll first",
    ]
    assert "Call log" not in response["output"] and "Locator." not in response["output"]


@pytest.mark.asyncio
async def test_scroll_none_link_trial_click_gets_the_hint(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    locator = _action_locator(mock_session, href="https://example.com/next")
    locator.click.side_effect = Exception(VIEWPORT_ERROR)
    response = await _run_action(mock_state, "click", ["e1"], {"scroll": "none"})
    assert response["output"].endswith("help: the element is off-screen; drop --scroll=none or scroll first")
    mock_session.page.goto.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("scroll", [None, "auto"])
async def test_viewport_error_without_scroll_none_has_no_hint(mock_state, mock_session, scroll):
    mock_state.sessions = {"default": mock_session}
    locator = _action_locator(mock_session)
    locator.click.side_effect = Exception(VIEWPORT_ERROR)
    response = await _run_action(mock_state, "click", ["e1"], {"scroll": scroll} if scroll else {})
    assert response["success"] is False
    assert "drop --scroll=none" not in response["output"]


@pytest.mark.asyncio
async def test_other_scroll_none_failures_have_no_hint(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    locator = _action_locator(mock_session)
    locator.hover.side_effect = Exception("Locator.hover: Timeout 1500ms exceeded.\n  - element is not visible")
    response = await _run_action(mock_state, "hover", ["e1"], {"scroll": "none"})
    assert response["success"] is False
    assert "drop --scroll=none" not in response["output"]


@pytest.mark.asyncio
async def test_click_link_with_scroll_none_checks_reachability_first(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    locator = _action_locator(mock_session, href="https://example.com/next")
    response = await _run_action(mock_state, "click", ["e1"], {"scroll": "none"})
    assert response["success"] is True, response
    locator.click.assert_awaited_once_with(trial=True, scroll="none")
    mock_session.page.goto.assert_awaited()


@pytest.mark.asyncio
async def test_click_link_unreachable_without_scrolling_does_not_navigate(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    locator = _action_locator(mock_session, href="https://example.com/next")
    locator.click.side_effect = Exception("Timeout 30000ms exceeded.")
    response = await _run_action(mock_state, "click", ["e1"], {"scroll": "none"})
    assert response["success"] is False
    mock_session.page.goto.assert_not_awaited()


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
    locator = MagicMock()
    locator.evaluate = AsyncMock(return_value="el-result")
    use_refs(mock_session, locator)

    response = await handle_command(
        mock_state,
        {"command": "eval", "args": ["el => el.textContent", "e1"]},
    )
    assert response["success"] is True
    assert "el-result" in response["output"]
    mock_session.page.get_by_ref.assert_called_with("e1")
    locator.evaluate.assert_awaited_once_with("el => el.textContent")


@pytest.mark.asyncio
async def test_snapshot_with_depth(mock_state, mock_session, tmp_path):
    mock_state.sessions = {"default": mock_session}
    raw = '- list [ref=e1]:\n  - listitem [ref=e2]:\n    - link "Deep" [ref=e3]\n'
    with patch("patchright_cli.daemon.ai_snapshot", new_callable=AsyncMock) as mock_snap:
        mock_snap.return_value = raw
        response = await handle_command(
            mock_state,
            {"command": "snapshot", "args": [], "options": {"depth": "1"}, "cwd": str(tmp_path)},
        )
    assert response["success"] is True
    # --depth is a text filter over one full snapshot; native depth= is never used.
    mock_snap.assert_awaited_once_with(mock_session.page, boxes=False)
    text = Path(response["snapshot_path"]).read_text(encoding="utf-8")
    assert text == "- list [ref=e1]:\n  - listitem [ref=e2]: # +1 ref\n"


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
    with patch("patchright_cli.daemon.ai_snapshot", new_callable=AsyncMock) as mock_snap:
        mock_snap.return_value = "snapshot-text"
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
    assert response["success"] is True
    assert response["output"].endswith(" (no-op)")


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
    locator = MagicMock()
    locator.evaluate = AsyncMock()
    use_refs(mock_session, locator)

    response = await handle_command(mock_state, {"command": "drop", "args": ["e1"]})
    assert response["success"] is False
    assert "--path" in response["output"]


@pytest.mark.asyncio
async def test_drop_with_data_uses_native_locator_drop(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    locator = MagicMock()
    locator.drop = AsyncMock(return_value=None)
    locator.evaluate = AsyncMock()
    use_refs(mock_session, locator)

    with patch("patchright_cli.daemon.ai_snapshot", new_callable=AsyncMock) as mock_snap:
        mock_snap.return_value = "snapshot-text"
        with patch("patchright_cli.daemon.save_snapshot", return_value="/tmp/snap.yml"):
            response = await handle_command(
                mock_state,
                {"command": "drop", "args": ["e1"], "options": {"data": "text/plain=hello"}},
            )

    assert response["success"] is True
    locator.drop.assert_awaited_once_with({"data": {"text/plain": "hello"}})
    locator.evaluate.assert_not_called()


@pytest.mark.asyncio
async def test_drop_with_files_and_data_sends_buffers_with_guessed_mime(mock_state, mock_session, tmp_path):
    note = tmp_path / "note.txt"
    note.write_bytes(b"hello file")
    blob = tmp_path / "blob.zzunknown"
    blob.write_bytes(b"\x00\x01")
    mock_state.sessions = {"default": mock_session}
    locator = MagicMock()
    locator.drop = AsyncMock(return_value=None)
    use_refs(mock_session, locator)

    with patch("patchright_cli.daemon.ai_snapshot", new_callable=AsyncMock) as mock_snap:
        mock_snap.return_value = "snapshot-text"
        with patch("patchright_cli.daemon.save_snapshot", return_value="/tmp/snap.yml"):
            response = await handle_command(
                mock_state,
                {
                    "command": "drop",
                    "args": ["e1"],
                    "options": {
                        "path": [str(note), str(blob)],
                        "data": ["text/uri-list=https://x.test/", "text/plain=a=b"],
                    },
                },
            )

    assert response["success"] is True
    locator.drop.assert_awaited_once_with(
        {
            "files": [
                {"name": "note.txt", "mimeType": "text/plain", "buffer": b"hello file"},
                {"name": "blob.zzunknown", "mimeType": "application/octet-stream", "buffer": b"\x00\x01"},
            ],
            "data": {"text/uri-list": "https://x.test/", "text/plain": "a=b"},
        }
    )


@pytest.mark.asyncio
async def test_drop_with_missing_file(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    locator = MagicMock()
    locator.drop = AsyncMock()
    use_refs(mock_session, locator)

    response = await handle_command(
        mock_state,
        {"command": "drop", "args": ["e1"], "options": {"path": "/no/such/file"}},
    )
    assert response["success"] is False
    assert "File not found" in response["output"]
    locator.drop.assert_not_awaited()


@pytest.mark.asyncio
async def test_state_load_applies_file_with_one_native_call(mock_state, mock_session, tmp_path):
    state_file = tmp_path / "state.json"
    state_file.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
    mock_state.sessions = {"default": mock_session}
    mock_session.context.set_storage_state = AsyncMock()
    mock_session.context.add_cookies = AsyncMock()
    mock_session.page.evaluate = AsyncMock()
    mock_session._passkey_lock = asyncio.Lock()
    mock_session._passkeys_installed = True

    response = await handle_command(mock_state, {"command": "state-load", "args": [str(state_file)]})

    assert response == {"success": True, "output": f"State loaded from {state_file}"}
    mock_session.context.set_storage_state.assert_awaited_once_with(str(state_file))
    # A state without credentials uninstalls the virtual authenticator.
    assert mock_session._passkeys_installed is False
    mock_session.context.add_cookies.assert_not_called()
    mock_session.page.evaluate.assert_not_called()


@pytest.mark.asyncio
async def test_state_load_missing_file(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session.context.set_storage_state = AsyncMock()
    response = await handle_command(mock_state, {"command": "state-load", "args": ["/no/such/state.json"]})
    assert response == {"success": False, "output": "File not found: /no/such/state.json"}
    mock_session.context.set_storage_state.assert_not_awaited()


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
    assert "HTTP version" not in response["output"]


def _request_entry(resp):
    return {
        "id": 0,
        "method": "GET",
        "url": "https://example.com/",
        "resource": "document",
        "ts": 1.0,
        "status": 200,
        "status_text": "OK",
        "_request": object(),
        "_response": resp,
    }


@pytest.mark.asyncio
async def test_request_shows_http_version(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    resp = MagicMock(http_version=AsyncMock(return_value="HTTP/2.0"))
    mock_session.network_log = [_request_entry(resp)]
    response = await handle_command(mock_state, {"command": "request", "args": ["0"]})
    assert response["success"] is True
    lines = response["output"].splitlines()
    assert lines[lines.index("- Status: 200 OK") + 1] == "- HTTP version: HTTP/2.0"


@pytest.mark.asyncio
async def test_request_omits_http_version_it_cannot_read(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    resp = MagicMock(http_version=AsyncMock(side_effect=Exception("Target closed")))
    mock_session.network_log = [_request_entry(resp)]
    response = await handle_command(mock_state, {"command": "request", "args": ["0"]})
    assert response["success"] is True
    assert "HTTP version" not in response["output"]


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
    mock_session.detach = AsyncMock()
    mock_state.sessions = {"default": mock_session}
    response = await handle_command(mock_state, {"command": "detach", "args": []})
    assert response["success"] is True
    assert "Detached" in response["output"]
    mock_session.detach.assert_awaited_once()
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
    mock_session.last_raw = None
    mock_state.sessions = {"default": mock_session}
    response = await handle_command(mock_state, {"command": "generate-locator", "args": ["e1"]})
    assert response["success"] is False
    assert "snapshot" in response["output"].lower()


@pytest.mark.asyncio
async def test_generate_locator_emits_role_name(mock_state, mock_session):
    raw = '- generic [ref=e1]:\n  - button "Sign in" [ref=e2]\n  - link [ref=e3]\n'
    locator = use_refs(mock_session, raw=raw)
    locator.element_handle = AsyncMock(return_value=MagicMock(dispose=AsyncMock()))
    single = MagicMock(count=AsyncMock(return_value=1))
    links = MagicMock(count=AsyncMock(return_value=3))
    links.nth = lambda i: MagicMock(evaluate=AsyncMock(return_value=i == 2))
    mock_session.page.get_by_role = MagicMock(side_effect=lambda role, **kw: single if role == "button" else links)
    mock_state.sessions = {"default": mock_session}

    r1 = await handle_command(mock_state, {"command": "generate-locator", "args": ["e2"]})
    assert r1["success"] is True
    assert r1["output"] == "getByRole('button', { name: 'Sign in', exact: true })"

    r2 = await handle_command(mock_state, {"command": "generate-locator", "args": ["e3"]})
    assert r2["success"] is True
    assert r2["output"] == "getByRole('link').nth(2)"


@pytest.mark.asyncio
async def test_generate_locator_escapes_quote(mock_state, mock_session):
    use_refs(mock_session, raw='- button "It\'s me" [ref=e1]\n')
    mock_session.page.get_by_role = MagicMock(return_value=MagicMock(count=AsyncMock(return_value=1)))
    mock_state.sessions = {"default": mock_session}

    r = await handle_command(mock_state, {"command": "generate-locator", "args": ["e1"]})
    assert r["success"] is True
    assert "It\\'s me" in r["output"]


def _highlight_locator(mock_session):
    locator = MagicMock()
    locator.highlight = AsyncMock()
    locator.hide_highlight = AsyncMock()
    use_refs(mock_session, locator)
    return locator


@pytest.mark.asyncio
async def test_highlight_clear_all(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session.page.hide_highlight = AsyncMock()
    mock_session.highlights = {id(mock_session.page): {"e1", "e2"}}
    response = await handle_command(mock_state, {"command": "highlight", "args": [], "options": {"hide": True}})
    assert response["success"] is True
    assert "cleared" in response["output"].lower()
    mock_session.page.hide_highlight.assert_awaited_once_with()
    assert mock_session.highlights[id(mock_session.page)] == set()


@pytest.mark.asyncio
async def test_highlight_show_passes_style_to_native_highlight(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    locator = _highlight_locator(mock_session)

    response = await handle_command(
        mock_state, {"command": "highlight", "args": ["e1"], "options": {"style": "outline: 4px solid red"}}
    )
    assert response == {"success": True, "output": "Highlighted e1."}
    locator.highlight.assert_awaited_once_with(style="outline: 4px solid red")
    assert mock_session.highlights[id(mock_session.page)] == {"e1"}


@pytest.mark.asyncio
async def test_highlight_show_without_style_uses_native_default(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    locator = _highlight_locator(mock_session)
    response = await handle_command(mock_state, {"command": "highlight", "args": ["e1"], "options": {}})
    assert response["success"] is True
    locator.highlight.assert_awaited_once_with(style=None)


@pytest.mark.asyncio
async def test_highlight_rejects_bare_style_flag(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    locator = _highlight_locator(mock_session)
    response = await handle_command(mock_state, {"command": "highlight", "args": ["e1"], "options": {"style": True}})
    assert response["success"] is False
    assert "--style" in response["output"]
    locator.highlight.assert_not_awaited()


@pytest.mark.asyncio
async def test_highlight_hide_ref_uses_native_hide(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    locator = _highlight_locator(mock_session)
    await handle_command(mock_state, {"command": "highlight", "args": ["e1"], "options": {}})
    response = await handle_command(mock_state, {"command": "highlight", "args": ["e1"], "options": {"hide": True}})
    assert response == {"success": True, "output": "Hid highlight on e1."}
    locator.hide_highlight.assert_awaited_once_with()
    assert mock_session.highlights[id(mock_session.page)] == set()


@pytest.mark.asyncio
async def test_snapshot_with_boxes(mock_state, mock_session, tmp_path):
    mock_state.sessions = {"default": mock_session}
    raw = '- button "Go" [ref=e1] [box=10,20,100,30]\n'
    with patch("patchright_cli.daemon.ai_snapshot", new_callable=AsyncMock) as mock_snap:
        mock_snap.return_value = raw
        response = await handle_command(
            mock_state,
            {"command": "snapshot", "args": [], "options": {"boxes": True}, "cwd": str(tmp_path)},
        )
    assert response["success"] is True
    mock_snap.assert_awaited_once_with(mock_session.page, boxes=True)
    assert Path(response["snapshot_path"]).read_text(encoding="utf-8") == raw


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
    return mock_session


FIND_RAW = (
    '- banner [ref=e1]:\n  - link "Sign in" [ref=e2] [cursor=pointer]\n  - link "Sign up" [ref=e3]\n'
    "  - text: Signal strength\n"
)


def _patch_snapshot(raw=FIND_RAW):
    """ai_snapshot is patched to return the page's raw AI snapshot, so no browser is needed."""
    return patch("patchright_cli.daemon.ai_snapshot", AsyncMock(return_value=raw))


@pytest.mark.asyncio
async def test_find_returns_matches(mock_state, find_session):
    mock_state.sessions = {"default": find_session}
    with _patch_snapshot():
        response = await handle_command(mock_state, {"command": "find", "args": ["Sign"], "options": {}})
    assert response["success"] is True
    assert "Sign in" in response["output"]
    assert "Sign up" in response["output"]


@pytest.mark.asyncio
async def test_find_excludes_text_nodes_by_default(mock_state, find_session):
    mock_state.sessions = {"default": find_session}
    with _patch_snapshot():
        response = await handle_command(mock_state, {"command": "find", "args": ["Sign"], "options": {}})
    assert "Signal strength" not in response["output"]


@pytest.mark.asyncio
async def test_find_all_flag_includes_text_nodes(mock_state, find_session):
    mock_state.sessions = {"default": find_session}
    with _patch_snapshot():
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
    with _patch_snapshot():
        response = await handle_command(
            mock_state, {"command": "find", "args": ["Sign (unclosed"], "options": {"regex": True}}
        )
    assert response["success"] is False
    assert "Invalid regex" in response["output"]


@pytest.mark.asyncio
async def test_find_invalid_limit_fails(mock_state, find_session):
    mock_state.sessions = {"default": find_session}
    response = await handle_command(mock_state, {"command": "find", "args": ["Sign"], "options": {"limit": "abc"}})
    assert response["success"] is False and response["exit_code"] == 2
    assert "--limit" in response["output"] and "1 or more" in response["output"]


@pytest.mark.asyncio
async def test_find_zero_limit_fails(mock_state, find_session):
    mock_state.sessions = {"default": find_session}
    response = await handle_command(mock_state, {"command": "find", "args": ["Sign"], "options": {"limit": "0"}})
    assert response["success"] is False and response["exit_code"] == 2
    assert "--limit" in response["output"] and "1 or more" in response["output"]


@pytest.mark.asyncio
async def test_find_bare_limit_flag_fails(mock_state, find_session):
    mock_state.sessions = {"default": find_session}
    response = await handle_command(mock_state, {"command": "find", "args": ["Sign"], "options": {"limit": True}})
    assert response["success"] is False and response["exit_code"] == 2
    assert "--limit" in response["output"] and "1 or more" in response["output"]


@pytest.mark.asyncio
async def test_find_rejects_pattern_given_twice(mock_state, find_session):
    mock_state.sessions = {"default": find_session}
    response = await handle_command(mock_state, {"command": "find", "args": ["Sign"], "options": {"regex": "Sign"}})
    assert response["success"] is False
    assert "a pattern twice" in response["output"]


@pytest.mark.asyncio
async def test_find_no_matches_succeeds(mock_state, find_session):
    mock_state.sessions = {"default": find_session}
    with _patch_snapshot():
        response = await handle_command(mock_state, {"command": "find", "args": ["absent"], "options": {}})
    assert response["success"] is True
    assert response["output"].startswith('0 matches for "absent".')
    assert response["data"] == []


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
    locator = MagicMock()
    locator.screenshot = AsyncMock()
    use_refs(mock_session, locator)

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

    locator = MagicMock()
    locator.bounding_box = AsyncMock(return_value={"x": 1, "y": 2, "width": 3, "height": 4})
    page = MagicMock()
    page.evaluate = AsyncMock()
    page.get_by_ref = MagicMock(return_value=locator)
    mock_session.last_raw = '- button "Sign in" [ref=e1]\n'

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
    mock_session.last_raw = None
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
    mock_session.page.get_by_ref = MagicMock(return_value=MagicMock(count=AsyncMock(return_value=1)))
    raw = '- generic [ref=e1]:\n  - main [ref=e2]:\n    - button "In" [ref=e3]\n  - button "Out" [ref=e4]\n'

    with (
        patch("patchright_cli.daemon.ai_snapshot", AsyncMock(return_value=raw)),
        patch("patchright_cli.daemon.probe_ref", AsyncMock(return_value="e2")) as probe,
    ):
        response = await handle_command(
            mock_state,
            {"command": "snapshot", "args": [], "options": {"selector": "#main"}, "cwd": str(tmp_path)},
        )

    assert response["success"] is True
    mock_session.page.locator.assert_called_once_with("#main")
    probe.assert_awaited_once_with(scoped)
    assert Path(response["snapshot_path"]).read_text(encoding="utf-8") == '- main [ref=e2]:\n  - button "In" [ref=e3]\n'


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
    """A multi-match selector used to report success while wiping the refs."""
    mock_state.sessions = {"default": mock_session}
    mock_session.last_raw = "- button [ref=e1]"
    scoped = MagicMock()
    scoped.count = AsyncMock(return_value=3)
    mock_session.page.locator = MagicMock(return_value=scoped)

    with patch("patchright_cli.daemon.ai_snapshot", new_callable=AsyncMock) as mock_snap:
        response = await handle_command(
            mock_state,
            {"command": "snapshot", "args": [], "options": {"selector": ".card"}, "cwd": str(tmp_path)},
        )

    assert response["success"] is False
    assert ".card" in response["output"]
    assert "3" in response["output"]
    mock_snap.assert_not_awaited()
    # The previous snapshot's refs must survive a rejected command.
    assert mock_session.last_raw == "- button [ref=e1]"


@pytest.mark.asyncio
async def test_list_works_when_named_session_is_not_open(mock_state):
    work = MagicMock()
    work.pages = [MagicMock()]
    mock_state.sessions = {"work": work}
    response = await handle_command(mock_state, {"command": "list", "args": []})
    assert response["success"] is True
    assert "work: 1 tab(s)" in response["output"]


@pytest.mark.asyncio
async def test_kill_all_stops_daemon_when_named_session_is_not_open(mock_state):
    import asyncio

    mock_state.sessions = {"work": MagicMock()}
    mock_state.close_session = AsyncMock()
    mock_state.shutdown_event = asyncio.Event()
    response = await handle_command(mock_state, {"command": "kill-all", "args": []})
    assert response["success"] is True
    mock_state.close_session.assert_awaited_once_with("work")
    assert mock_state.shutdown_event.is_set()


@pytest.mark.asyncio
async def test_close_all_works_when_named_session_is_not_open(mock_state):
    mock_state.sessions = {"work": MagicMock()}
    mock_state.close_session = AsyncMock()
    response = await handle_command(mock_state, {"command": "close-all", "args": []})
    assert response["success"] is True
    mock_state.close_session.assert_awaited_once_with("work")


# -- Console capture ---------------------------------------------------------
#
# Console comes from CDP because Patchright suppresses page.on('console'), but
# an enabled Runtime domain is itself detectable. Enabling it at attach time
# marks every page as automated, so these tests pin the lazy behaviour.


def _console_page():
    """A Page whose CDP session records the domains enabled on it."""
    cdp = MagicMock()
    cdp.send = AsyncMock()
    cdp.detach = AsyncMock()
    page = MagicMock()
    page.url = "https://example.com"
    page.is_closed = MagicMock(return_value=False)
    page.context = MagicMock()
    page.context.new_cdp_session = AsyncMock(return_value=cdp)
    return page, cdp


@pytest.mark.asyncio
async def test_attaching_a_page_sends_no_runtime_enable():
    page, cdp = _console_page()
    session = Session("default", page.context, [page])

    await session.setup_listeners()

    page.context.new_cdp_session.assert_not_awaited()
    cdp.send.assert_not_awaited()
    assert session._console_sessions == {}
    # The page-event listeners, which involve no CDP domain, stay.
    assert [call.args[0] for call in page.on.call_args_list] == ["request", "response", "dialog"]


@pytest.mark.asyncio
async def test_first_console_call_enables_runtime_exactly_once(mock_state):
    page, cdp = _console_page()
    session = Session("default", page.context, [page])
    await session.setup_listeners()
    mock_state.sessions = {"default": session}

    first = await handle_command(mock_state, {"command": "console", "args": []})
    assert first["success"] is True
    assert "earlier messages are unavailable" in first["output"]
    assert "detectable" in first["output"]
    cdp.send.assert_awaited_once_with("Runtime.enable")
    assert [call.args[0] for call in cdp.on.call_args_list] == ["Runtime.consoleAPICalled"]
    assert session._console_sessions == {id(page): cdp}

    second = await handle_command(mock_state, {"command": "console", "args": []})
    assert second["success"] is True
    assert "earlier messages" not in second["output"]
    cdp.send.assert_awaited_once_with("Runtime.enable")
    assert cdp.on.call_count == 1
    page.context.new_cdp_session.assert_awaited_once_with(page)


@pytest.mark.asyncio
async def test_console_listener_records_messages_after_capture_starts(mock_state):
    page, cdp = _console_page()
    session = Session("default", page.context, [page])
    mock_state.sessions = {"default": session}
    await handle_command(mock_state, {"command": "console", "args": []})

    listener = cdp.on.call_args.args[1]
    listener({"type": "warning", "args": [{"value": "careful"}]})
    listener({"type": "log", "args": [{"value": "hi"}, {"value": 3}]})

    assert [(m["type"], m["text"]) for m in session.console_messages] == [("warning", "careful"), ("log", "hi 3")]
    assert session.console_messages[0]["url"] == "https://example.com"
    response = await handle_command(mock_state, {"command": "console", "args": []})
    assert response["output"] == "[warning] careful\n[log] hi 3"


@pytest.mark.asyncio
async def test_console_reports_the_cdp_event_timestamp(mock_state):
    page, cdp = _console_page()
    session = Session("default", page.context, [page])
    mock_state.sessions = {"default": session}
    await handle_command(mock_state, {"command": "console", "args": []})

    listener = cdp.on.call_args.args[1]
    listener({"type": "log", "args": [{"value": "hi"}], "timestamp": 1762000000123.456})
    with patch("patchright_cli.daemon.time.time", return_value=1762000000.5):
        listener({"type": "log", "args": [{"value": "no stamp"}]})

    response = await handle_command(mock_state, {"command": "console", "args": []})
    assert [m["timestamp"] for m in response["data"]] == [1762000000123.456, 1762000000500.0]


@pytest.mark.asyncio
async def test_console_capture_retries_a_dead_cdp_session_once(mock_state):
    page, cdp = _console_page()
    page.context.new_cdp_session = AsyncMock(side_effect=[RuntimeError("Execution context was destroyed"), cdp])
    session = Session("default", page.context, [page])
    mock_state.sessions = {"default": session}

    response = await handle_command(mock_state, {"command": "console", "args": []})

    assert response["success"] is True
    assert page.context.new_cdp_session.await_count == 2
    cdp.send.assert_awaited_once_with("Runtime.enable")


@pytest.mark.asyncio
async def test_console_does_not_reuse_capture_after_the_page_closed(mock_state):
    page, cdp = _console_page()
    session = Session("default", page.context, [page])
    await session.setup_listeners()
    mock_state.sessions = {"default": session}
    await handle_command(mock_state, {"command": "console", "args": []})
    cdp.send.reset_mock()

    page.is_closed.return_value = True
    close_listener = next(call.args[1] for call in page.on.call_args_list if call.args[0] == "close")
    close_listener()
    response = await handle_command(mock_state, {"command": "console", "args": []})

    assert response["success"] is True
    cdp.send.assert_not_awaited()
    assert session._console_sessions == {}


@pytest.mark.asyncio
async def test_console_reports_capture_failure_and_retries_next_call(mock_state):
    page, cdp = _console_page()
    page.context.new_cdp_session = AsyncMock(side_effect=RuntimeError("Target page has been closed"))
    session = Session("default", page.context, [page])
    mock_state.sessions = {"default": session}

    response = await handle_command(mock_state, {"command": "console", "args": []})

    assert response["success"] is False
    assert response["data"] == []
    assert "0 console messages captured" in response["output"]
    assert "could not start" in response["output"]
    assert page.context.new_cdp_session.await_count == 2
    page.context.new_cdp_session.side_effect = None
    page.context.new_cdp_session.return_value = cdp
    assert (await handle_command(mock_state, {"command": "console", "args": []}))["success"] is True


@pytest.mark.asyncio
async def test_console_captures_all_existing_pages_and_future_popups():
    page, cdp = _console_page()
    sibling, sibling_cdp = _console_page()
    session = Session("default", page.context, [page, sibling], is_attached=True)
    await session.setup_listeners()
    sibling.context.new_cdp_session.assert_not_awaited()

    assert await session.start_console_capture()
    cdp.send.assert_awaited_once_with("Runtime.enable")
    sibling_cdp.send.assert_awaited_once_with("Runtime.enable")
    popup, popup_cdp = _console_page()

    async def enable(_):
        popup_cdp.on.call_args.args[1]({"type": "log", "args": [{"value": "popup startup"}]})

    popup_cdp.send.side_effect = enable
    await session._on_new_page(popup)
    popup_cdp.send.assert_awaited_once_with("Runtime.enable")
    assert session.console_messages[-1]["text"] == "popup startup"
    popup_cdp.on.call_args.args[1]({"type": "log", "args": [{"value": "popup"}]})
    assert session.console_messages[-1]["text"] == "popup"


@pytest.mark.asyncio
async def test_new_pages_and_named_tabs_are_clean_until_console_then_captured_before_navigation():
    page, _ = _console_page()
    session = Session("default", page.context, [page])
    early, early_cdp = _console_page()
    await session._on_new_page(early)
    early.context.new_cdp_session.assert_not_awaited()
    named, named_cdp = _console_page()
    page.context.new_page = AsyncMock(return_value=named)
    await session.open_tab("worker")
    named.context.new_cdp_session.assert_not_awaited()
    assert await session.start_console_capture()
    named_cdp.send.assert_awaited_once_with("Runtime.enable")

    later, later_cdp = _console_page()
    page.context.new_page.return_value = later
    assert (await session.open_tab("later")).page is later
    later_cdp.send.assert_awaited_once_with("Runtime.enable")
    # The context event can run after new_page() returns without double capture.
    await session._on_new_page(later)
    later.context.new_cdp_session.assert_awaited_once_with(later)
    reopened = Session("reopened", page.context, [later])
    await reopened.setup_listeners()
    assert not reopened._console_capture_enabled
    assert reopened._console_sessions == {}


@pytest.mark.asyncio
async def test_concurrent_console_commands_create_one_cdp_session():
    page, cdp = _console_page()
    session = Session("default", page.context, [page])
    started = asyncio.Event()
    finish = asyncio.Event()

    async def enable(_):
        started.set()
        await finish.wait()

    cdp.send.side_effect = enable
    first = asyncio.create_task(session.start_console_capture())
    await started.wait()
    second = asyncio.create_task(session.start_console_capture())
    popup_event = asyncio.create_task(session._on_new_page(page))
    finish.set()
    await asyncio.gather(first, second, popup_event)

    page.context.new_cdp_session.assert_awaited_once_with(page)
    cdp.send.assert_awaited_once_with("Runtime.enable")


@pytest.mark.asyncio
async def test_console_detaches_failed_cdp_and_ignores_replayed_messages():
    page, failed = _console_page()
    _, good = _console_page()
    page.context.new_cdp_session.side_effect = [failed, good]
    failed.send.side_effect = RuntimeError("Target went away")
    session = Session("default", page.context, [page])

    async def enable(_):
        good.on.call_args.args[1]({"type": "log", "args": [{"value": "before capture"}]})

    good.send.side_effect = enable
    assert await session.start_console_capture()
    failed.detach.assert_awaited_once()
    assert session.console_messages == []
    failed.on.call_args.args[1]({"type": "log", "args": [{"value": "stale event"}]})
    good.on.call_args.args[1]({"type": "log", "args": [{"value": "after capture"}]})
    assert [m["text"] for m in session.console_messages] == ["after capture"]


@pytest.mark.asyncio
async def test_console_page_closing_during_enable_is_detached_and_not_retained():
    page, cdp = _console_page()
    session = Session("default", page.context, [page])

    async def enable(_):
        page.is_closed.return_value = True

    cdp.send.side_effect = enable
    assert not await session.start_console_capture()
    assert session._console_sessions == {}
    cdp.detach.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_locator_flags_too_many_matches(mock_state, mock_session):
    use_refs(mock_session, raw='- button "Buy" [ref=e1]\n')
    mock_session.page.get_by_role = MagicMock(return_value=MagicMock(count=AsyncMock(return_value=25)))
    mock_state.sessions = {"default": mock_session}

    r = await handle_command(mock_state, {"command": "generate-locator", "args": ["e1"]})
    assert r["output"] == "getByRole('button', { name: 'Buy', exact: true })  # ambiguous: 25 matches"
