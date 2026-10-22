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
SCREENSHOT = "/patchright/output/docker-test.png"

pytestmark = pytest.mark.skipif(not IMAGE, reason="PATCHRIGHT_CLI_DOCKER_IMAGE not set")


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120, check=check)


def _mcp_ready(url: str) -> bool:
    body = (
        b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18",'
        b'"capabilities":{},"clientInfo":{"name":"t","version":"0"}}}'
    )
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {TOKEN}",
    }
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status == 200
    except (urllib.error.URLError, ConnectionError, OSError):
        return False


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
        "PATCHRIGHT_CLI_MODE=mcp-http",
        "-e",
        f"PATCHRIGHT_CLI_MCP_TOKEN={TOKEN}",
        "-p",
        "127.0.0.1::9090",
        IMAGE,
    )
    try:
        port = _docker("port", name, "9090/tcp").stdout.strip().splitlines()[0].rsplit(":", 1)[1]
        url = f"http://127.0.0.1:{port}/mcp"
        deadline = time.monotonic() + 60
        while not _mcp_ready(url):
            if time.monotonic() > deadline:
                logs = _docker("logs", name, check=False)
                pytest.fail(f"MCP server never came up:\n{logs.stdout}\n{logs.stderr}")
            time.sleep(0.5)
        yield {"name": name, "url": url}
    finally:
        _docker("rm", "-f", name, check=False)


def test_version_matches_package():
    result = _docker("run", "--rm", IMAGE, "--version")
    assert __version__ in result.stdout


def test_google_chrome_installed(container):
    assert "Google Chrome" in _docker("exec", container["name"], "google-chrome", "--version").stdout


async def test_mcp_tools_drive_chrome(container):
    from fastmcp import Client

    async with Client(container["url"], auth=TOKEN) as client:
        names = {tool.name for tool in await client.list_tools()}
        assert {"patchright_open", "patchright_eval", "patchright_screenshot"} <= names

        try:
            await client.call_tool("patchright_open", {"url": PAGE_URL})

            title = await client.call_tool("patchright_eval", {"code": "document.title"})
            assert TITLE in title.content[0].text

            webdriver = await client.call_tool("patchright_eval", {"code": "String(navigator.webdriver)"})
            assert "true" not in webdriver.content[0].text

            await client.call_tool("patchright_screenshot", {"filename": SCREENSHOT})
        finally:
            await client.call_tool("patchright_close", {})

    assert _docker("exec", container["name"], "test", "-s", SCREENSHOT, check=False).returncode == 0
