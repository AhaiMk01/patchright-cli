import subprocess
import sys

DAEMON_ONLY = ["patchright_cli.daemon", "patchright_cli.background", "patchright", "aiohttp", "asyncio"]
PER_COMMAND = ["argparse", "subprocess", "patchright_cli.daemon_pid", "patchright_cli.mcp_server"]


def _loaded_after(statement: str, names: list[str]) -> list[str]:
    code = f"import sys; {statement}; print([m for m in {names!r} if m in sys.modules])"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    return eval(out)


def test_cli_import_loads_no_daemon_modules():
    assert _loaded_after("import patchright_cli.cli", DAEMON_ONLY + PER_COMMAND + ["click"]) == []


def test_protocol_is_a_leaf_module():
    loaded = _loaded_after("import patchright_cli.protocol", DAEMON_ONLY + PER_COMMAND + ["patchright_cli.cli"])
    assert loaded == []
