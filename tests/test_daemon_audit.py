"""AXI audit, daemon side: the checks MCP and raw clients meet without the CLI in front."""

import pytest

from patchright_cli import values
from patchright_cli.daemon import handle_command
from tests.test_daemon_axi import _run, mock_session, mock_state  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [["1+1", "x"], ["1", "+", "1"]])
async def test_eval_rejects_a_trailing_non_ref(mock_state, mock_session, args):  # noqa: F811
    mock_state.sessions = {"default": mock_session}
    reply = await _run(mock_state, "eval", args)
    assert reply["success"] is False and reply["exit_code"] == 2
    assert "is not a ref" in reply["output"]
    mock_session.page.evaluate.assert_not_called()


_TYPED = [(c, f, k) for c, kinds in values.FLAG_KINDS.items() for f, k in kinds.items()]


@pytest.mark.asyncio
@pytest.mark.parametrize(("command", "flag", "kind"), _TYPED, ids=lambda v: str(v)[:20])
async def test_daemon_rejects_every_bad_flag_value_like_the_cli(mock_state, command, flag, kind):  # noqa: F811
    bad = True if isinstance(kind, values.Text) else "zz-bogus"
    args = ["e1", "e2"][: 1 if command != "drag" else 2]
    reply = await handle_command(mock_state, {"command": command, "args": args, "options": {flag: bad}})
    assert reply["success"] is False and reply["exit_code"] == 2
    first, help_line = reply["output"].splitlines()[:2]
    assert first in (f"Error: invalid --{flag} value `zz-bogus`", f"Error: --{flag} needs a value")
    assert help_line.startswith(f"help: --{flag} takes ")


@pytest.mark.asyncio
async def test_daemon_rejects_a_bad_positional_value(mock_state):  # noqa: F811
    reply = await _run(mock_state, "resize", ["wide", "800"])
    assert reply["exit_code"] == 2
    assert reply["output"].startswith("Error: invalid <w> value `wide` for `resize`")
