"""AXI audit, daemon side: the checks MCP and raw clients meet without the CLI in front."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from patchright_cli import daemon, values
from patchright_cli.daemon import _error_reply, handle_command
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


# -- E. Playwright internals stay out of the reply ------------------------------

DROP_REFUSED = (
    "Locator.drop: Drop target did not accept the drop: its dragover handler must call preventDefault()\n"
    'Call log:\n  - waiting for get_by_role("region", name="Zone")\n'
)


@pytest.mark.parametrize(
    ("cmd", "args", "options", "raw", "expected"),
    [
        (
            "drop",
            ["e4"],
            {},
            DROP_REFUSED,
            [
                "e4 did not accept the drop (its dragover handler does not call preventDefault)",
                "help: drop onto the element that handles drops",
            ],
        ),
        (
            "hover",
            ["e2"],
            {"scroll": "none"},
            "Locator.hover: Timeout 30000ms exceeded.\nCall log:\n  - element is outside of the viewport",
            [
                "hover on e2 timed out after 30s: the element is outside of the viewport",
                "help: the element is off-screen; drop --scroll=none or scroll first",
            ],
        ),
        (
            "localstorage-list",
            [],
            {},
            "WebStorage.items: SecurityError: Failed to read the 'localStorage' property from 'Window'",
            ["localStorage is not available on this page (opaque origin)", "help: goto an http(s) page first"],
        ),
        (
            "sessionstorage-get",
            ["k"],
            {},
            "WebStorage.get_item: SecurityError: Failed to read the 'sessionStorage' property from 'Window'",
            ["sessionStorage is not available on this page (opaque origin)", "help: goto an http(s) page first"],
        ),
        (
            "click",
            ["e7"],
            {},
            "Locator.click: Timeout 5000ms exceeded.\nCall log:\n  - waiting for get_by_ref('e7')",
            [
                "click on e7 timed out after 5s",
                "help: run `snapshot` to check e7 is visible and enabled, or raise --timeout-action=<ms>",
            ],
        ),
        (
            "goto",
            ["https://example.com"],
            {},
            'Page.goto: Timeout 30000ms exceeded.\nCall log:\n  - navigating to "https://example.com/"',
            [
                "goto timed out after 30s",
                "help: the page did not finish loading; check the URL, or raise --timeout-navigation=<ms>",
            ],
        ),
        (
            "eval",
            ["foo()"],
            {},
            "Page.evaluate: ReferenceError: foo is not defined\n    at eval (eval at evaluate (:291:30))",
            ["ReferenceError: foo is not defined"],
        ),
        (
            "click",
            ["f6e3"],
            {},
            "Locator.click: Timeout 2000ms exceeded.\nCall log:\n  - attempting click action\n"
            '      - <dialog open="" id="dlg">…</dialog> intercepts pointer events\n    - retrying click action',
            [
                'click on f6e3 timed out after 2s: <dialog open="" id="dlg">…</dialog> covers it',
                "help: close what covers it first (a dialog or overlay: press Escape, or click its close button)",
            ],
        ),
        (
            "select",
            ["e3", "Purple"],
            {},
            "Locator.select_option: Timeout 2000ms exceeded.\nCall log:\n      - did not find some options\n",
            ["select on e3 timed out after 2s: no option with that label"],
        ),
        (
            "check",
            ["e2"],
            {},
            "Locator.check: Timeout 1000ms exceeded.\nCall log:\n      - element is not enabled\n",
            [
                "check on e2 timed out after 1s: element is not enabled",
                "help: run `snapshot` to check e2, or raise --timeout-action=<ms>",
            ],
        ),
        ("click", ["e1"], {}, "plain failure", ["plain failure"]),
    ],
)
def test_error_reply_translates_playwright_errors(cmd, args, options, raw, expected):
    reply = _error_reply(cmd, args, options, Exception(raw))
    assert reply["success"] is False
    lines = reply["output"].splitlines()
    assert lines == [f"Error: {expected[0]}", *expected[1:]]
    assert "Call log" not in reply["output"]


# -- A2. highlight --hide takes Playwright's overlay element out of the page ----


def _highlight_page(session):
    frame = MagicMock()
    frame.evaluate = AsyncMock()
    session.page.frames = [frame]
    loc = MagicMock()
    loc.count = AsyncMock(return_value=1)
    loc.highlight = AsyncMock()
    loc.hide_highlight = AsyncMock()
    session.page.get_by_ref = MagicMock(return_value=loc)
    session.page.hide_highlight = AsyncMock()
    session.last_raw = '- button "A" [ref=e1]\n- button "B" [ref=e2]'
    return frame


@pytest.mark.asyncio
async def test_hide_all_removes_the_glass_in_the_isolated_world(mock_state, mock_session):  # noqa: F811
    mock_state.sessions = {"default": mock_session}
    frame = _highlight_page(mock_session)
    await _run(mock_state, "highlight", ["e1"])
    frame.evaluate.assert_not_awaited()
    await _run(mock_state, "highlight", [], hide=True)
    frame.evaluate.assert_awaited_once_with(daemon._REMOVE_GLASS, isolated_context=True)


@pytest.mark.asyncio
async def test_hide_one_keeps_the_glass_while_another_highlight_shows(mock_state, mock_session):  # noqa: F811
    mock_state.sessions = {"default": mock_session}
    frame = _highlight_page(mock_session)
    await _run(mock_state, "highlight", ["e1"])
    await _run(mock_state, "highlight", ["e2"])
    await _run(mock_state, "highlight", ["e1"], hide=True)
    frame.evaluate.assert_not_awaited()
    await _run(mock_state, "highlight", ["e2"], hide=True)
    frame.evaluate.assert_awaited_once_with(daemon._REMOVE_GLASS, isolated_context=True)


@pytest.mark.asyncio
async def test_daemon_rejects_an_unknown_console_level_before_capturing(mock_state, mock_session):  # noqa: F811
    mock_state.sessions = {"default": mock_session}
    mock_session.start_console_capture = AsyncMock(return_value=True)
    reply = await _run(mock_state, "console", ["bogus"])
    assert reply["success"] is False and reply["exit_code"] == 2
    assert reply["output"].startswith("Error: invalid [level] value `bogus` for `console`")
    mock_session.start_console_capture.assert_not_called()
