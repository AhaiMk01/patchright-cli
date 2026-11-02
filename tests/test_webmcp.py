"""Unit tests for WebMCP: `open --webmcp`, `webmcp-list` and `webmcp-call`."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from patchright_cli import daemon, mcp_server
from patchright_cli.daemon import DaemonState, _enable_chrome_feature, _webmcp_input, handle_command
from tests.test_cli_axi import _run_reply
from tests.test_daemon_axi import mock_session, mock_state  # noqa: F401 (fixtures)
from tests.test_mcp import _defaults
from tests.test_profiles import _fake_context

TOOLS = [
    {
        "name": "add",
        "description": "Add two numbers",
        "inputSchema": {"type": "object", "properties": {"a": {"type": "number"}}},
        "annotations": {"readOnly": True, "consequential": False},
    },
    {"name": "refuse", "description": "Always fails"},
]


async def _run(state, command, args=None, **options):
    return await handle_command(state, {"command": command, "args": args or [], "options": options})


# -- open --webmcp: the Chrome feature switch ------------------------------------------


def test_enable_feature_appends_a_switch():
    assert _enable_chrome_feature(["--x"], "WebMCP") == ["--x", "--enable-features=WebMCP"]


def test_enable_feature_merges_into_an_existing_switch():
    args = ["--a", "--enable-features=Foo,Bar", "--b"]
    assert _enable_chrome_feature(args, "WebMCP") == ["--a", "--enable-features=Foo,Bar,WebMCP", "--b"]


def test_enable_feature_already_on_is_unchanged():
    args = ["--enable-features=WebMCP,Foo"]
    assert _enable_chrome_feature(args, "WebMCP") == args


@pytest.fixture
def launch_state(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon, "PROFILES_ROOT", tmp_path / "profiles")
    st = DaemonState()
    st.playwright = MagicMock()
    st.playwright.devices = {}
    st.playwright.chromium.launch_persistent_context = AsyncMock(side_effect=lambda *a, **k: _fake_context())
    return st


def _launch_args(state):
    return state.playwright.chromium.launch_persistent_context.await_args.kwargs["args"]


@pytest.mark.asyncio
async def test_launch_without_webmcp_leaves_the_feature_off(launch_state):
    session = await launch_state.get_or_create_session("plain")
    assert not any(a.startswith("--enable-features") for a in _launch_args(launch_state))
    assert session.webmcp is False


@pytest.mark.asyncio
async def test_launch_with_webmcp_turns_the_feature_on_once(launch_state):
    session = await launch_state.get_or_create_session("mcp", webmcp=True)
    switches = [a for a in _launch_args(launch_state) if a.startswith("--enable-features=")]
    assert switches == ["--enable-features=WebMCP"]
    assert session.webmcp is True


@pytest.mark.asyncio
async def test_open_passes_the_webmcp_flag(launch_state, monkeypatch):
    monkeypatch.setattr(daemon, "_page_info", AsyncMock(return_value={"success": True, "output": ""}))
    await handle_command(launch_state, {"command": "open", "args": [], "options": {"session": "s", "webmcp": True}})
    assert "--enable-features=WebMCP" in _launch_args(launch_state)


@pytest.mark.asyncio
async def test_webmcp_on_a_session_opened_without_it_is_refused(launch_state):
    await launch_state.get_or_create_session("plain")
    with pytest.raises(ValueError, match="already open without WebMCP"):
        await launch_state.get_or_create_session("plain", webmcp=True)
    assert launch_state.playwright.chromium.launch_persistent_context.await_count == 1


# -- webmcp-list ------------------------------------------------------------------------


@pytest.fixture
def webmcp_session(mock_state, mock_session):  # noqa: F811
    mock_state.sessions = {"default": mock_session}
    mock_session.page.webmcp = MagicMock()
    mock_session.page.webmcp.tools = AsyncMock(return_value=TOOLS)
    mock_session.page.webmcp.call_tool = AsyncMock()
    return mock_state, mock_session


@pytest.mark.asyncio
async def test_list_renders_tools_and_data(webmcp_session):
    state, _ = webmcp_session
    reply = await _run(state, "webmcp-list")
    assert reply["success"] is True
    lines = reply["output"].splitlines()
    assert lines[0].startswith("2 webmcp tool(s).") and "untrusted" in lines[0]
    assert lines[1:] == [
        "- add [readOnly]: Add two numbers",
        '  - inputSchema: {"type":"object","properties":{"a":{"type":"number"}}}',
        "- refuse: Always fails",
    ]
    assert reply["data"] == [
        {
            "name": "add",
            "description": "Add two numbers",
            "inputSchema": {"type": "object", "properties": {"a": {"type": "number"}}},
            "annotations": {"readOnly": True},
        },
        {"name": "refuse", "description": "Always fails"},
    ]


@pytest.mark.asyncio
async def test_list_empty_on_an_insecure_page_says_why(webmcp_session):
    state, session = webmcp_session
    session.page.webmcp.tools.return_value = []
    session.page.url = "data:text/html,x"
    session.page.evaluate.return_value = False
    reply = await _run(state, "webmcp-list")
    assert reply["data"] == []
    assert reply["output"].splitlines()[0] == "0 webmcp tools on this page"
    assert "secure context" in reply["output"]


@pytest.mark.asyncio
async def test_list_empty_on_a_secure_page_has_no_hint(webmcp_session):
    state, session = webmcp_session
    session.page.webmcp.tools.return_value = []
    session.page.evaluate.return_value = True
    reply = await _run(state, "webmcp-list")
    assert reply == {"success": True, "output": "0 webmcp tools on this page", "data": []}


@pytest.mark.asyncio
async def test_list_without_the_feature_tells_how_to_enable(webmcp_session):
    state, session = webmcp_session
    session.page.webmcp.tools.side_effect = Exception(
        'WebMCP.tools: WebMCP is not enabled. Launch the browser with the "--enable-features=WebMCP" argument.'
    )
    reply = await _run(state, "webmcp-list")
    assert reply["success"] is False
    assert reply["output"].startswith("Error: WebMCP is not enabled in this browser.")
    assert "open --webmcp" in reply["output"]
    assert "--enable-features=WebMCP" in reply["output"]


# -- webmcp-call ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "options", "expected"),
    [
        (["add"], {}, {}),
        (["add", '{"a": 1}'], {}, {"a": 1}),
        (["add"], {"params": '{"a": 2}'}, {"a": 2}),
        # `--params '{...}'` with a space: a bare flag plus a positional.
        (["add", '{"a": 3}'], {"params": True}, {"a": 3}),
        (["add", "  "], {}, {}),
    ],
)
def test_input_sources(args, options, expected):
    assert _webmcp_input(args, options) == expected


@pytest.mark.parametrize(
    ("args", "options", "message"),
    [
        (["add", "{a:1}"], {}, "not valid JSON"),
        (["add", "[1, 2]"], {}, "must be a JSON object, got list"),
        (["add", "{}"], {"params": "{}"}, "not both"),
        (["add", "{}", "{}"], {}, "expected one JSON argument"),
    ],
)
def test_input_errors(args, options, message):
    with pytest.raises(ValueError, match=message):
        _webmcp_input(args, options)


@pytest.mark.asyncio
async def test_call_returns_the_result(webmcp_session):
    state, session = webmcp_session
    result = {"content": [{"type": "text", "text": "3"}]}
    session.page.webmcp.call_tool.return_value = result
    reply = await _run(state, "webmcp-call", ["add", '{"a": 1, "b": 2}'])
    session.page.webmcp.call_tool.assert_awaited_once_with("add", {"a": 1, "b": 2})
    assert reply["success"] is True
    assert reply["data"] == result
    assert reply["output"].startswith('Called WebMCP tool "add".')
    assert '"text": "3"' in reply["output"]


@pytest.mark.asyncio
async def test_call_with_an_error_result_fails(webmcp_session):
    state, session = webmcp_session
    session.page.webmcp.call_tool.return_value = {"isError": True, "content": []}
    reply = await _run(state, "webmcp-call", ["refuse"])
    assert reply["success"] is False
    assert reply["output"].startswith('WebMCP tool failed "refuse".')


@pytest.mark.asyncio
async def test_call_bad_json_never_reaches_the_page(webmcp_session):
    state, session = webmcp_session
    reply = await _run(state, "webmcp-call", ["add", "{oops"])
    assert reply["success"] is False
    assert reply["output"].startswith("Error: tool input is not valid JSON")
    assert "help: patchright-cli webmcp-call add" in reply["output"]
    session.page.webmcp.call_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_call_unknown_tool_points_at_webmcp_list(webmcp_session):
    state, session = webmcp_session
    session.page.webmcp.call_tool.side_effect = Exception(
        'WebMCP.call_tool: No WebMCP tool named "nope". Available tools: add, refuse.\nCall log: ...'
    )
    reply = await _run(state, "webmcp-call", ["nope"])
    assert reply == {
        "success": False,
        "output": 'Error: No WebMCP tool named "nope". Available tools: add, refuse.\n'
        "help: run `patchright-cli webmcp-list` to see the page's tools",
    }


# -- CLI and MCP surface ----------------------------------------------------------------


def test_cli_rejects_unknown_flags(monkeypatch, capsys):
    code, out = _run_reply(["webmcp-call", "add", "--frame=x"], {"success": True}, monkeypatch, capsys)
    assert code == 2
    assert "unknown flag --frame for `webmcp-call`" in out


def test_cli_webmcp_call_requires_a_name(monkeypatch, capsys):
    code, out = _run_reply(["webmcp-call"], {"success": True}, monkeypatch, capsys)
    assert code == 2
    assert "`webmcp-call` requires <name>" in out


def test_toon_lists_tools(monkeypatch, capsys):
    reply = {"success": True, "output": "...", "data": [{"name": "add", "description": "Add"}]}
    code, out = _run_reply(["--toon", "webmcp-list"], reply, monkeypatch, capsys)
    assert code == 0
    assert out.splitlines() == ["tools[1]{name,description}:", "  add,Add"]


def test_mcp_tools_build_args():
    assert mcp_server._build_args("patchright_webmcp_list", _defaults("patchright_webmcp_list")) == ["webmcp-list"]
    call = _defaults("patchright_webmcp_call", name="add", params='{"a":1}')
    assert mcp_server._build_args("patchright_webmcp_call", call) == ["webmcp-call", "add", '{"a":1}']
    bare = _defaults("patchright_webmcp_call", name="add")
    assert mcp_server._build_args("patchright_webmcp_call", bare) == ["webmcp-call", "add"]
    opened = _defaults("patchright_open", headless=False, webmcp=True)
    assert mcp_server._build_args("patchright_open", opened)[:2] == ["open", "--webmcp"]
