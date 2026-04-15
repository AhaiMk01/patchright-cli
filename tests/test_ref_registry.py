"""Unit tests for RefRegistry."""

from unittest.mock import MagicMock

from patchright_cli.ref_registry import RefRegistry


def test_parse_simple_snapshot():
    registry = RefRegistry()
    raw = '- heading "Login"\n- textbox "Username"\n- button "Sign in"'
    result = registry.parse(raw)
    assert "[ref=e1]" in result
    assert "[ref=e2]" in result
    assert "[ref=e3]" in result
    assert "e1" in registry.entries
    assert registry.entries["e1"].role == "heading"
    assert registry.entries["e1"].name == "Login"
    assert registry.entries["e2"].role == "textbox"
    assert registry.entries["e3"].role == "button"


def test_parse_with_existing_props():
    registry = RefRegistry()
    raw = '- heading "Login" [level=2]'
    result = registry.parse(raw)
    assert result == '- heading "Login" [level=2] [ref=e1]'


def test_parse_duplicates_track_nth():
    registry = RefRegistry()
    raw = '- link "Home"\n- link "Home"'
    result = registry.parse(raw)
    lines = result.splitlines()
    assert "[ref=e1]" in lines[0]
    assert "[ref=e2]" in lines[1]
    assert registry.entries["e1"].nth == 0
    assert registry.entries["e2"].nth == 1


def test_resolve_locator():
    registry = RefRegistry()
    raw = '- button "OK"\n- button "Cancel"'
    registry.parse(raw)
    page = MagicMock()
    locator = MagicMock()
    page.get_by_role.return_value = locator

    registry.resolve(page, "e2")
    page.get_by_role.assert_called_once_with("button", name="Cancel", exact=True)
    locator.nth.assert_called_once_with(0)


def test_resolve_strips_at_sign():
    registry = RefRegistry()
    raw = '- link "Home"'
    registry.parse(raw)
    page = MagicMock()
    locator = MagicMock()
    page.get_by_role.return_value = locator

    registry.resolve(page, "@e1")
    page.get_by_role.assert_called_once_with("link", name="Home", exact=True)
    locator.nth.assert_called_once_with(0)


def test_resolve_missing_ref_raises():
    registry = RefRegistry()
    registry.parse('- link "Home"')
    page = MagicMock()
    try:
        registry.resolve(page, "e99")
    except ValueError as e:
        assert "e99" in str(e)
    else:
        raise AssertionError("Expected ValueError for missing ref")
