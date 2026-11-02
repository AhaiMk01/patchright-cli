"""Unit tests for the passkey-* commands (virtual WebAuthn authenticator via BrowserContext.credentials)."""

import asyncio
import json
import os
import stat
from unittest.mock import AsyncMock

import pytest

from patchright_cli import mcp_server
from patchright_cli.daemon import handle_command
from tests.test_cli_axi import _run as _run_cli
from tests.test_daemon_axi import mock_session, mock_state  # noqa: F401  (fixtures)

CRED = {
    "id": "rOcULsEhnOTidWZYElf2oA",
    "rpId": "localhost",
    "userHandle": "YWxpY2UtaWQ",
    "privateKey": "PRIV",
    "publicKey": "PUB",
    "signCount": 1,
}


@pytest.fixture
def state(request):
    return request.getfixturevalue("mock_state")


@pytest.fixture
def session(request, state):
    s = request.getfixturevalue("mock_session")
    s._passkeys_installed = False
    s._passkey_lock = asyncio.Lock()
    creds = s.context.credentials
    creds.install = AsyncMock()
    creds.get = AsyncMock(return_value=[])
    creds.create = AsyncMock(return_value=dict(CRED))
    creds.delete = AsyncMock()
    state.sessions = {"default": s}
    return s


async def _run(state, command, args=None, cwd=None, **options):
    return await handle_command(state, {"command": command, "args": args or [], "options": options, "cwd": cwd})


# -- install -----------------------------------------------------------------


async def test_first_passkey_command_installs_once(state, session):
    first = await _run(state, "passkey-list")
    assert first["output"].startswith("Virtual passkey authenticator installed in this session.\n")
    second = await _run(state, "passkey-list")
    assert "installed" not in second["output"]
    session.context.credentials.install.assert_awaited_once()
    assert session._passkeys_installed is True


@pytest.mark.parametrize(
    "command,args", [("passkey-add", ["x.com"]), ("passkey-delete", ["id1"]), ("passkey-clear", [])]
)
async def test_every_passkey_command_installs(state, session, command, args):
    await _run(state, command, args)
    session.context.credentials.install.assert_awaited_once()


# -- list --------------------------------------------------------------------


async def test_list_empty_says_so_with_filters(state, session):
    session._passkeys_installed = True
    resp = await _run(state, "passkey-list", **{"rp-id": "x.com"})
    assert resp == {"success": True, "output": "0 passkeys in this session matching --rp-id=x.com", "data": []}
    session.context.credentials.get.assert_awaited_once_with(id=None, rp_id="x.com")


async def test_list_hides_keys(state, session):
    session._passkeys_installed = True
    session.context.credentials.get.return_value = [dict(CRED)]
    resp = await _run(state, "passkey-list", id=CRED["id"])
    want = {"id": CRED["id"], "rpId": "localhost", "userHandle": CRED["userHandle"], "signCount": 1}
    assert resp["data"] == [want]
    assert json.loads(resp["output"]) == [want]
    assert "PRIV" not in resp["output"] and "PUB" not in resp["output"]
    session.context.credentials.get.assert_awaited_once_with(id=CRED["id"], rp_id=None)


# -- add ---------------------------------------------------------------------


async def test_add_generates_by_default(state, session):
    session._passkeys_installed = True
    resp = await _run(state, "passkey-add", ["localhost"])
    assert resp["success"] is True
    assert resp["output"] == f"Passkey '{CRED['id']}' added for localhost."
    assert "privateKey" not in resp["data"]
    session.context.credentials.create.assert_awaited_once_with("localhost")


async def test_add_passes_flags(state, session):
    opts = {"id": "AAAA", "user-handle": "dXNlcg", "private-key": "K1", "public-key": "K2", "sign-count": "7"}
    await _run(state, "passkey-add", ["x.com"], **opts)
    session.context.credentials.create.assert_awaited_once_with(
        "x.com", id="AAAA", user_handle="dXNlcg", private_key="K1", public_key="K2", sign_count=7
    )


@pytest.mark.parametrize("flag", ["private-key", "public-key"])
async def test_add_rejects_half_a_keypair(state, session, flag):
    resp = await _run(state, "passkey-add", ["x.com"], **{flag: "K"})
    assert resp["success"] is False
    assert "--private-key and --public-key together" in resp["output"]
    session.context.credentials.create.assert_not_awaited()
    session.context.credentials.install.assert_not_awaited()


async def test_add_rejects_non_integer_sign_count(state, session):
    resp = await _run(state, "passkey-add", ["x.com"], **{"sign-count": "lots"})
    assert resp == {"success": False, "output": "--sign-count must be an integer, got 'lots'"}
    session.context.credentials.create.assert_not_awaited()


# -- delete / clear ------------------------------------------------------------


async def test_delete_absent_is_a_noop(state, session):
    session._passkeys_installed = True
    resp = await _run(state, "passkey-delete", ["nope"])
    assert resp == {"success": True, "output": "No passkey with id 'nope' (no-op)"}
    session.context.credentials.delete.assert_not_awaited()


async def test_delete_present(state, session):
    session._passkeys_installed = True
    session.context.credentials.get.return_value = [dict(CRED)]
    resp = await _run(state, "passkey-delete", [CRED["id"]])
    assert resp == {"success": True, "output": f"Passkey '{CRED['id']}' deleted."}
    session.context.credentials.delete.assert_awaited_once_with(CRED["id"])


async def test_clear_deletes_each_matching(state, session):
    session._passkeys_installed = True
    session.context.credentials.get.return_value = [dict(CRED), dict(CRED, id="BBBB")]
    resp = await _run(state, "passkey-clear", **{"rp-id": "localhost"})
    assert resp == {"success": True, "output": "Deleted 2 passkey(s) for localhost."}
    session.context.credentials.get.assert_awaited_once_with(rp_id="localhost")
    assert [c.args[0] for c in session.context.credentials.delete.await_args_list] == [CRED["id"], "BBBB"]


# -- export / import -----------------------------------------------------------


async def test_export_writes_keys_relative_to_cwd(state, session, tmp_path):
    session._passkeys_installed = True
    session.context.credentials.get.return_value = [dict(CRED)]
    resp = await _run(state, "passkey-export", ["out/pk.json"], cwd=str(tmp_path))
    target = tmp_path / "out" / "pk.json"
    assert resp == {
        "success": True,
        "output": f"Exported 1 passkey(s) to {target} (contains private keys; keep it out of version control)",
    }
    assert json.loads(target.read_text(encoding="utf-8")) == [CRED]


async def test_export_replaces_an_existing_file(state, session, tmp_path):
    session._passkeys_installed = True
    session.context.credentials.get.return_value = [dict(CRED)]
    target = tmp_path / "pk.json"
    target.write_text("old", encoding="utf-8")
    await _run(state, "passkey-export", [str(target)])
    assert json.loads(target.read_text(encoding="utf-8")) == [CRED]
    assert [p.name for p in tmp_path.iterdir()] == ["pk.json"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
async def test_export_file_is_owner_only(state, session, tmp_path):
    session._passkeys_installed = True
    target = tmp_path / "pk.json"
    target.write_text("old", encoding="utf-8")
    target.chmod(0o644)
    await _run(state, "passkey-export", [str(target)])
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


async def test_export_default_path(state, session, tmp_path):
    session._passkeys_installed = True
    resp = await _run(state, "passkey-export", cwd=str(tmp_path))
    files = list((tmp_path / ".patchright-cli").glob("passkeys-*.json"))
    assert len(files) == 1 and str(files[0]) in resp["output"]
    assert json.loads(files[0].read_text(encoding="utf-8")) == []


async def test_import_reseeds_every_credential(state, session, tmp_path):
    (tmp_path / "pk.json").write_text(json.dumps([CRED]), encoding="utf-8")
    resp = await _run(state, "passkey-import", ["pk.json"], cwd=str(tmp_path))
    assert resp["success"] is True
    assert resp["output"].endswith(f"Imported 1 passkey(s) from {tmp_path / 'pk.json'}")
    session.context.credentials.create.assert_awaited_once_with(
        "localhost", id=CRED["id"], user_handle=CRED["userHandle"], private_key="PRIV", public_key="PUB", sign_count=1
    )


async def test_import_missing_file(state, session, tmp_path):
    resp = await _run(state, "passkey-import", ["nope.json"], cwd=str(tmp_path))
    assert resp == {"success": False, "output": f"File not found: {tmp_path / 'nope.json'}"}


@pytest.mark.parametrize("content", ["not json", json.dumps({"cookies": []}), json.dumps([{"id": "x"}])])
async def test_import_rejects_bad_files(state, session, tmp_path, content):
    (tmp_path / "pk.json").write_text(content, encoding="utf-8")
    resp = await _run(state, "passkey-import", ["pk.json"], cwd=str(tmp_path))
    assert resp["success"] is False
    assert resp["output"].startswith("Invalid passkey file")
    session.context.credentials.create.assert_not_awaited()


# -- CLI argument layer ----------------------------------------------------------


def test_cli_passkey_flags_reach_the_daemon(monkeypatch, capsys):
    code, _, _, sent = _run_cli(
        ["passkey-add", "x.com", "--id=AAAA", "--user-handle=dXNlcg", "--sign-count=3"], monkeypatch, capsys
    )
    assert code == 0
    assert sent["command"] == "passkey-add"
    assert sent["args"] == ["x.com"]
    assert sent["options"]["id"] == "AAAA"
    assert sent["options"]["user-handle"] == "dXNlcg"
    assert sent["options"]["sign-count"] == "3"


@pytest.mark.parametrize(
    "argv",
    [["passkey-list", "--rp=x.com"], ["passkey-delete", "id1", "--rp-id=x"], ["passkey-import", "f.json", "--force"]],
)
def test_cli_passkey_unknown_flags_exit_2(monkeypatch, capsys, argv):
    code, out, _, sent = _run_cli(argv, monkeypatch, capsys)
    assert code == 2
    assert out.startswith(f"error: unknown flag {argv[-1].split('=')[0]} for `{argv[0]}`")
    assert sent == {}


@pytest.mark.parametrize("argv", [["passkey-add"], ["passkey-delete"], ["passkey-import"]])
def test_cli_passkey_missing_argument_exits_2(monkeypatch, capsys, argv):
    code, _, _, sent = _run_cli(argv, monkeypatch, capsys)
    assert code == 2
    assert sent == {}


# -- MCP tools -------------------------------------------------------------------


def test_mcp_passkey_args():
    build = mcp_server._build_args
    assert build("patchright_passkey_list", {"rp_id": "x.com", "id": "", "session": "default"}) == [
        "passkey-list",
        "--rp-id=x.com",
    ]
    args = build(
        "patchright_passkey_add",
        {
            "rp_id": "x.com",
            "id": "AAAA",
            "user_handle": "",
            "private_key": "",
            "public_key": "",
            "sign_count": 2,
            "session": "default",
        },
    )
    assert args == ["passkey-add", "x.com", "--id=AAAA", "--sign-count=2"]
    assert build("patchright_passkey_delete", {"id": "AAAA", "session": "default"}) == ["passkey-delete", "AAAA"]
    assert build("patchright_passkey_clear", {"rp_id": "", "session": "default"}) == ["passkey-clear"]
