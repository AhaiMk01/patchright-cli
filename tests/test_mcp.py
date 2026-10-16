"""Browserless tests for the optional MCP wrapper."""

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
    assert args == ["--profile=work", "open"]


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
