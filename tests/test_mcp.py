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
