"""Browserless tests for the optional MCP wrapper."""

import pytest

from patchright_cli import cli, mcp_server


def test_attach_builds_host_context_args():
    cdp_url = "http://browser.example.test:9222"
    args = mcp_server._build_args(
        "patchright_attach",
        {
            "cdp_url": cdp_url,
            "headless": True,
            "context": "host",
            "cdp_timeout": 30000,
            "session": "default",
        },
    )
    assert args == [
        "attach",
        "--headless",
        f"--cdp={cdp_url}",
        "--cdp-timeout=30000",
        "--context=host",
        "--block-networks=link-local",
    ]


def test_attach_rejects_unknown_context():
    try:
        mcp_server._build_args(
            "patchright_attach",
            {
                "cdp_url": "http://browser.example.test:9222",
                "headless": True,
                "context": "other",
                "cdp_timeout": 30000,
                "session": "default",
            },
        )
    except ValueError as exc:
        assert str(exc) == "context must be 'new' or 'host'"
    else:
        raise AssertionError("invalid context was accepted")


def test_run_mcp_server_defaults_to_stdio(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp_server.server, "run", lambda **kwargs: calls.append(kwargs))

    mcp_server.run_mcp_server()

    assert calls == [{"transport": "stdio"}]


def test_run_mcp_server_http(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp_server.server, "run", lambda **kwargs: calls.append(kwargs))

    mcp_server.run_mcp_server(http=True, host="0.0.0.0", port=9090)

    assert calls == [{"transport": "http", "host": "0.0.0.0", "port": 9090}]


def test_cli_mcp_http_options(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp_server, "run_mcp_server", lambda **kwargs: calls.append(kwargs))

    cli._handle_mcp(["--http", "--host=0.0.0.0", "--port=9090"])

    assert calls == [{"http": True, "host": "0.0.0.0", "port": 9090}]


def test_cli_output_round_trips_non_ascii(tmp_path, monkeypatch):
    import asyncio
    import sys

    monkeypatch.setattr(mcp_server, "CLI", (sys.executable, "-c", "print('Example — Domain')"))
    monkeypatch.setattr(mcp_server, "OUTPUT_ROOT", tmp_path)
    out = asyncio.run(mcp_server._run_patchright(["tab-list"]))
    assert out.strip() == "Example — Domain"


def _defaults(tool_name, **overrides):
    params = {name: default for name, _, _, default, _ in mcp_server.TOOL_SPECS[tool_name]["params"]}
    params.update(overrides)
    return params


def test_snapshot_with_filename_builds_args(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_server, "MCP_ROOT", tmp_path)
    target = tmp_path / "snap.yml"
    args = mcp_server._build_args(
        "patchright_snapshot", _defaults("patchright_snapshot", filename=str(target), depth=3)
    )
    assert args == ["snapshot", f"--filename={target}", "--depth=3"]


def test_open_accepts_profile_name():
    args = mcp_server._build_args("patchright_open", _defaults("patchright_open", profile="work", headless=False))
    assert args == ["--profile=work", "open", "--block-networks=link-local"]


def test_open_rejects_profile_dir_outside_root(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_server, "MCP_ROOT", tmp_path / "root")
    outside = str(tmp_path / "elsewhere" / "profile")
    try:
        mcp_server._build_args("patchright_open", _defaults("patchright_open", profile=outside))
    except ValueError as exc:
        assert "beneath" in str(exc)
    else:
        raise AssertionError("profile directory outside the MCP root was accepted")


def test_unrestricted_filesystem_skips_path_check(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_server, "MCP_ROOT", tmp_path / "root")
    monkeypatch.setattr(mcp_server, "UNRESTRICTED_FILESYSTEM", True)
    outside = str(tmp_path / "elsewhere" / "state.json")
    args = mcp_server._build_args("patchright_state_load", _defaults("patchright_state_load", path=outside))
    assert args == ["state-load", outside]


def test_install_skills_requires_unrestricted_filesystem(monkeypatch):
    monkeypatch.setattr(mcp_server, "UNRESTRICTED_FILESYSTEM", False)
    try:
        mcp_server._build_args("patchright_install_skills", {})
    except ValueError:
        pass
    else:
        raise AssertionError("install_skills ran inside the filesystem sandbox")


@pytest.mark.parametrize(
    ("tool", "field"),
    [("patchright_open", "url"), ("patchright_goto", "url"), ("patchright_tab_new", "url")],
)
@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "FILE:///root/.patchright-cli",
        "chrome://settings",
        "chrome://version",
        "javascript:1",
        "data:text/html,x",
    ],
)
def test_browser_urls_must_be_http(tool, field, url):
    with pytest.raises(ValueError, match="URL scheme"):
        mcp_server._build_args(tool, _defaults(tool, **{field: url}))


def test_browser_urls_allow_http_and_blank():
    for url in ["https://example.com", "http://example.com/a?b=c", "about:blank"]:
        assert mcp_server._build_args("patchright_goto", _defaults("patchright_goto", url=url)) == ["goto", url]


def test_unrestricted_filesystem_allows_file_urls(monkeypatch):
    monkeypatch.setattr(mcp_server, "UNRESTRICTED_FILESYSTEM", True)
    args = mcp_server._build_args("patchright_goto", _defaults("patchright_goto", url="file:///tmp/a.html"))
    assert args == ["goto", "file:///tmp/a.html"]


def test_attach_rejects_non_cdp_schemes():
    with pytest.raises(ValueError, match="URL scheme"):
        mcp_server._build_args("patchright_attach", _defaults("patchright_attach", cdp_url="ftp://10.0.0.1/"))


def test_block_networks_none_drops_the_flag(monkeypatch):
    monkeypatch.setattr(mcp_server, "BLOCK_NETWORKS", "none")
    assert mcp_server._build_args("patchright_open", _defaults("patchright_open", headless=False)) == ["open"]


def test_tab_select_requires_index():
    assert mcp_server.TOOL_SPECS["patchright_tab_select"]["params"][0][2] is True
    assert mcp_server._build_args("patchright_tab_select", {"index": 2, "session": "default"}) == ["tab-select", "2"]


def test_every_cli_command_has_a_tool():
    commands = set(cli.ALL_COMMANDS) - {"mcp", "install"}
    tools = {name.removeprefix("patchright_").replace("_", "-") for name in mcp_server.TOOL_SPECS}
    aliases = {"list-sessions": "list"}
    tools = {aliases.get(tool, tool) for tool in tools}
    assert commands - tools == set()


def test_http_token_rejects_missing_and_wrong_bearer(monkeypatch):
    import importlib

    from starlette.testclient import TestClient

    monkeypatch.setenv("PATCHRIGHT_CLI_MCP_TOKEN", "s3cret\r\n")
    module = importlib.reload(mcp_server)
    try:
        app = module.server.http_app()
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "0"},
            },
        }
        headers = {"Accept": "application/json, text/event-stream"}
        with TestClient(app) as client:
            assert client.post("/mcp", json=body, headers=headers).status_code == 401
            wrong = {**headers, "Authorization": "Bearer nope"}
            assert client.post("/mcp", json=body, headers=wrong).status_code == 401
            right = {**headers, "Authorization": "Bearer s3cret"}
            assert client.post("/mcp", json=body, headers=right).status_code == 200
    finally:
        monkeypatch.delenv("PATCHRIGHT_CLI_MCP_TOKEN")
        importlib.reload(mcp_server)


def _string_params(tool):
    return [name for name, kind, *_ in mcp_server.TOOL_SPECS[tool]["params"] if kind == "string" and name != "session"]


@pytest.mark.parametrize("value", ["--file=/etc/passwd", "-", "plain"])
def test_client_values_never_become_cli_options(monkeypatch, value):
    """Every client string lands after `--`, and only the command name sits before it unmarked."""
    monkeypatch.setattr(mcp_server, "UNRESTRICTED_FILESYSTEM", True)
    checked = 0
    for tool in mcp_server.TOOL_SPECS:
        for name in _string_params(tool):
            try:
                args = mcp_server._build_args(tool, _defaults(tool, **{name: value}))
            except ValueError:
                continue
            command = mcp_server._split_values(args)
            head = command[: command.index("--")] if "--" in command else command
            assert value not in head, (tool, name, command)
            assert len([a for a in head if not a.startswith("-")]) == 1, (tool, name, command)
            checked += 1
    assert checked > 40


def test_cli_never_loads_a_client_writable_config(tmp_path, monkeypatch):
    """The CLI runs from <root>/output, where clients can write .patchright-cli/config.json."""
    import asyncio
    import json
    import sys
    from pathlib import Path

    echo = "import json, sys; print(json.dumps(sys.argv[1:]))"
    monkeypatch.setattr(mcp_server, "CLI", (sys.executable, "-c", echo))
    monkeypatch.setattr(mcp_server, "MCP_ROOT", tmp_path)
    monkeypatch.setattr(mcp_server, "OUTPUT_ROOT", tmp_path / "output")
    argv = json.loads(asyncio.run(mcp_server._run_patchright(["tab-list"])))
    config = Path(next(a for a in argv if a.startswith("--config=")).split("=", 1)[1])
    assert json.loads(config.read_text(encoding="utf-8")) == {}
    assert tmp_path not in config.parents
