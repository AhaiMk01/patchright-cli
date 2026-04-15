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


def test_parse_with_max_depth():
    registry = RefRegistry()
    raw = '- heading "Login"\n  - textbox "Username"\n  - button "Submit"\n    - link "Forgot"'
    result = registry.parse(raw, max_depth=1)
    lines = result.splitlines()
    assert "[ref=e1]" in lines[0]  # depth 0
    assert "[ref=e2]" in lines[1]  # depth 1
    assert "[ref=e3]" in lines[2]  # depth 1
    assert "[ref=" not in lines[3]  # depth 2 skipped


def test_parse_interactive_only():
    registry = RefRegistry()
    raw = (
        '- heading "Login"\n'
        '- paragraph "Welcome back"\n'
        '- textbox "Username"\n'
        '- img "Logo"\n'
        '- button "Sign in"\n'
        '- link "Forgot password"'
    )
    result = registry.parse(raw, interactive_only=True)
    # Only textbox, button, link should have refs
    assert len(registry.entries) == 3
    assert registry.entries["e1"].role == "textbox"
    assert registry.entries["e2"].role == "button"
    assert registry.entries["e3"].role == "link"
    # Non-interactive lines should still be present but without refs
    assert "heading" in result
    assert "[ref=" not in result.splitlines()[0]  # heading has no ref


def test_parse_interactive_only_nested():
    registry = RefRegistry()
    raw = (
        '- navigation "Main"\n'
        '  - link "Home"\n'
        '  - link "About"\n'
        '- main "Content"\n'
        '  - heading "Title"\n'
        '  - textbox "Search"'
    )
    registry.parse(raw, interactive_only=True)
    assert len(registry.entries) == 3  # 2 links + 1 textbox
    assert registry.entries["e1"].role == "link"
    assert registry.entries["e2"].role == "link"
    assert registry.entries["e3"].role == "textbox"


def test_parse_interactive_false_same_as_default():
    registry = RefRegistry()
    raw = '- heading "Login"\n- textbox "Username"\n- button "Sign in"'
    registry.parse(raw)
    count_default = len(registry.entries)

    registry2 = RefRegistry()
    registry2.parse(raw, interactive_only=False)
    count_explicit = len(registry2.entries)

    assert count_default == count_explicit == 3
