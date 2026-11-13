import json
import os
import socket
import struct
import subprocess
import sys
import threading

DAEMON_ONLY = ["patchright_cli.daemon", "patchright_cli.background", "patchright", "aiohttp", "asyncio"]
PER_COMMAND = ["argparse", "subprocess", "patchright_cli.daemon_pid", "patchright_cli.mcp_server"]
REPORT_LOADED = "import json, sys; print(json.dumps([m for m in {names!r} if m in sys.modules]), file=sys.stderr)"


def _loaded_after(statement: str, names: list[str], env: dict | None = None) -> tuple[list[str], str]:
    """Run `statement` in a new interpreter; return which of `names` it loaded, and its stdout."""
    code = f"{statement}\n{REPORT_LOADED.format(names=names)}"
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, env={**os.environ, **(env or {})}
    )
    return json.loads(done.stderr.splitlines()[-1]), done.stdout


def _serve_one_reply(server: socket.socket, reply: dict) -> None:
    """Answer the first framed request; a connection that sends nothing is the client's port probe."""
    while True:
        conn, _ = server.accept()
        with conn:
            header = conn.recv(4)
            if len(header) < 4:
                continue
            conn.recv(struct.unpack("!I", header)[0])
            data = json.dumps(reply).encode("utf-8")
            conn.sendall(struct.pack("!I", len(data)) + data)
            return


def test_cli_import_loads_no_daemon_modules():
    assert _loaded_after("import patchright_cli.cli", DAEMON_ONLY + PER_COMMAND + ["click"])[0] == []


def test_protocol_is_a_leaf_module():
    loaded, _ = _loaded_after("import patchright_cli.protocol", DAEMON_ONLY + PER_COMMAND + ["patchright_cli.cli"])
    assert loaded == []


def test_a_command_sent_to_a_running_daemon_loads_no_daemon_modules():
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]
        reply = {"success": True, "output": "reply from the fake daemon"}
        threading.Thread(target=_serve_one_reply, args=(server, reply), daemon=True).start()
        run_list = (
            "import sys\n"
            f"sys.argv = ['patchright-cli', '--port={port}', 'list']\n"
            "from patchright_cli._entry import main\n"
            "try:\n    main()\nexcept SystemExit as stop:\n    assert not stop.code, stop.code"
        )
        quiet = {"PATCHRIGHT_CLI_NO_SKILL_CHECK": "1", "PATCHRIGHT_CLI_NO_VERSION_CHECK": "1"}
        loaded, stdout = _loaded_after(run_list, DAEMON_ONLY + PER_COMMAND + ["click"], env=quiet)
    assert "reply from the fake daemon" in stdout
    assert loaded == []
