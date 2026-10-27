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
@pytest.mark.parametrize("feature", FEATURES)
async def test_clear_on_attached_session_drops_the_override(feature):
    # Over CDP, Playwright emulates no media, so the user's own browser
    # settings are the starting state: clearing must not force light etc.
    kwarg, value, _, _ = FEATURES[feature]
    page = _fake_page()
    session = _session([page])
    session.is_attached = True
    await _run(f"set-{feature}", session, value)
    await _run(f"clear-{feature}", session)
    page.emulate_media.assert_awaited_with(**{kwarg: "null"})


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
