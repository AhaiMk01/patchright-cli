"""AXI audit, daemon side: the checks MCP and raw clients meet without the CLI in front."""

import pytest

from tests.test_daemon_axi import _run, mock_session, mock_state  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [["1+1", "x"], ["1", "+", "1"]])
async def test_eval_rejects_a_trailing_non_ref(mock_state, mock_session, args):  # noqa: F811
    mock_state.sessions = {"default": mock_session}
    reply = await _run(mock_state, "eval", args)
    assert reply["success"] is False and reply["exit_code"] == 2
    assert "is not a ref" in reply["output"]
    mock_session.page.evaluate.assert_not_called()
