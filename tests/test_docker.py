"""Tests against the built Docker image.

Skipped unless PATCHRIGHT_CLI_DOCKER_IMAGE names an image, e.g.:

    docker build -t patchright-cli:test .
    PATCHRIGHT_CLI_DOCKER_IMAGE=patchright-cli:test pytest tests/test_docker.py -v
"""

import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid

import pytest

from patchright_cli import __version__

IMAGE = os.environ.get("PATCHRIGHT_CLI_DOCKER_IMAGE")
TOKEN = "docker-test-token"
TITLE = "Docker Smoke"
PAGE_URL = f"data:text/html,<title>{TITLE.replace(' ', '%20')}</title><h1>hi</h1>"

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not IMAGE, reason="PATCHRIGHT_CLI_DOCKER_IMAGE not set"),
]


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120, check=check)


def _post_initialize(url: str, token: str | None) -> int:
    body = (
        b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18",'
        b'"capabilities":{},"clientInfo":{"name":"t","version":"0"}}}'
    )
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


@pytest.fixture(scope="module")
def container():
    name = f"patchright-cli-test-{uuid.uuid4().hex[:8]}"
    _docker(
        "run",
        "-d",
        "--name",
        name,
        "--shm-size=1g",
        "-e",
        f"PATCHRIGHT_CLI_MCP_TOKEN={TOKEN}",
        "-p",
        "127.0.0.1::8000",
        IMAGE,
    )
    try:
        port = _docker("port", name, "8000/tcp").stdout.strip().splitlines()[0].rsplit(":", 1)[1]
        url = f"http://127.0.0.1:{port}/mcp"
        deadline = time.monotonic() + 60
        while _post_initialize_safe(url) is None:
            if time.monotonic() > deadline:
                logs = _docker("logs", name, check=False)
                pytest.fail(f"MCP server never came up:\n{logs.stdout}\n{logs.stderr}")
            time.sleep(0.5)
        yield {"name": name, "url": url}
    finally:
        _docker("rm", "-f", name, check=False)


def _post_initialize_safe(url: str) -> int | None:
    try:
        return _post_initialize(url, TOKEN)
    except (urllib.error.URLError, ConnectionError, OSError):
        return None


def test_version_matches_package():
    result = _docker("run", "--rm", IMAGE, "--version")
    assert __version__ in result.stdout


def test_runs_as_non_root(container):
    assert _docker("exec", container["name"], "id", "-u").stdout.strip() != "0"


def test_google_chrome_installed(container):
    assert "Google Chrome" in _docker("exec", container["name"], "google-chrome", "--version").stdout


def test_mcp_rejects_missing_and_wrong_token(container):
    assert _post_initialize(container["url"], None) == 401
    assert _post_initialize(container["url"], "wrong") == 401
    assert _post_initialize(container["url"], TOKEN) == 200


async def test_mcp_drives_headed_chrome(container):
    from fastmcp import Client

    async with Client(container["url"], auth=TOKEN) as client:
        names = {tool.name for tool in await client.list_tools()}
        assert {"patchright_open", "patchright_eval", "patchright_screenshot"} <= names

        try:
            await client.call_tool("patchright_open", {"url": PAGE_URL})

            title = await client.call_tool("patchright_eval", {"code": "document.title"})
            assert TITLE in title.content[0].text

            # Headed under Xvfb: no HeadlessChrome UA, no webdriver flag.
            ua = await client.call_tool("patchright_eval", {"code": "navigator.userAgent"})
            assert "HeadlessChrome" not in ua.content[0].text
            webdriver = await client.call_tool("patchright_eval", {"code": "String(navigator.webdriver)"})
            assert "true" not in webdriver.content[0].text

            shot = await client.call_tool("patchright_screenshot", {})
            assert "/data/output/" in shot.content[0].text
        finally:
            await client.call_tool("patchright_close", {})


def test_cli_via_docker_exec_shares_display(container):
    name = container["name"]
    session = "-s=exec-test"
    _docker("exec", name, "patchright-cli", session, "open", PAGE_URL)
    try:
        title = _docker("exec", name, "patchright-cli", session, "eval", "document.title")
        assert TITLE in title.stdout
    finally:
        _docker("exec", name, "patchright-cli", session, "close", check=False)
