"""Browserless tests for the optional MCP wrapper."""

import sys

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


def _http_env(monkeypatch, *, token=None, protection=None):
    """Point run_mcp_server at a token and a fastmcp Settings built from a fresh environment."""
    import fastmcp

    monkeypatch.setattr(mcp_server, "TOKEN", token)
    monkeypatch.delenv("FASTMCP_HTTP_HOST_ORIGIN_PROTECTION", raising=False)
    if protection is not None:
        monkeypatch.setenv("FASTMCP_HTTP_HOST_ORIGIN_PROTECTION", protection)
    monkeypatch.setattr(fastmcp, "settings", type(fastmcp.settings)(_env_file=None))
    calls = []
    monkeypatch.setattr(mcp_server.server, "run", lambda **kwargs: calls.append(kwargs))
    return calls


@pytest.mark.parametrize("host", ["127.0.0.1", "0.0.0.0"])
def test_run_mcp_server_http_refuses_without_token_or_protection_choice(monkeypatch, capsys, host):
    calls = _http_env(monkeypatch)

    with pytest.raises(SystemExit) as exc:
        mcp_server.run_mcp_server(http=True, host=host, port=9090)

    assert exc.value.code != 0
    assert calls == []
    err = capsys.readouterr().err
    assert "PATCHRIGHT_CLI_MCP_TOKEN" in err
    assert "FASTMCP_HTTP_HOST_ORIGIN_PROTECTION=true" in err
    assert "FASTMCP_HTTP_ALLOWED_HOSTS" in err
    assert "FASTMCP_HTTP_HOST_ORIGIN_PROTECTION=false" in err


def test_run_mcp_server_http_with_token(monkeypatch, capsys):
    calls = _http_env(monkeypatch, token="s3cret")

    mcp_server.run_mcp_server(http=True, host="0.0.0.0", port=9090)

    assert calls == [{"transport": "http", "host": "0.0.0.0", "port": 9090}]
    assert capsys.readouterr().err == ""


_PROTECTION_FIELD = type(mcp_server.fastmcp.settings).model_fields["http_host_origin_protection"]


@pytest.mark.parametrize(
    "protection",
    [
        "true",
        "false",
        "FALSE",
        "0",
        # fastmcp 3.4.3 takes a plain bool; "auto" arrived later.
        pytest.param(
            "auto",
            marks=pytest.mark.skipif(_PROTECTION_FIELD.annotation is bool, reason="this fastmcp has no 'auto'"),
        ),
    ],
)
def test_run_mcp_server_http_with_explicit_protection_choice(monkeypatch, capsys, protection):
    calls = _http_env(monkeypatch, protection=protection)

    mcp_server.run_mcp_server(http=True, host="0.0.0.0", port=9090)

    assert calls == [{"transport": "http", "host": "0.0.0.0", "port": 9090}]
    assert "unauthenticated" in capsys.readouterr().err


def test_run_mcp_server_stdio_ignores_http_protection(monkeypatch):
    calls = _http_env(monkeypatch)

    mcp_server.run_mcp_server()

    assert calls == [{"transport": "stdio"}]


def test_empty_host_origin_protection_counts_as_unset(tmp_path):
    """An empty value would break fastmcp's import; it must instead reach the refusal."""
    import os
    import subprocess

    env = {**os.environ, "FASTMCP_HTTP_HOST_ORIGIN_PROTECTION": "", "PATCHRIGHT_CLI_MCP_TOKEN": " "}
    result = subprocess.run(
        [sys.executable, "-m", "patchright_cli", "mcp", "--http", "--port=1"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )

    assert result.returncode != 0
    assert "refusing to start `mcp --http`" in result.stderr
    assert "ValidationError" not in result.stderr


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


# Optional parameters some tools still need one of.
_TOOL_EXTRAS = {"patchright_drop": {"data": "x"}}


def _tool_argv(monkeypatch, tmp_path, tool, **overrides):
    """The CLI argv `tool` runs, with required parameters filled in."""
    import asyncio

    monkeypatch.setattr(mcp_server, "MCP_ROOT", tmp_path)
    monkeypatch.setattr(mcp_server, "OUTPUT_ROOT", tmp_path / "output")
    (tmp_path / "input.json").write_text("{}", encoding="utf-8")
    params = _defaults(tool)
    for name, kind, required, _, _ in mcp_server.TOOL_SPECS[tool]["params"]:
        if required and kind == "string":
            params[name] = {
                "url": "https://example.test/",
                "cdp_url": "http://127.0.0.1:9222",
                "path": str(tmp_path / "input.json"),
            }.get(name, "e1")
        elif required and kind == "number":
            params[name] = 1
    params.update(_TOOL_EXTRAS.get(tool, {}), **overrides)
    calls = []

    class Process:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def spawn(*command, **kwargs):
        calls.append(list(command))
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    if tool == "patchright_snapshot":
        asyncio.run(mcp_server._snapshot_tool(params))
    else:
        asyncio.run(mcp_server._run_registered_tool(tool, params))
    (command,) = calls
    return command[len(mcp_server.CLI) :]


def _flags(argv):
    return argv[: argv.index("--")] if "--" in argv else argv


def test_every_tool_sends_web_only_before_the_values(monkeypatch, tmp_path):
    """A client can drive a session the plain CLI opened, so the policy rides on every call."""
    for tool in mcp_server.TOOL_SPECS:
        if tool == "patchright_install_skills":  # refused unless the filesystem is unrestricted
            continue
        argv = _tool_argv(monkeypatch, tmp_path, tool)
        flags = _flags(argv)
        assert flags.count("--web-only") == 1, (tool, argv)
        # After the command name: a leading unknown flag would be read as the command.
        command = next(a for a in flags if not a.startswith("-"))
        assert flags.index("--web-only") > flags.index(command), (tool, argv)


def test_unrestricted_filesystem_drops_web_only(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "UNRESTRICTED_FILESYSTEM", True)
    for tool in mcp_server.TOOL_SPECS:
        assert "--web-only" not in _tool_argv(monkeypatch, tmp_path, tool), tool


def test_a_client_value_cannot_stand_in_for_web_only(monkeypatch, tmp_path):
    argv = _tool_argv(monkeypatch, tmp_path, "patchright_type", text="--web-only")
    assert _flags(argv).count("--web-only") == 1
    assert argv[argv.index("--") :] == ["--", "--web-only"]


@pytest.mark.parametrize(
    ("tool", "command", "args"),
    [
        ("patchright_open", "open", ["https://example.test/"]),
        ("patchright_click", "click", ["e1", "left"]),
        ("patchright_go_back", "go-back", []),
        ("patchright_title", "title", []),
    ],
)
def test_web_only_reaches_the_daemon_as_an_option(monkeypatch, tmp_path, tool, command, args):
    argv = _tool_argv(monkeypatch, tmp_path, tool, url="https://example.test/")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["patchright-cli", *argv])
    sent = {}

    def send(command, args, options, port):
        sent.update(command=command, args=args, options=options)
        return {"success": True, "output": ""}

    monkeypatch.setattr(cli, "ensure_daemon_running", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_send_command", send)
    try:
        cli.main()
    except SystemExit as exc:
        assert not exc.code
    assert sent["command"] == command
    assert sent["options"]["web-only"] is True
    assert sent["args"] == args


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
