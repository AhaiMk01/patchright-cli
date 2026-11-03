"""Text operations on Playwright's AI snapshot (snapshot_text.py), and the one-snapshot-source rule."""

import re
from pathlib import Path

import pytest

from patchright_cli import snapshot_text as st

SRC = Path(__file__).resolve().parent.parent / "src" / "patchright_cli"


# -- one snapshot source ---------------------------------------------------------


def test_only_snapshot_py_calls_aria_snapshot():
    """Any other aria_snapshot() call would replace the page's ref map and break refs."""
    calls = {
        path.name: len(re.findall(r"\.aria_snapshot\(", path.read_text(encoding="utf-8"))) for path in SRC.rglob("*.py")
    }
    calls = {name: n for name, n in calls.items() if n}
    # ai_snapshot (full page) and probe_ref (depth-0 element probe), nothing else.
    assert calls == {"snapshot.py": 2}


def test_snapshot_calls_are_ai_mode():
    text = (SRC / "snapshot.py").read_text(encoding="utf-8")
    for call in re.findall(r"\.aria_snapshot\(([^)]*)\)", text):
        assert 'mode="ai"' in call


# -- refs ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "ref"),
    [
        ("e5", "e5"),
        ("@e5", "e5"),
        ("ref=e5", "e5"),
        ("[ref=e5]", "e5"),
        (" f3e12 ", "f3e12"),
        ("@f1e2", "f1e2"),
        ("e", None),
        ("5", None),
        ("button", None),
        ("#main", None),
        ("f3", None),
        ("e5x", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_ref(value, ref):
    assert st.normalize_ref(value) == ref


def test_frame_prefixes():
    assert st.frame_prefix("e5") == ""
    assert st.frame_prefix("f3e5") == "f3"
    assert st.main_frame_prefix("- generic [ref=f2e1]:\n  - button [ref=f2e2]") == "f2"
    assert st.main_frame_prefix("") == ""


# -- trimming -----------------------------------------------------------------------------

RAW_CONTROLS = """- generic [active] [ref=e1]:
  - heading "Controls" [level=1] [ref=e2]
  - region "Form" [ref=e3]:
    - generic [ref=e4]:
      - text: Name
      - textbox "Name" [ref=e5]
    - link "Jump" [ref=e6] [cursor=pointer]:
      - /url: "#two"
    - generic [ref=e7] [cursor=pointer]: Clickable div
    - generic "Named wrapper" [ref=e8]:
      - button "Go" [ref=e9] [cursor=pointer]
    - generic [ref=e10]"""


def test_trim_hoists_bare_generic_wrappers_including_the_body_root():
    assert st.trim(RAW_CONTROLS) == "\n".join(
        [
            '- heading "Controls" [level=1] [ref=e2]',
            '- region "Form" [ref=e3]:',
            "  - text: Name",
            '  - textbox "Name" [ref=e5]',
            '  - link "Jump" [ref=e6]:',
            '    - /url: "#two"',
            "  - generic [ref=e7] [cursor=pointer]: Clickable div",
            '  - generic "Named wrapper" [ref=e8]:',
            '    - button "Go" [ref=e9]',
            "  - generic [ref=e10]",
        ]
    )


def test_trim_keeps_cursor_pointer_only_where_it_is_the_clickable_hint():
    out = st.trim(RAW_CONTROLS)
    assert "generic [ref=e7] [cursor=pointer]" in out
    assert 'link "Jump" [ref=e6] [cursor=pointer]' not in out


def test_trim_hoists_wrappers_that_carry_boxes():
    raw = (
        "- generic [active] [ref=e1] [box=8,21,1264,511]:\n"
        "  - generic [ref=e4] [box=8,123,219,17]:\n"
        '    - textbox "Name" [ref=e5] [box=50,122,177,21]'
    )
    assert st.trim(raw) == '- textbox "Name" [ref=e5] [box=50,122,177,21]'


def test_trim_keeps_iframe_content_and_options():
    raw = (
        "- generic [active] [ref=e1]:\n"
        '  - combobox "Color" [ref=e2]:\n'
        '    - option "Red" [selected]\n'
        '    - option "Green"\n'
        "  - iframe [ref=e3]:\n"
        "    - generic [ref=f1e1]:\n"
        '      - button "Inner" [ref=f1e2]'
    )
    assert st.trim(raw) == (
        '- combobox "Color" [ref=e2]:\n  - option "Red" [selected]\n  - option "Green"\n'
        '- iframe [ref=e3]:\n  - button "Inner" [ref=f1e2]'
    )


def test_trim_keeps_a_text_value_ending_in_a_colon():
    assert st.trim("- generic [ref=e1]:\n  - text: Total:") == "- text: Total:"


# -- scoping ----------------------------------------------------------------------------


def test_subtree_slices_the_refs_block():
    assert (
        st.subtree(RAW_CONTROLS, "e8")
        == '- generic "Named wrapper" [ref=e8]:\n  - button "Go" [ref=e9] [cursor=pointer]'
    )
    assert st.subtree(RAW_CONTROLS, "e9") == '- button "Go" [ref=e9] [cursor=pointer]'
    assert st.subtree(RAW_CONTROLS, "e99") is None


def test_subtree_does_not_match_a_longer_ref():
    assert st.subtree("- button [ref=e10]\n- button [ref=e1]", "e1") == "- button [ref=e1]"


# -- --depth ------------------------------------------------------------------------------


def test_limit_depth_marks_hidden_refs():
    text = st.trim(RAW_CONTROLS)
    assert st.limit_depth(text, 0) == "\n".join(
        ['- heading "Controls" [level=1] [ref=e2]', '- region "Form" [ref=e3]: # +6 refs']
    )
    one = st.limit_depth(text, 1).splitlines()
    assert '  - link "Jump" [ref=e6]: # +more' in one
    assert '  - generic "Named wrapper" [ref=e8]: # +1 ref' in one
    assert '  - textbox "Name" [ref=e5]' in one


def test_limit_depth_keeps_everything_when_deep_enough():
    text = st.trim(RAW_CONTROLS)
    assert st.limit_depth(text, 10) == text


# -- -i -------------------------------------------------------------------------------------


def test_interactive_only_keeps_controls_and_named_containers():
    raw = (
        "- generic [active] [ref=e1]:\n"
        '  - heading "Title" [level=1] [ref=e2]\n'
        '  - region "Form" [ref=e3]:\n'
        "    - generic [ref=e4]:\n"
        "      - text: Name\n"
        '      - textbox "Name" [ref=e5]\n'
        '    - combobox "Color" [ref=e6]:\n'
        '      - option "Red" [selected]\n'
        '    - link "Docs" [ref=e7] [cursor=pointer]:\n'
        '      - /url: "/docs"\n'
        "  - list [ref=e8]:\n"
        "    - listitem [ref=e9]:\n"
        '      - button "Save" [ref=e10]\n'
        "  - generic [ref=e11] [cursor=pointer]: Card\n"
        '  - region "Empty" [ref=e12]:\n'
        "    - paragraph [ref=e13]: Nothing to click\n"
        "  - iframe [ref=e14]:\n"
        '    - checkbox "Inner" [ref=f1e2]\n'
        "  - paragraph [ref=e15]: Text only"
    )
    assert st.interactive_only(st.trim(raw)) == "\n".join(
        [
            '- region "Form" [ref=e3]:',
            '  - textbox "Name" [ref=e5]',
            '  - combobox "Color" [ref=e6]:',
            '    - option "Red" [selected]',
            '  - link "Docs" [ref=e7]',
            '- button "Save" [ref=e10]',
            "- generic [ref=e11] [cursor=pointer]: Card",
            "- iframe [ref=e14]:",
            '  - checkbox "Inner" [ref=f1e2]',
        ]
    )


# -- --boxes in iframes ---------------------------------------------------------------------

RAW_BOXES = (
    "- generic [active] [ref=e1] [box=8,21,748,268]:\n"
    '  - button "Submit" [ref=e3] [box=8,269,58,21]\n'
    "  - iframe [ref=e4] [box=70,80,404,204]:\n"
    "    - generic [ref=f1e1] [box=8,8,384,40]:\n"
    '      - button "Inner" [ref=f1e4] [box=260,9,58,21]\n'
    "      - iframe [ref=f1e5] [box=0,50,100,100]:\n"
    '        - button "Deep" [ref=f2e2] [box=5,5,10,10]'
)


def test_frame_box_anchors_skip_the_main_frame():
    assert st.frame_box_anchors(RAW_BOXES) == {"f1": ("f1e1", (8, 8)), "f2": ("f2e2", (5, 5))}


def test_shift_frame_boxes_moves_each_frame_by_its_offset():
    out = st.shift_frame_boxes(RAW_BOXES, {"f1": (72, 82), "f2": (72, 132)})
    assert '  - button "Submit" [ref=e3] [box=8,269,58,21]' in out
    assert '      - button "Inner" [ref=f1e4] [box=332,91,58,21]' in out
    assert '        - button "Deep" [ref=f2e2] [box=77,137,10,10]' in out
    assert st.shift_frame_boxes(RAW_BOXES, {}) == RAW_BOXES


# -- node lines ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "node"),
    [
        ('- button "Go" [ref=e2]', ("button", "Go")),
        ('  - link "More" [ref=e3] [cursor=pointer]:', ("link", "More")),
        ('- button "Say \\"hi\\"" [ref=e4]', ("button", 'Say "hi"')),
        ("- generic [active] [ref=e1]:", ("generic", "")),
        ("- text: Name", ("text", "")),
        ("- link /about/ [ref=e5]:", ("link", "/about/")),
        ("- 'button \"a: b\"' [ref=e6]", ("button", "a: b")),
        ('- /url: "#a"', None),
    ],
)
def test_parse_node(line, node):
    assert st.parse_node(line) == node


# -- find -------------------------------------------------------------------------------------

FIND_TEXT = "\n".join(
    [
        '- navigation "Main" [ref=e1]:',
        '  - link "Sign in" [ref=e2]',
        '  - link "Sign up" [ref=e3]:',
        '    - /url: "/join"',
        "- text: Signal strength",
        '- combobox "Color" [ref=e4]:',
        '  - option "Green"',
        '- region "Results" [ref=e5]:',
        "  - list [ref=e6]:",
        "    - listitem [ref=e7]:",
        '      - button "Save" [ref=e8]',
        "    - listitem [ref=e9]:",
        '      - button "Save" [ref=e10]',
        "- paragraph [ref=e11]: Plain paragraph text",
    ]
)


def test_search_substring_is_case_insensitive_and_interactive_by_default():
    hits, total = st.SnapshotIndex(FIND_TEXT).search("sign")
    assert [h.ref for h in hits] == ["e2", "e3"]
    assert total == 2


def test_search_all_includes_text_and_values():
    hits, _ = st.SnapshotIndex(FIND_TEXT).search("sign", all_roles=True)
    assert [h.node.role for h in hits] == ["link", "link", "text"]
    hits, _ = st.SnapshotIndex(FIND_TEXT).search("plain", all_roles=True)
    assert [h.ref for h in hits] == ["e11"]


def test_search_never_matches_property_lines():
    hits, _ = st.SnapshotIndex(FIND_TEXT).search("join", all_roles=True)
    assert hits == []


def test_search_regex_forms():
    index = st.SnapshotIndex(FIND_TEXT)
    assert [h.ref for h in index.search("^sign", regex=True)[0]] == ["e2", "e3"]
    assert [h.ref for h in index.search("/^Sign/", regex=True)[0]] == ["e2", "e3"]
    assert index.search("/^sign/", regex=True)[0] == []
    with pytest.raises(re.error):
        index.search("(", regex=True)


def test_search_limit_caps_hits_but_not_total():
    hits, total = st.SnapshotIndex(FIND_TEXT).search("s", limit=1)
    assert len(hits) == 1
    assert total > 1


def test_refless_hit_names_its_nearest_ref():
    index = st.SnapshotIndex(FIND_TEXT)
    hits, _ = index.search("green")
    assert hits[0].ref is None
    assert hits[0].node.within == "e4"
    assert hits[0].block == ['- option "Green" [in=e4]']
    text_hit = index.search("signal", all_roles=True)[0][0]
    assert text_hit.node.within is None  # a root-level text line has no ref'd ancestor
    assert text_hit.block == ["- text: Signal strength"]


def test_in_tag_goes_before_a_value():
    index = st.SnapshotIndex('- region "R" [ref=e1]:\n  - text: Hello there')
    hit = index.search("hello", all_roles=True)[0][0]
    assert hit.block == ["- text [in=e1]: Hello there"]


def test_breadcrumb_and_block():
    index = st.SnapshotIndex(FIND_TEXT)
    hits, _ = index.search("save")
    assert [h.breadcrumb for h in hits] == ['region "Results" > list > listitem'] * 2
    sign_up = index.search("sign up")[0][0]
    assert sign_up.breadcrumb == 'navigation "Main"'
    assert sign_up.block == ['- link "Sign up" [ref=e3]:', '  - /url: "/join"']


def test_render_hits():
    index = st.SnapshotIndex(FIND_TEXT)
    hits, total = index.search("save", limit=1)
    out = st.render_hits(hits, total, "save")
    assert out.startswith('Found 1 of 2 matches for "save".')
    assert '  # region "Results" > list > listitem' in out
    assert "###" not in out
