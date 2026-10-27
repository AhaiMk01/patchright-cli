"""Unit tests for the media emulation commands (set-color-scheme and friends)."""

from typing import get_args
from unittest.mock import AsyncMock, MagicMock

import pytest

from patchright_cli import cli, mcp_server
from patchright_cli.daemon import _CODEGEN_RECORDABLE, COMMAND_HANDLERS, Session

# Command suffix -> (emulate_media keyword, a valid value, allowed values, Playwright's default).
FEATURES = {
    "color-scheme": ("color_scheme", "dark", ("dark", "light", "no-preference"), "light"),
    "reduced-motion": ("reduced_motion", "reduce", ("reduce", "no-preference"), "no-preference"),
    "forced-colors": ("forced_colors", "active", ("active", "none"), "none"),
    "contrast": ("contrast", "more", ("more", "no-preference"), "no-preference"),
    "media": ("media", "print", ("screen", "print"), "null"),
}


def _fake_page():
    page = MagicMock()
    page.is_closed = MagicMock(return_value=False)
    page.emulate_media = AsyncMock()
    page.context.new_cdp_session = AsyncMock(side_effect=RuntimeError("no CDP in tests"))
    return page


def _session(pages):
    context = MagicMock()
    context.new_page = AsyncMock(side_effect=lambda: _fake_page())
    return Session("default", context, pages)


async def _run(command, session, *args):
    return await COMMAND_HANDLERS[command](session, session.page, list(args), {}, None, MagicMock())


@pytest.mark.parametrize("feature", FEATURES)
def test_help_lists_set_and_clear(feature, capsys):
    allowed = FEATURES[feature][2]
    assert "|".join(allowed) in cli.COMMANDS_HELP[f"set-{feature}"]
    assert f"clear-{feature}" in cli.COMMANDS_HELP
    cli._print_help()
    out = capsys.readouterr().out
    assert f"set-{feature} <" in out
    assert f"clear-{feature}" in out


@pytest.mark.parametrize("feature", FEATURES)
def test_commands_are_registered_and_recorded_by_codegen(feature):
    for verb in ("set", "clear"):
        assert f"{verb}-{feature}" in COMMAND_HANDLERS
        assert f"{verb}-{feature}" in _CODEGEN_RECORDABLE


@pytest.mark.asyncio
@pytest.mark.parametrize("feature", FEATURES)
@pytest.mark.parametrize("bad", ["", "bogus", "DARK"])
async def test_set_rejects_unknown_value(feature, bad):
    page = _fake_page()
    session = _session([page])
    args = [bad] if bad else []
    result = await _run(f"set-{feature}", session, *args)
    assert result["success"] is False
    for value in FEATURES[feature][2]:
        assert value in result["output"]
    page.emulate_media.assert_not_awaited()
    assert session.emulation == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("feature", FEATURES)
async def test_set_applies_to_every_page_and_clear_resets_only_that_feature(feature):
    kwarg, value, _, default = FEATURES[feature]
    pages = [_fake_page(), _fake_page()]
    closed = _fake_page()
    closed.is_closed.return_value = True
    session = _session([*pages, closed])
    session.emulation = {"other": "kept"}

    result = await _run(f"set-{feature}", session, value)
    assert result["success"] is True
    for page in pages:
        page.emulate_media.assert_awaited_once_with(**{kwarg: value})
    closed.emulate_media.assert_not_awaited()
    assert session.emulation == {"other": "kept", kwarg: value}

    result = await _run(f"clear-{feature}", session)
    assert result["success"] is True
    for page in pages:
        # Back to the context default; leaving the other keywords out keeps them.
        page.emulate_media.assert_awaited_with(**{kwarg: default})
    assert session.emulation == {"other": "kept"}


@pytest.mark.asyncio
async def test_tab_opened_later_gets_the_stored_emulation():
    session = _session([_fake_page()])
    await _run("set-color-scheme", session, "dark")
    await _run("set-media", session, "print")

    page = await session.new_page()
    page.emulate_media.assert_awaited_with(color_scheme="dark", media="print")


@pytest.mark.asyncio
async def test_popup_gets_the_stored_emulation():
    session = _session([_fake_page()])
    session.emulation = {"reduced_motion": "reduce"}
    popup = _fake_page()
    await session._on_new_page(popup)
    assert popup in session.pages
    popup.emulate_media.assert_awaited_once_with(reduced_motion="reduce")


@pytest.mark.asyncio
async def test_new_page_without_emulation_is_left_alone():
    session = _session([_fake_page()])
    page = await session.new_page()
    await session._on_new_page(page)
    page.emulate_media.assert_not_awaited()


@pytest.mark.parametrize("feature", FEATURES)
def test_mcp_tools_build_cli_argv(feature):
    tool = feature.replace("-", "_")
    _, value, allowed, _ = FEATURES[feature]
    args = mcp_server._build_args(f"patchright_set_{tool}", {"value": value, "session": "default"})
    assert args == [f"set-{feature}", value]
    assert mcp_server._build_args(f"patchright_clear_{tool}", {"session": "default"}) == [f"clear-{feature}"]
    value_type = mcp_server._parameter_type(f"patchright_set_{tool}", "value", "string")
    assert get_args(value_type) == allowed


@pytest.mark.asyncio
async def test_set_stores_the_value_before_awaiting_pages():
    # A page opened while emulate_media awaits reads session.emulation in
    # _on_new_page, so the new value must already be there.
    page = _fake_page()
    session = _session([page])
    seen = []
    page.emulate_media = AsyncMock(side_effect=lambda **kw: seen.append(dict(session.emulation)))
    await _run("set-color-scheme", session, "dark")
    assert seen == [{"color_scheme": "dark"}]

    seen.clear()
    await _run("clear-color-scheme", session)
    assert seen == [{}]


@pytest.mark.asyncio
async def test_page_closing_mid_loop_does_not_stop_the_rest():
    closing, live = _fake_page(), _fake_page()

    async def close_during_call(**kw):
        closing.is_closed.return_value = True
        raise RuntimeError("Target page, context or browser has been closed")

    closing.emulate_media = AsyncMock(side_effect=close_during_call)
    session = _session([closing, live])
    result = await _run("set-color-scheme", session, "dark")
    assert result["success"] is True
    live.emulate_media.assert_awaited_once_with(color_scheme="dark")
    assert session.emulation == {"color_scheme": "dark"}


@pytest.mark.asyncio
async def test_error_on_a_live_page_still_fails():
    page = _fake_page()
    page.emulate_media = AsyncMock(side_effect=RuntimeError("boom"))
    session = _session([page])
    with pytest.raises(RuntimeError, match="boom"):
        await _run("set-color-scheme", session, "dark")
