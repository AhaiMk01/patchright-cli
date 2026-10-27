"""TOON (Token-Oriented Object Notation) encoder.

Implements the encoder side of the TOON specification v4.4
(https://github.com/toon-format/spec/blob/main/SPEC.md): objects, inline
primitive arrays, tabular arrays (including nested field groups), keyed tabular
objects, list-form arrays, and the spec's quoting and escaping rules.

Host-type normalization (§3): dicts become objects (keys via ``str()``), lists,
tuples, sets and frozensets become arrays, ``date``/``datetime``/``time`` become
ISO 8601 strings, NaN and ±Infinity become ``null``, and any other value becomes
``null``. Python ints of any size are emitted as plain decimal digits.
"""

from __future__ import annotations

import datetime
import math
import re
from decimal import Decimal

__all__ = ["encode"]

_UNQUOTED_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")
_NUMERIC_LIKE = re.compile(r"[+-]?[0-9]+(?:\.[0-9]+)?(?:e[+-]?[0-9]+)?", re.IGNORECASE)
_NEEDS_QUOTE_CHARS = frozenset(':"\\[]{}')
_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}
_DELIMITER_SYMBOLS = {",": "", "\t": "\t", "|": "|"}

# A field spec describes a tabular column layout: a list of (key, sub-spec) pairs,
# where sub-spec is None for a primitive column or a nested field spec for a
# nested-uniform column (§9.3).
_FieldSpec = list


def encode(value, *, indent: int = 2, delimiter: str = ",") -> str:
    """Encode a JSON-like Python value as a TOON document (no trailing newline)."""
    if not isinstance(indent, int) or isinstance(indent, bool) or indent < 1:
        raise ValueError(f"indent must be a positive integer, got {indent!r}")
    if delimiter not in _DELIMITER_SYMBOLS:
        raise ValueError(f"delimiter must be one of ',', '\\t', '|', got {delimiter!r}")
    encoder = _Encoder(delimiter)
    lines = encoder.root(_normalize(value))
    pad = " " * indent
    return "\n".join(pad * depth + text for depth, text in lines)


def _normalize(value):
    """Map a host value onto the JSON data model (§3)."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_normalize(v) for v in value]
    if isinstance(value, datetime.date | datetime.time):
        return value.isoformat()
    return None


def _is_primitive(value) -> bool:
    return value is None or isinstance(value, bool | int | float | str)


def _check_scalar_values(text: str) -> None:
    if any("\ud800" <= ch <= "\udfff" for ch in text):
        raise ValueError("TOON cannot encode a string containing an unpaired surrogate")


def _quote(text: str) -> str:
    _check_scalar_values(text)
    out = []
    for ch in text:
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif ch < " ":
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _encode_key(key: str) -> str:
    if _UNQUOTED_KEY.fullmatch(key):
        return key
    return _quote(key)


def _format_number(n: int | float) -> str:
    if isinstance(n, int):
        return str(n)
    if n == 0:
        return "0"  # also folds -0.0
    shortest = repr(n)  # fewest digits that round-trip
    if 1e-6 <= abs(n) < 1e21:
        text = format(Decimal(shortest), "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text
    mantissa, _, exponent = shortest.partition("e")
    exp = int(exponent)
    return f"{mantissa}e{'+' if exp >= 0 else '-'}{abs(exp)}"


def _cells_for(obj: dict, spec: _FieldSpec) -> list:
    """Primitive leaf values of `obj` in depth-first pre-order of `spec`."""
    cells = []
    for key, sub in spec:
        if sub is None:
            cells.append(obj[key])
        else:
            cells.extend(_cells_for(obj[key], sub))
    return cells


def _field_spec(objects: list) -> _FieldSpec | None:
    """Tabular layout shared by `objects`, or None when they are not uniform (§9.3)."""
    if not objects or not all(isinstance(o, dict) and o for o in objects):
        return None
    keys = set(objects[0])
    if any(set(o) != keys for o in objects[1:]):
        return None
    spec: _FieldSpec = []
    for key in objects[0]:
        column = [o[key] for o in objects]
        if all(_is_primitive(v) for v in column):
            spec.append((key, None))
            continue
        sub = _field_spec(column)
        if sub is None:
            return None
        spec.append((key, sub))
    return spec


def _keyed_spec(obj: dict) -> _FieldSpec | None:
    """Layout for keyed tabular form (§9.5), or None when the object does not qualify."""
    if len(obj) < 2:
        return None
    return _field_spec(list(obj.values()))


class _Encoder:
    def __init__(self, delimiter: str):
        self.delimiter = delimiter
        self.symbol = _DELIMITER_SYMBOLS[delimiter]

    # -- primitives ----------------------------------------------------------

    def primitive(self, value, *, root: bool = False) -> str:
        if value is None:
            return "null"
        if value is True:
            return "true"
        if value is False:
            return "false"
        if isinstance(value, int | float):
            return _format_number(value)
        return self.string(value, root=root)

    def string(self, text: str, *, root: bool = False) -> str:
        if self._needs_quotes(text, root):
            return _quote(text)
        _check_scalar_values(text)
        return text

    def _needs_quotes(self, text: str, root: bool) -> bool:
        if not text:
            return True
        if text[0] in " \t" or text[-1] in " \t":
            return True
        if text in ("true", "false", "null"):
            return True
        if _NUMERIC_LIKE.fullmatch(text):
            return True
        if any(ch in _NEEDS_QUOTE_CHARS or ch < " " for ch in text):
            return True
        if self.delimiter in text:
            return True
        if text[0] in "-#":
            return True
        return root and text[0] == "﻿"

    def join(self, values) -> str:
        return self.delimiter.join(self.primitive(v) for v in values)

    # -- headers -------------------------------------------------------------

    def fields(self, spec: _FieldSpec) -> str:
        parts = []
        for key, sub in spec:
            part = _encode_key(key)
            if sub is not None:
                part += self.fields(sub)
            parts.append(part)
        return "{" + self.delimiter.join(parts) + "}"

    def bracket(self, length: int, *, keyed: bool = False) -> str:
        return f"[{length}{':' if keyed else ''}{self.symbol}]"

    # -- structure -----------------------------------------------------------

    def root(self, value) -> list[tuple[int, str]]:
        if _is_primitive(value):
            return [(0, self.primitive(value, root=True))]
        if isinstance(value, list):
            if not value:
                return [(0, "[]")]
            return self.array("", value, 0)
        if not value:
            return []  # empty root object: empty document
        spec = _keyed_spec(value)
        if spec is not None:
            return self.keyed("", value, spec, 0)
        return self.object_fields(value, 0)

    def object_fields(self, obj: dict, depth: int) -> list[tuple[int, str]]:
        lines = []
        for key, value in obj.items():
            lines.extend(self.field(key, value, depth))
        return lines

    def field(self, key: str, value, depth: int) -> list[tuple[int, str]]:
        name = _encode_key(key)
        if _is_primitive(value):
            return [(depth, f"{name}: {self.primitive(value)}")]
        if isinstance(value, list):
            if not value:
                return [(depth, f"{name}: []")]
            return self.array(name, value, depth)
        if not value:
            return [(depth, f"{name}:")]
        spec = _keyed_spec(value)
        if spec is not None:
            return self.keyed(name, value, spec, depth)
        return [(depth, f"{name}:")] + self.object_fields(value, depth + 1)

    def keyed(self, name: str, obj: dict, spec: _FieldSpec, depth: int) -> list[tuple[int, str]]:
        lines = [(depth, f"{name}{self.bracket(len(obj), keyed=True)}{self.fields(spec)}:")]
        for key, entry in obj.items():
            lines.append((depth + 1, f"{_encode_key(key)}: {self.join(_cells_for(entry, spec))}"))
        return lines

    def array(self, name: str, items: list, depth: int, *, tabular: bool = True) -> list[tuple[int, str]]:
        """Non-empty array under header `name` (empty for root or list-item position)."""
        header = name + self.bracket(len(items))
        if all(_is_primitive(v) for v in items):
            return [(depth, f"{header}: {self.join(items)}")]
        spec = _field_spec(items) if tabular else None
        if spec is not None:
            lines = [(depth, f"{header}{self.fields(spec)}:")]
            lines.extend((depth + 1, self.join(_cells_for(item, spec))) for item in items)
            return lines
        lines = [(depth, f"{header}:")]
        for item in items:
            lines.extend(self.list_item(item, depth + 1))
        return lines

    def list_item(self, item, depth: int) -> list[tuple[int, str]]:
        if _is_primitive(item):
            return [(depth, f"- {self.primitive(item)}")]
        if isinstance(item, list):
            if not item:
                return [(depth, f"- {self.bracket(0)}:")]
            # A keyless header is the list item itself, so its items sit one level
            # below the hyphen, and tabular form is unavailable here (§9.4, §10).
            lines = self.array("", item, depth, tabular=False)
            lines[0] = (depth, "- " + lines[0][1])
            return lines
        if not item:
            return [(depth, "-")]
        # The first field rides on the hyphen line but counts as depth+1, so any
        # scope it opens has its content at depth+2 (§10).
        entries = iter(item.items())
        first_key, first_value = next(entries)
        lines = self.field(first_key, first_value, depth + 1)
        lines[0] = (depth, "- " + lines[0][1])
        for key, value in entries:
            lines.extend(self.field(key, value, depth + 1))
        return lines
