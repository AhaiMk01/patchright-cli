"""Unit tests for the TOON encoder (spec v4.4)."""

import datetime

import pytest

from patchright_cli.toon import encode

# -- Spec Appendix A examples -------------------------------------------------


def test_flat_object():
    assert encode({"id": 123, "name": "Ada", "active": True}) == "id: 123\nname: Ada\nactive: true"


def test_nested_object():
    assert encode({"user": {"id": 123, "name": "Ada"}}) == "user:\n  id: 123\n  name: Ada"


def test_primitive_array_inline():
    assert encode({"tags": ["admin", "ops", "dev"]}) == "tags[3]: admin,ops,dev"


def test_array_of_primitive_arrays():
    assert encode({"pairs": [[1, 2], [3, 4]]}) == "pairs[2]:\n  - [2]: 1,2\n  - [2]: 3,4"


def test_tabular_array():
    value = {"items": [{"sku": "A1", "qty": 2, "price": 9.99}, {"sku": "B2", "qty": 1, "price": 14.5}]}
    assert encode(value) == "items[2]{sku,qty,price}:\n  A1,2,9.99\n  B2,1,14.5"


def test_tabular_array_with_nested_field_group():
    value = {
        "orders": [
            {"id": 1, "customer": {"name": "Ada", "country": "DK"}, "total": 99},
            {"id": 2, "customer": {"country": "UK", "name": "Bob"}, "total": 149},
        ]
    }
    assert encode(value) == "orders[2]{id,customer{name,country},total}:\n  1,Ada,DK,99\n  2,Bob,UK,149"


def test_tabular_rows_follow_first_object_key_order():
    value = {"rows": [{"a": 1, "b": 2}, {"b": 4, "a": 3}]}
    assert encode(value) == "rows[2]{a,b}:\n  1,2\n  3,4"


def test_keyed_tabular_object():
    value = {"users": {"alice": {"age": 30, "city": "Berlin"}, "bob": {"age": 25, "city": "Oslo"}}}
    assert encode(value) == "users[2:]{age,city}:\n  alice: 30,Berlin\n  bob: 25,Oslo"


def test_keyed_tabular_at_root_omits_key():
    value = {"alice": {"age": 30}, "bob": {"age": 25}}
    assert encode(value) == "[2:]{age}:\n  alice: 30\n  bob: 25"


def test_single_entry_object_nests_instead_of_keyed():
    assert encode({"users": {"alice": {"age": 30}}}) == "users:\n  alice:\n    age: 30"


def test_mixed_array_uses_list_form():
    assert encode({"items": [1, {"a": 1}, "text"]}) == "items[3]:\n  - 1\n  - a: 1\n  - text"


def test_objects_as_list_items():
    value = {"items": [{"id": 1, "name": "First"}, {"id": 2, "name": "Second", "extra": True}]}
    assert encode(value) == ("items[2]:\n  - id: 1\n    name: First\n  - id: 2\n    name: Second\n    extra: true")


def test_tabular_array_as_first_field_of_list_item():
    value = {"items": [{"users": [{"id": 1, "name": "Ada"}, {"id": 2, "name": "Bob"}], "status": "active"}]}
    assert encode(value) == "items[1]:\n  - users[2]{id,name}:\n      1,Ada\n      2,Bob\n    status: active"


def test_nested_object_as_first_field_of_list_item():
    value = {"items": [{"user": {"id": 1}, "name": "x"}, {"user": {"id": 2}}]}
    assert encode(value) == "items[2]:\n  - user:\n      id: 1\n    name: x\n  - user:\n      id: 2"


def test_deeply_nested_tabular():
    value = {"root": {"level1": {"level2": {"level3": {"items": [{"id": 1, "val": "a"}, {"id": 2, "val": "b"}]}}}}}
    expected = "root:\n  level1:\n    level2:\n      level3:\n        items[2]{id,val}:\n          1,a\n          2,b"
    assert encode(value) == expected


def test_quoted_keys_in_headers():
    assert encode({"my-key": [1, 2, 3]}) == '"my-key"[3]: 1,2,3'
    value = {"x-items": [{"id": 1, "name": "Ada"}, {"id": 2, "name": "Bob"}]}
    assert encode(value) == '"x-items"[2]{id,name}:\n  1,Ada\n  2,Bob'


def test_non_uniform_objects_with_quoted_key():
    value = {"x-items": [{"id": 1}, {"id": 2, "label": "archived"}]}
    assert encode(value) == '"x-items"[2]:\n  - id: 1\n  - id: 2\n    label: archived'


def test_quoted_colons_in_tabular_cells():
    value = {"links": [{"id": 1, "url": "http://a:b"}, {"id": 2, "url": "https://example.com?q=a:b"}]}
    assert encode(value) == 'links[2]{id,url}:\n  1,"http://a:b"\n  2,"https://example.com?q=a:b"'


def test_unicode_and_emoji_stay_unquoted():
    assert encode({"message": "Hello 世界 👋", "tags": ["🎉", "🎊", "🎈"]}) == (
        "message: Hello 世界 👋\ntags[3]: 🎉,🎊,🎈"
    )


# -- Empty values -------------------------------------------------------------


def test_empty_containers():
    assert encode({"name": "", "tags": [], "meta": {}}) == 'name: ""\ntags: []\nmeta:'
    assert encode({}) == ""
    assert encode([]) == "[]"


def test_empty_object_and_empty_array_as_list_items():
    assert encode({"items": [{}, [], 1]}) == "items[3]:\n  -\n  - [0]:\n  - 1"


def test_array_with_empty_object_is_not_tabular():
    assert encode({"items": [{"a": 1}, {}]}) == "items[2]:\n  - a: 1\n  -"


# -- Root forms -----------------------------------------------------------------


def test_root_primitives():
    assert encode("hello") == "hello"
    assert encode(42) == "42"
    assert encode(None) == "null"
    assert encode("\ufeffbom") == '"\ufeffbom"'


def test_root_arrays():
    assert encode([1, 2]) == "[2]: 1,2"
    assert encode([{"a": 1}, {"a": 2}]) == "[2]{a}:\n  1\n  2"


def test_arrays_nested_in_list_items_use_list_form():
    value = [[{"a": 1}, {"a": 2}]]
    assert encode(value) == "[1]:\n  - [2]:\n    - a: 1\n    - a: 2"


# -- Quoting -------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "",
        " lead",
        "trail ",
        "\tlead",
        "true",
        "false",
        "null",
        "42",
        "-3.14",
        "05",
        "+1",
        "1e-6",
        "1E5",
        "a:b",
        'say "hi"',
        "back\\slash",
        "[x]",
        "{x}",
        "a,b",
        "-",
        "-x",
        "#tag",
        "line\nbreak",
    ],
)
def test_strings_that_must_be_quoted(text):
    assert encode({"v": text}).startswith('v: "')


@pytest.mark.parametrize(
    "text",
    ["hello world", "it's", "a|b", "True", "Null", "1.2.3", "x-1", "café", "NaN", "Infinity", "(no-op)"],
)
def test_strings_that_stay_unquoted(text):
    assert encode({"v": text}) == f"v: {text}"


def test_escapes():
    assert encode({"v": 'a"b\\c\nd\re\tf\x01'}) == 'v: "a\\"b\\\\c\\nd\\re\\tf\\u0001"'


def test_key_quoting():
    assert encode({"user.name": 1, "_x": 2, "1st": 3, "a b": 4, "": 5, "é": 6}) == (
        'user.name: 1\n_x: 2\n"1st": 3\n"a b": 4\n"": 5\n"é": 6'
    )


def test_unpaired_surrogate_is_an_error():
    with pytest.raises(ValueError, match="surrogate"):
        encode({"v": "bad\ud800"})


# -- Delimiters ------------------------------------------------------------------


def test_pipe_delimiter():
    assert encode({"tags": ["reading", "gaming", "coding"]}, delimiter="|") == "tags[3|]: reading|gaming|coding"
    assert encode({"tags": ["a,b", "c|d"]}, delimiter="|") == 'tags[2|]: a,b|"c|d"'


def test_tab_delimiter():
    value = {"items": [{"sku": "A1", "qty": 2}, {"sku": "B2", "qty": 1}]}
    assert encode(value, delimiter="\t") == "items[2\t]{sku\tqty}:\n  A1\t2\n  B2\t1"


def test_invalid_options():
    with pytest.raises(ValueError):
        encode({}, indent=0)
    with pytest.raises(ValueError):
        encode({}, delimiter=";")


def test_custom_indent():
    assert encode({"a": {"b": 1}}, indent=4) == "a:\n    b: 1"


# -- Numbers and normalization ---------------------------------------------------------


@pytest.mark.parametrize(
    ("number", "expected"),
    [
        (1.0, "1"),
        (-0.0, "0"),
        (1.5, "1.5"),
        (1e6, "1000000"),
        (1e-6, "0.000001"),
        (1e20, "100000000000000000000"),
        (1e21, "1e+21"),
        (1.5e-7, "1.5e-7"),
        (0.3333333333333333, "0.3333333333333333"),
        (9007199254740992, "9007199254740992"),
        (float("nan"), "null"),
        (float("inf"), "null"),
        (float("-inf"), "null"),
    ],
)
def test_number_canonical_form(number, expected):
    assert encode({"n": number}) == f"n: {expected}"


def test_host_type_normalization():
    value = {"when": datetime.date(2026, 10, 27), "set": (1, 2), "other": object()}
    assert encode(value) == "when: 2026-10-27\nset[2]: 1,2\nother: null"


def test_no_trailing_spaces_or_newline():
    out = encode({"a": {}, "b": [{}], "c": [1]})
    assert not out.endswith("\n")
    assert all(line == line.rstrip() for line in out.split("\n"))


def test_daemon_response_shape():
    response = {
        "success": True,
        "output": "### Tabs\n  [0] * https://example.com — Example",
        "data": [{"index": 0, "title": "Example", "url": "https://example.com", "active": True}],
    }
    assert encode(response) == (
        "success: true\n"
        'output: "### Tabs\\n  [0] * https://example.com — Example"\n'
        "data[1]{index,title,url,active}:\n"
        '  0,Example,"https://example.com",true'
    )
