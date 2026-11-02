"""find --role/--name/--description/--exact, html, and video-start --fps (issue #41)."""

import re
from functools import partial
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from patchright_cli import cli, daemon, mcp_server
from patchright_cli.daemon import DaemonState, Session, handle_command
from patchright_cli.ref_registry import RefRegistry


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
    session.page.url = "https://example.com/"
    session.page.title = AsyncMock(return_value="Example")
    session._codegen = None
    session.highlights = {}
    session._video_recording = False
    session._video_cdp = None
    session._video_show_actions = None
    session.tabs = {"default": MagicMock()}
    session.activate_tab = MagicMock()
    session.uses_host_context = False
    session.network_guard = None
    session.goto = partial(Session.goto, session)
    session.check_navigation = partial(Session.check_navigation, session)
    return session


async def _run(state, command, args=(), **options):
    return await handle_command(state, {"command": command, "args": list(args), "options": options})


# -- find --role ---------------------------------------------------------------


def _role_page(session, count, refs):
    """page.get_by_role yields `count` matches; _ref_for_element maps them to `refs` in turn."""
    registry = RefRegistry()
    registry.parse('- button "Delete"\n- button "Delete"\n- button "Save"\n')
    locator = MagicMock()
    locator.count = AsyncMock(return_value=count)
    locator.nth = MagicMock(side_effect=lambda i: f"match-{i}")
    session.page.get_by_role = MagicMock(return_value=locator)
    snap = patch("patchright_cli.daemon.take_snapshot", AsyncMock(return_value=("ignored", registry)))
    mapper = patch("patchright_cli.daemon._ref_for_element", AsyncMock(side_effect=list(refs)))
    return snap, mapper


@pytest.mark.asyncio
async def test_find_role_passes_filters_to_get_by_role(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    snap, mapper = _role_page(mock_session, 2, ["e1", "e2"])
    with snap, mapper:
        response = await _run(mock_state, "find", role="button", description="Deletes")
    assert response["success"] is True
    mock_session.page.get_by_role.assert_called_once_with("button", description="Deletes")
    assert response["data"] == [
        {"ref": "e1", "role": "button", "name": "Delete"},
        {"ref": "e2", "role": "button", "name": "Delete"},
    ]
    assert 'Found 2 matches for role=button description="Deletes".' in response["output"]
    assert '- button "Delete" [ref=e2]' in response["output"]


@pytest.mark.asyncio
async def test_find_role_slash_value_is_a_regex_and_exact_is_passed(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    snap, mapper = _role_page(mock_session, 1, ["e3"])
    with snap, mapper:
        response = await _run(mock_state, "find", role="button", name="/^sav/i", exact=True)
    assert response["success"] is True
    _, kwargs = mock_session.page.get_by_role.call_args
    assert kwargs["exact"] is True
    assert kwargs["name"].pattern == "^sav"
    assert kwargs["name"].flags & re.IGNORECASE


@pytest.mark.asyncio
async def test_find_role_lists_a_match_without_a_ref(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    snap, mapper = _role_page(mock_session, 1, [None])
    with snap, mapper:
        response = await _run(mock_state, "find", role="button")
    assert response["data"] == [{"ref": "", "role": "button", "name": ""}]
    assert "(no ref: not in the snapshot; nth=0)" in response["output"]


@pytest.mark.asyncio
async def test_find_role_honours_limit_and_reports_total(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    snap, mapper = _role_page(mock_session, 3, ["e1"])
    with snap, mapper:
        response = await _run(mock_state, "find", role="button", limit="1")
    assert response["total"] == 3
    assert len(response["data"]) == 1
    assert "Found 1 of 3 matches" in response["output"]


@pytest.mark.asyncio
async def test_find_role_no_matches_skips_the_snapshot(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    snap, mapper = _role_page(mock_session, 0, [])
    with snap as taken, mapper:
        response = await _run(mock_state, "find", role="button", description="nothing")
    assert response["success"] is True
    assert response["data"] == []
    assert response["output"].startswith('0 matches for role=button description="nothing".')
    taken.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("args", "options", "message"),
    [
        (["Sign"], {"description": "x"}, "--description only works with --role=<role>."),
        (["Sign"], {"exact": True, "name": "x"}, "--name, --exact only works with --role=<role>."),
        (["Sign"], {"role": "button"}, "it takes no <text>, --regex or --all"),
        ([], {"role": "button", "all": True}, "it takes no <text>, --regex or --all"),
        ([], {"role": True}, "Give --role an ARIA role"),
        ([], {"role": "button", "name": True}, "Give --name a value"),
        ([], {"role": "button", "description": "/[/"}, "Invalid --description regex"),
    ],
)
async def test_find_role_rejects_bad_combinations(mock_state, mock_session, args, options, message):
    mock_state.sessions = {"default": mock_session}
    mock_session.page.get_by_role = MagicMock()
    response = await _run(mock_state, "find", args, **options)
    assert response["success"] is False
    assert message in response["output"]


@pytest.mark.asyncio
async def test_ref_for_element_picks_the_identical_candidate():
    registry = RefRegistry()
    registry.parse('- button "Delete"\n- button "Delete"\n- button "Save"\n')
    target = MagicMock()
    target.aria_snapshot = AsyncMock(return_value='- button "Delete"')
    handle = MagicMock()
    handle.dispose = AsyncMock()
    target.element_handle = AsyncMock(return_value=handle)
    page = MagicMock()
    resolved = {}

    def resolve(_page, ref):
        loc = MagicMock()
        loc.evaluate = AsyncMock(return_value=ref == "e2")
        resolved[ref] = loc
        return loc

    with patch.object(registry, "resolve", side_effect=resolve):
        assert await daemon._ref_for_element(registry, page, target) == "e2"
    assert set(resolved) == {"e1", "e2"}  # only same role and name are checked
    handle.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_ref_for_element_without_candidates_is_none():
    registry = RefRegistry()
    registry.parse('- button "Save"\n')
    target = MagicMock()
    target.aria_snapshot = AsyncMock(return_value='- link "Home"')
    assert await daemon._ref_for_element(registry, MagicMock(), target) is None


def test_find_role_needs_no_text_on_the_command_line(monkeypatch, capsys):
    from tests.test_cli_axi import _run as run_cli

    code, _, _, sent = run_cli(["find", "--role=button", "--description=Deletes"], monkeypatch, capsys)
    assert (code, sent["command"], sent["args"]) == (0, "find", [])
    assert sent["options"]["description"] == "Deletes"
    for argv in (["find", "--description=Deletes"], ["find", "--name=x", "--exact"], ["find"]):
        code, out, _, sent = run_cli(argv, monkeypatch, capsys)
        assert (code, sent) == (2, {})
        assert out.startswith("error: `find` requires <text> or --role=<role>\n")
        assert "--name, --description and --exact only apply with --role" in out


# -- html ------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("shadow", [False, True])
async def test_html_whole_page_passes_include_shadow(mock_state, mock_session, shadow):
    mock_state.sessions = {"default": mock_session}
    mock_session.page.content = AsyncMock(return_value="<html></html>")
    options = {"include-shadow": True} if shadow else {}
    response = await _run(mock_state, "html", **options)
    assert response == {"success": True, "output": "<html></html>"}
    mock_session.page.content.assert_awaited_once_with(include_shadow=shadow)


@pytest.mark.asyncio
async def test_html_truncates_and_points_at_filename(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session.page.content = AsyncMock(return_value="x" * 9000)
    response = await _run(mock_state, "html")
    assert response["output"].startswith("x" * daemon.DEFAULT_HTML_MAX_CHARS + "\n... (truncated: 8000 of 9000 chars;")
    assert "--filename=<name>" in response["output"]
    whole = await _run(mock_state, "html", **{"max-chars": "0"})
    assert whole["output"] == "x" * 9000
    short = await _run(mock_state, "html", **{"max-chars": "10"})
    assert short["output"].startswith("x" * 10 + "\n... (truncated: 10 of 9000")


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["-1", "abc", True])
async def test_html_rejects_bad_max_chars(mock_state, mock_session, bad):
    mock_state.sessions = {"default": mock_session}
    response = await _run(mock_state, "html", **{"max-chars": bad})
    assert response["success"] is False
    assert "Invalid --max-chars" in response["output"]


@pytest.mark.asyncio
async def test_html_filename_writes_everything(mock_state, mock_session, tmp_path):
    mock_state.sessions = {"default": mock_session}
    mock_session.page.content = AsyncMock(return_value="y" * 9000)
    response = await handle_command(
        mock_state,
        {"command": "html", "args": [], "options": {"filename": "page.html"}, "cwd": str(tmp_path)},
    )
    saved = tmp_path / ".patchright-cli" / "page.html"
    assert response["success"] is True
    assert str(saved) in response["output"]
    assert saved.read_text(encoding="utf-8") == "y" * 9000


@pytest.mark.asyncio
async def test_html_selector_serializes_one_element(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    scoped = MagicMock()
    scoped.count = AsyncMock(return_value=1)
    scoped.evaluate = AsyncMock(return_value="<main></main>")
    mock_session.page.locator = MagicMock(return_value=scoped)
    response = await _run(mock_state, "html", selector="main", **{"include-shadow": True})
    assert response == {"success": True, "output": "<main></main>"}
    scoped.evaluate.assert_awaited_once_with(daemon._ELEMENT_HTML_JS, True)


@pytest.mark.asyncio
async def test_html_ref_serializes_the_ref(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    registry = MagicMock()
    elem = MagicMock()
    elem.evaluate = AsyncMock(return_value="<button>Delete</button>")
    registry.resolve.return_value = elem
    mock_session.ref_registry = registry
    response = await _run(mock_state, "html", ["e3"])
    assert response["output"] == "<button>Delete</button>"
    registry.resolve.assert_called_once_with(mock_session.page, "e3")
    elem.evaluate.assert_awaited_once_with(daemon._ELEMENT_HTML_JS, False)


@pytest.mark.asyncio
@pytest.mark.parametrize(("count", "message"), [(0, "No element matches"), (3, "matches 3 elements")])
async def test_html_selector_needs_exactly_one_match(mock_state, mock_session, count, message):
    mock_state.sessions = {"default": mock_session}
    scoped = MagicMock()
    scoped.count = AsyncMock(return_value=count)
    mock_session.page.locator = MagicMock(return_value=scoped)
    response = await _run(mock_state, "html", selector="button")
    assert response["success"] is False
    assert message in response["output"]


@pytest.mark.asyncio
async def test_html_ref_and_selector_together_fail(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    response = await _run(mock_state, "html", ["e1"], selector="main")
    assert response["success"] is False
    assert "not both" in response["output"]


def test_html_rejects_unknown_flags():
    with pytest.raises(cli.UsageError, match="unknown flag --shadow for `html`"):
        cli._validate_flags("html", {"shadow": True})
    cli._validate_flags("html", {"include-shadow": True, "max-chars": "0", "filename": "x", "selector": "y"})


def test_html_unknown_flag_exits_2(monkeypatch, capsys):
    from tests.test_cli_axi import _run as run_cli

    code, out, _, sent = run_cli(["html", "--shadow"], monkeypatch, capsys)
    assert code == 2
    assert sent == {}
    assert out.startswith("error: unknown flag --shadow for `html`")


# -- video-start --fps -----------------------------------------------------------


@pytest.mark.parametrize(("raw", "fps"), [("25", 25), (1, 1), ("60", 60), ("0", None), ("61", None), ("2.5", None)])
def test_parse_video_fps(raw, fps):
    assert daemon._parse_video_fps(raw) == fps


def test_parse_video_fps_rejects_a_bare_flag():
    assert daemon._parse_video_fps(True) is None


def test_video_concat_list_keeps_each_frame_until_the_next():
    text = daemon._video_concat_list([10.0, 10.5, 12.0], 13.0, 25)
    assert text.splitlines() == [
        "file 'frame-000000.jpg'",
        "duration 0.500000",
        "file 'frame-000001.jpg'",
        "duration 1.500000",
        "file 'frame-000002.jpg'",
        "duration 1.000000",
        "file 'frame-000002.jpg'",
    ]
    assert daemon._video_length([10.0, 10.5, 12.0], 13.0, 25) == 3.0


def test_video_length_is_at_least_one_frame():
    assert daemon._video_length([5.0], 5.0, 25) == pytest.approx(0.04)
    assert "duration 0.040000" in daemon._video_concat_list([5.0], 5.0, 25)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["0", "abc", "120", True])
async def test_video_start_rejects_bad_fps(mock_state, mock_session, bad):
    mock_state.sessions = {"default": mock_session}
    mock_session.page.context.new_cdp_session = AsyncMock()
    response = await _run(mock_state, "video-start", fps=bad)
    assert response["success"] is False
    assert "Invalid --fps value" in response["output"]
    mock_session.page.context.new_cdp_session.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(("options", "fps", "output"), [({}, None, "started."), ({"fps": "30"}, 30, "at 30 fps.")])
async def test_video_start_records_the_fps(mock_state, mock_session, options, fps, output):
    mock_state.sessions = {"default": mock_session}
    cdp = MagicMock()
    cdp.send = AsyncMock()
    mock_session.page.context.new_cdp_session = AsyncMock(return_value=cdp)
    response = await _run(mock_state, "video-start", **options)
    assert response["success"] is True
    assert response["output"].endswith(output)
    assert mock_session._video_fps == fps
    assert mock_session._video_times == []
    # Same screencast either way: --fps only changes how video-stop encodes.
    cdp.send.assert_awaited_once_with(
        "Page.startScreencast", {"format": "jpeg", "quality": 80, "maxWidth": 1280, "maxHeight": 720}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fps", "expected"),
    [
        (None, ["-framerate", "5", "-i"]),
        (25, ["-f", "concat", "-safe", "0", "-i"]),
    ],
)
async def test_video_stop_encodes_by_fps(mock_state, mock_session, tmp_path, fps, expected):
    mock_state.sessions = {"default": mock_session}
    cdp = MagicMock()
    cdp.send = AsyncMock()
    mock_session._video_recording = True
    mock_session._video_cdp = cdp
    mock_session._video_frames = [b"a", b"b"]
    mock_session._video_times = [1.0, 2.0]
    mock_session._video_fps = fps
    mock_session._video_chapters = []
    calls = []

    class Proc:
        returncode = 0

        async def wait(self):
            return 0

    async def spawn(*command, **kwargs):
        calls.append(list(command))
        return Proc()

    with (
        patch("shutil.which", return_value="ffmpeg"),
        patch("asyncio.create_subprocess_exec", spawn),
        patch("patchright_cli.daemon.time.time", return_value=4.0),
    ):
        response = await handle_command(
            mock_state, {"command": "video-stop", "args": [], "options": {}, "cwd": str(tmp_path)}
        )
    assert response["success"] is True
    (command,) = calls
    assert command[2 : 2 + len(expected)] == expected
    if fps:
        assert command[command.index("-vf") + 1] == "fps=25"
        assert command[command.index("-t") + 1] == "3.000"
    else:
        assert "-vf" not in command


def test_video_start_fps_flag_is_known():
    cli._validate_flags("video-start", {"fps": "25"})
    with pytest.raises(cli.UsageError):
        cli._validate_flags("video-start", {"framerate": "25"})


# -- MCP tools -------------------------------------------------------------------


def _mcp_defaults(tool, **overrides):
    params = {name: default for name, _, _, default, _ in mcp_server.TOOL_SPECS[tool]["params"]}
    params.update(overrides)
    return params


def test_mcp_find_maps_role_filters():
    args = mcp_server._build_args(
        "patchright_find", _mcp_defaults("patchright_find", role="button", description="Deletes", exact=True)
    )
    assert args == ["find", "--limit=20", "--role=button", "--description=Deletes", "--exact"]
    with pytest.raises(ValueError, match="Either text or role"):
        mcp_server._build_args("patchright_find", _mcp_defaults("patchright_find"))


def test_mcp_html_and_video_start_map_their_flags():
    args = mcp_server._build_args(
        "patchright_html", _mcp_defaults("patchright_html", include_shadow=True, selector="main", max_chars=0)
    )
    assert args == ["html", "--selector=main", "--include-shadow", "--max-chars=0"]
    assert mcp_server._build_args("patchright_video_start", _mcp_defaults("patchright_video_start")) == ["video-start"]
    assert mcp_server._build_args("patchright_video_start", _mcp_defaults("patchright_video_start", fps=25)) == [
        "video-start",
        "--fps=25",
    ]
    with pytest.raises(ValueError, match="fps must be"):
        mcp_server._build_args("patchright_video_start", _mcp_defaults("patchright_video_start", fps=90))
