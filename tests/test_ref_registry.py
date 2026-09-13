"""Unit tests for RefRegistry."""

import re
from unittest.mock import MagicMock

import pytest

from patchright_cli.ref_registry import RefRegistry, render_hits


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


def test_parse_retains_annotated_lines():
    registry = RefRegistry()
    raw = '- navigation "Main"\n  - link "Home"\n  - link "About"'
    result = registry.parse(raw)
    assert registry._lines == result.splitlines()
    assert len(registry._lines) == 3


def test_parse_records_line_index():
    registry = RefRegistry()
    raw = '- navigation "Main"\n  - link "Home"\n  - link "About"'
    registry.parse(raw)
    assert registry.entries["e1"].line_index == 0
    assert registry.entries["e2"].line_index == 1
    assert registry.entries["e3"].line_index == 2


def test_parse_line_index_counts_skipped_lines():
    # Property lines get no ref but still occupy a slot in _lines.
    registry = RefRegistry()
    raw = '- link "Home"\n  - /url: /home\n- button "Go"'
    registry.parse(raw)
    assert registry.entries["e1"].line_index == 0
    assert registry.entries["e2"].line_index == 2


def test_parse_search_text_falls_back_to_value():
    # `- text: Star 95.3k` has no quoted name; the value after the colon is
    # what a search should match, but entry.name must stay empty so that
    # resolve() does not pass a bogus name to get_by_role().
    registry = RefRegistry()
    raw = '- link "Home"\n- text: Star 95.3k'
    registry.parse(raw)
    assert registry.entries["e1"].search_text == "Home"
    assert registry.entries["e2"].name == ""
    assert registry.entries["e2"].search_text == "Star 95.3k"


def test_parse_resets_lines_between_calls():
    registry = RefRegistry()
    registry.parse('- link "A"\n- link "B"')
    registry.parse('- button "C"')
    assert registry._lines == ['- button "C" [ref=e1]']


SEARCH_YAML = (
    "- banner:\n"
    '  - heading "Get Started"\n'
    '  - link "Star this repo"\n'
    "    - /url: /star\n"
    "  - text: 95.3k stars\n"
    "- main:\n"
    '  - button "Star"\n'
    "  - paragraph: Starting is easy\n"
)


def test_search_substring_is_case_insensitive():
    registry = RefRegistry()
    registry.parse(SEARCH_YAML)
    hits, total = registry.search("star")
    refs = [h.ref for h in hits]
    assert total == 3  # heading "Get Started", link "Star this repo", button "Star"
    assert len(refs) == 3


def test_search_excludes_non_interactive_roles_by_default():
    registry = RefRegistry()
    registry.parse(SEARCH_YAML)
    hits, _ = registry.search("star")
    roles = {registry.entries[h.ref].role for h in hits}
    assert "text" not in roles
    assert "paragraph" not in roles
    assert roles == {"heading", "link", "button"}


def test_search_all_roles_includes_text_nodes():
    registry = RefRegistry()
    registry.parse(SEARCH_YAML)
    hits, total = registry.search("star", all_roles=True)
    roles = {registry.entries[h.ref].role for h in hits}
    assert total == 5  # + text "95.3k stars" + paragraph "Starting is easy"
    assert "text" in roles
    assert "paragraph" in roles


def test_search_ignores_property_lines():
    # `- /url: /star` contains "star" but is not a node and must not match.
    registry = RefRegistry()
    registry.parse(SEARCH_YAML)
    hits, _ = registry.search("/star", all_roles=True)
    assert hits == []


def test_search_regex_bare_pattern_is_case_insensitive():
    registry = RefRegistry()
    registry.parse(SEARCH_YAML)
    hits, total = registry.search("star (this|repo)", regex=True)
    assert total == 1
    assert registry.entries[hits[0].ref].name == "Star this repo"


def test_search_regex_slash_form_honours_flags():
    registry = RefRegistry()
    registry.parse(SEARCH_YAML)
    sensitive, _ = registry.search("/star this/", regex=True)
    insensitive, _ = registry.search("/star this/i", regex=True)
    assert sensitive == []
    assert len(insensitive) == 1


def test_search_invalid_regex_raises():
    registry = RefRegistry()
    registry.parse(SEARCH_YAML)
    with pytest.raises(re.error):
        registry.search("star (unclosed", regex=True)


def test_search_limit_caps_hits_but_not_total():
    registry = RefRegistry()
    registry.parse(SEARCH_YAML)
    hits, total = registry.search("star", limit=2)
    assert len(hits) == 2
    assert total == 3


def test_search_no_matches_returns_empty():
    registry = RefRegistry()
    registry.parse(SEARCH_YAML)
    hits, total = registry.search("nonexistent")
    assert hits == []
    assert total == 0


def test_search_hit_carries_ref_and_line_index():
    registry = RefRegistry()
    registry.parse(SEARCH_YAML)
    hits, _ = registry.search("Star this repo")
    entry = registry.entries[hits[0].ref]
    assert hits[0].line_index == entry.line_index
    assert registry._lines[hits[0].line_index].startswith('  - link "Star this repo"')


NESTED_YAML = (
    "- table:\n"
    "  - rowgroup:\n"
    '    - row "407 points by pluc 4 hours ago | hide | 321 comments":\n'
    '      - cell "407 points by pluc 4 hours ago | hide | 321 comments":\n'
    '        - link "hide":\n'
    "          - /url: hide?id=49489982\n"
    '    - row "83 points by lioeters 4 hours ago | hide | 11 comments":\n'
    '      - cell "83 points by lioeters 4 hours ago | hide | 11 comments":\n'
    '        - link "hide":\n'
    "          - /url: hide?id=49426995\n"
)


def test_breadcrumb_disambiguates_identical_nodes():
    registry = RefRegistry()
    registry.parse(NESTED_YAML)
    hits, total = registry.search("hide")
    assert total == 2
    assert "pluc" in hits[0].breadcrumb
    assert "lioeters" in hits[1].breadcrumb
    assert hits[0].breadcrumb != hits[1].breadcrumb


def test_breadcrumb_collapses_repeated_names():
    # `row "X" > cell "X"` carries the same name twice; only one survives.
    registry = RefRegistry()
    registry.parse(NESTED_YAML)
    hits, _ = registry.search("hide")
    assert hits[0].breadcrumb.count("407 points") == 1


def test_breadcrumb_truncates_long_names_with_ascii_ellipsis():
    registry = RefRegistry()
    registry.parse(NESTED_YAML)
    hits, _ = registry.search("hide")
    assert "..." in hits[0].breadcrumb
    assert "…" not in hits[0].breadcrumb


def test_breadcrumb_caps_ancestor_depth_at_three():
    registry = RefRegistry()
    registry.parse(NESTED_YAML)
    hits, _ = registry.search("hide")
    assert hits[0].breadcrumb.count(" > ") <= 2


def test_breadcrumb_empty_for_root_node():
    registry = RefRegistry()
    registry.parse('- button "Go"')
    hits, _ = registry.search("Go")
    assert hits[0].breadcrumb == ""


def test_block_includes_subtree_with_relative_indent():
    registry = RefRegistry()
    registry.parse(NESTED_YAML)
    hits, _ = registry.search("hide")
    block = hits[0].block
    assert block[0].startswith('- link "hide"')
    assert block[1] == "  - /url: hide?id=49489982"


def test_block_for_leaf_is_single_line():
    registry = RefRegistry()
    registry.parse('- heading "Title"\n- button "Go"')
    hits, _ = registry.search("Go")
    assert len(hits[0].block) == 1


def test_block_stops_at_sibling():
    registry = RefRegistry()
    raw = '- link "A":\n  - /url: /a\n- link "B":\n  - /url: /b'
    registry.parse(raw)
    hits, _ = registry.search("A")
    assert len(hits[0].block) == 2
    assert all("/b" not in line for line in hits[0].block)


def test_render_hits_reports_partial_count():
    registry = RefRegistry()
    registry.parse(NESTED_YAML)
    hits, total = registry.search("hide", limit=1)
    output = render_hits(hits, total, "hide")
    assert "1 of 2" in output


def test_render_hits_reports_full_count():
    registry = RefRegistry()
    registry.parse(NESTED_YAML)
    hits, total = registry.search("hide")
    output = render_hits(hits, total, "hide")
    assert "2 matches" in output
    assert " of " not in output.splitlines()[0]


def test_render_hits_empty_suggests_all_flag():
    output = render_hits([], 0, "nothing")
    assert 'No matches for "nothing"' in output
    assert "--all" in output


def test_render_hits_prefixes_breadcrumb_with_hash():
    registry = RefRegistry()
    registry.parse(NESTED_YAML)
    hits, total = registry.search("hide")
    output = render_hits(hits, total, "hide")
    assert "  # " in output
    assert "[ref=" in output


def test_render_hits_omits_breadcrumb_line_for_root_node():
    registry = RefRegistry()
    registry.parse('- button "Go"')
    hits, total = registry.search("Go")
    output = render_hits(hits, total, "Go")
    assert "#" not in output


def test_render_hits_has_no_section_headers():
    # `--raw` strips lines after a `### ` header; find output must survive it.
    registry = RefRegistry()
    registry.parse(NESTED_YAML)
    hits, total = registry.search("hide")
    output = render_hits(hits, total, "hide")
    assert "### " not in output


# -- Scoped snapshots must resolve within their scope -------------------------


def test_resolve_uses_the_page_when_the_snapshot_was_unscoped():
    from unittest.mock import MagicMock

    from patchright_cli.ref_registry import RefRegistry

    registry = RefRegistry()
    registry.parse('- button "Go"')
    page = MagicMock()
    registry.resolve(page, "e1")
    page.get_by_role.assert_called_once()


def test_resolve_searches_inside_the_scope_it_was_parsed_from():
    """nth is counted within the snapshotted subtree, so applying it to the
    whole page selects a different element entirely."""
    from unittest.mock import MagicMock

    from patchright_cli.ref_registry import RefRegistry

    root = MagicMock(name="scope")
    registry = RefRegistry(root=root)
    registry.parse('- button "Go"')

    page = MagicMock(name="page")
    registry.resolve(page, "e1")

    root.get_by_role.assert_called_once()
    page.get_by_role.assert_not_called()


def test_scoped_registry_applies_nth_within_the_scope():
    from unittest.mock import MagicMock

    from patchright_cli.ref_registry import RefRegistry

    root = MagicMock(name="scope")
    registry = RefRegistry(root=root)
    registry.parse('- button "Go"' + chr(10) + '- button "Go"')

    registry.resolve(MagicMock(), "e2")

    assert root.get_by_role.return_value.nth.call_args.args[0] == 1


# -- Accessible names containing quotes ---------------------------------------


def test_parse_handles_a_name_with_escaped_quotes():
    """Playwright JSON-stringifies the accessible name into the node key, so a
    name containing a quote arrives escaped."""
    from patchright_cli.ref_registry import RefRegistry

    registry = RefRegistry()
    line = '- button "Say \\"hi\\" now"'
    registry.parse(line)

    entry = registry.entries["e1"]
    assert entry.role == "button"
    assert entry.name == 'Say "hi" now'


def test_parse_does_not_truncate_at_an_inner_quote():
    from patchright_cli.ref_registry import RefRegistry

    registry = RefRegistry()
    registry.parse('- link "a \\"b\\" c"')
    assert registry.entries["e1"].name == 'a "b" c'


def test_resolve_passes_the_unescaped_name_to_the_locator():
    from unittest.mock import MagicMock

    from patchright_cli.ref_registry import RefRegistry

    registry = RefRegistry()
    registry.parse('- button "Say \\"hi\\""')
    page = MagicMock()
    registry.resolve(page, "e1")
    assert page.get_by_role.call_args.kwargs["name"] == 'Say "hi"'


DEEP_YAML = """- main "Outer"
  - navigation "Middle"
    - list "Inner"
      - listitem "Row"
        - group "Cell"
          - link "target"
"""


def test_breadcrumb_keeps_only_the_three_nearest_ancestors():
    """Binds the cap: five distinct ancestors, none of which collapse."""
    from patchright_cli.ref_registry import RefRegistry

    registry = RefRegistry()
    registry.parse(DEEP_YAML)
    hits, _ = registry.search("target", all_roles=True)

    crumb = hits[0].breadcrumb
    assert crumb.count(">") == 2, crumb
    # The rightmost entry is the direct parent; the outermost two are dropped.
    assert "group" in crumb
    assert "listitem" in crumb
    assert "list" in crumb
    assert "main" not in crumb
    assert "navigation" not in crumb
