"""Value checks for flags and positionals, shared by the CLI and the daemon.

The CLI runs them before it contacts the daemon, so a bad value is a usage
error (exit 2) that costs no browser round trip. The daemon runs the same
checks for clients that skip the CLI, so both report a bad value the same way.
Only shape is checked here (a whole number, one of a set, a non-empty value);
anything that needs the page stays in the handler.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from patchright_cli.snapshot_text import normalize_ref


class ValueProblem(ValueError):
    """A flag or positional value of the wrong shape: one message, then help lines."""

    def __init__(self, message: str, *help_lines: str, positional: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.help_lines = help_lines
        self.positional = positional  # a bad positional: the caller adds the usage line


@dataclass(frozen=True)
class Int:
    """A whole number, optionally bounded (inclusive)."""

    lo: int | None = None
    hi: int | None = None
    example: int = 1

    def describe(self) -> str:
        if self.lo is not None and self.hi is not None:
            return f"a whole number from {self.lo} to {self.hi}"
        if self.lo == 0:
            return "a whole number, 0 or more"
        if self.lo is not None:
            return f"a whole number, {self.lo} or more"
        return "a whole number"

    def parse(self, raw: str) -> bool:
        try:
            value = int(raw)
        except ValueError:
            return False
        return (self.lo is None or value >= self.lo) and (self.hi is None or value <= self.hi)


@dataclass(frozen=True)
class Number:
    """A finite decimal number, optionally with a lower bound (inclusive)."""

    lo: float | None = None
    unit: str = ""
    example: str = "100"

    def describe(self) -> str:
        unit = f" of {self.unit}" if self.unit else ""
        if self.lo == 0:
            return f"a number{unit}, 0 or more"
        return f"a number{unit}"

    def parse(self, raw: str) -> bool:
        try:
            value = float(raw)
        except ValueError:
            return False
        return math.isfinite(value) and (self.lo is None or value >= self.lo)


@dataclass(frozen=True)
class Seconds:
    """A finite number of seconds above 0."""

    example: str = "60"

    def describe(self) -> str:
        return "a number of seconds above 0"

    def parse(self, raw: str) -> bool:
        try:
            value = float(raw)
        except ValueError:
            return False
        return math.isfinite(value) and value > 0


@dataclass(frozen=True)
class Choice:
    """One of a fixed set of words."""

    values: tuple[str, ...]

    @property
    def example(self) -> str:
        return self.values[0]

    def describe(self) -> str:
        return "one of: " + ", ".join(self.values)

    def parse(self, raw: str) -> bool:
        return raw in self.values


@dataclass(frozen=True)
class Text:
    """Any string; `empty` says whether `--flag=` (an empty value) is allowed."""

    example: str
    empty: bool = False

    def describe(self) -> str:
        return "a value"

    def parse(self, raw: str) -> bool:
        return self.empty or raw != ""


@dataclass(frozen=True)
class Ref:
    """An element ref as a snapshot prints it (`e5`, `f3e5`, also `@e5` and `[ref=e5]`)."""

    example: str = "e5"

    def describe(self) -> str:
        return "an element ref from a snapshot (quote an expression with spaces as one argument)"

    def parse(self, raw: str) -> bool:
        return normalize_ref(raw) is not None


SCROLL = Choice(("auto", "none"))
BUTTON = Choice(("left", "right", "middle"))
CALLOUT_POSITIONS = ("top-left", "top-right", "bottom-left", "bottom-right")
WAIT_STATES = ("visible", "hidden", "attached", "detached")
# The `type` of CDP's Runtime.consoleAPICalled, which console capture records;
# `warn` is the handler's alias for `warning`.
CONSOLE_LEVELS = (
    "log", "info", "warning", "warn", "error", "debug", "trace", "assert", "dir", "dirxml", "table",
    "clear", "count", "timeEnd", "startGroup", "startGroupCollapsed", "endGroup", "profile", "profileEnd",
)  # fmt: skip
MAX_VIDEO_FPS = 60
FILENAME = Text("out.png")

# Flags every command may carry.
GLOBAL_FLAG_KINDS: dict[str, object] = {
    "timeout": Seconds("1800"),
    "timeout-action": Number(0, "milliseconds", "10000"),
    "timeout-navigation": Number(0, "milliseconds", "30000"),
    "show-port": Int(1, 65535, 9322),
}

# command -> flag -> kind. A flag missing here takes any value, or none.
FLAG_KINDS: dict[str, dict[str, object]] = {
    "attach": {"cdp-timeout": Int(0, None, 30000), "cdp-headers": Text("Authorization: Bearer x")},
    "click": {"scroll": SCROLL, "modifiers": Text("Alt,Shift")},
    "dblclick": {"modifiers": Text("Alt,Shift")},
    "hover": {"scroll": SCROLL},
    "check": {"scroll": SCROLL},
    "drag": {"scroll": SCROLL},
    "snapshot": {"depth": Int(0, None, 3), "selector": Text("#main"), "filename": Text("page.yml")},
    "find": {
        "limit": Int(1, None, 20),
        "role": Text("button"),
        "name": Text("Submit"),
        "description": Text("Deletes"),
    },
    "eval": {"file": Text("script.js")},
    "run-code": {"file": Text("setup.js")},
    "html": {"selector": Text("#main"), "filename": Text("page.html"), "max-chars": Int(0, None, 8000)},
    "screenshot": {"filename": FILENAME},
    "drop": {"path": Text("file.txt"), "data": Text("text/plain=hello")},
    "wait": {"url": Text("**/dashboard")},
    "wait-for": {"state": Choice(WAIT_STATES)},
    "cookie-list": {"domain": Text("example.com"), "path": Text("/")},
    "cookie-delete": {"domain": Text("example.com")},
    "cookie-clear": {"domain": Text("example.com")},
    "cookie-set": {
        "domain": Text("example.com"),
        "path": Text("/"),
        "expires": Int(None, None, 1767225600),
        "sameSite": Choice(("Strict", "Lax", "None")),
    },
    "grant-permissions": {"origin": Text("https://example.com")},
    "passkey-list": {"rp-id": Text("example.com"), "id": Text("<credential-id>")},
    "passkey-add": {
        "id": Text("<base64url>"),
        "user-handle": Text("<base64url>"),
        "private-key": Text("<base64url>"),
        "public-key": Text("<base64url>"),
        "sign-count": Int(0, None, 0),
    },
    "passkey-clear": {"rp-id": Text("example.com")},
    "passkey-export": {"rp-id": Text("example.com")},
    "route": {
        "status": Int(100, 599, 404),
        "body": Text("hello", empty=True),
        "content-type": Text("application/json"),
        "header": Text("X-Test:1"),
        "remove-header": Text("X-Test"),
    },
    "video-start": {"fps": Int(1, MAX_VIDEO_FPS, 30)},
    "video-stop": {"filename": Text("demo.webm")},
    "video-show-actions": {"duration": Int(1, None, 600), "position": Choice(CALLOUT_POSITIONS)},
    "pdf": {"filename": Text("page.pdf")},
    "pick-locator": {"wait": Seconds("120")},
    "highlight": {"style": Text("outline: 2px dashed red")},
    "show": {"wait": Seconds("600")},
}

# command -> flags that may be given more than once; the values then arrive as a list.
# Any other flag given twice keeps its last value.
REPEATABLE_FLAGS: dict[str, frozenset[str]] = {"route": frozenset({"header"})}

# command -> (name, kind) per positional, in usage order; None skips one.
ARG_KINDS: dict[str, list[tuple[str, object] | None]] = {
    "eval": [None, ("[ref]", Ref())],
    "click": [None, ("[button]", BUTTON)],
    "dblclick": [None, ("[button]", BUTTON)],
    "mousedown": [("[button]", BUTTON)],
    "mouseup": [("[button]", BUTTON)],
    "mousemove": [("<x>", Number(example="100")), ("<y>", Number(example="200"))],
    "mousewheel": [("<dx>", Number(example="0")), ("<dy>", Number(example="100"))],
    "scroll": [("<dx>", Number(example="0")), ("<dy>", Number(example="500"))],
    "resize": [("<w>", Int(1, None, 1280)), ("<h>", Int(1, None, 800))],
    "wait": [("[ms]", Int(0, None, 1000))],
    "tab-select": [("<index>", Int(0, None, 0))],
    "tab-close": [("[index]", Int(0, None, 0))],
    "set-color-scheme": [("<scheme>", Choice(("dark", "light", "no-preference")))],
    "set-reduced-motion": [("<motion>", Choice(("reduce", "no-preference")))],
    "set-forced-colors": [("<colors>", Choice(("active", "none")))],
    "set-contrast": [("<contrast>", Choice(("more", "no-preference")))],
    "set-media": [("<media>", Choice(("screen", "print")))],
    "network-state-set": [("<state>", Choice(("online", "offline")))],
    "console": [("[level]", Choice(CONSOLE_LEVELS))],
}


def check_flag(flag: str, kind, value) -> None:
    """Raise ValueProblem unless `value` (a string, a number, or True for a bare flag) fits `kind`."""
    if value is True or value is False or value is None:
        raise ValueProblem(
            f"--{flag} needs a value",
            f"help: --{flag} takes {kind.describe()}, written --{flag}=<value>, e.g. --{flag}={kind.example}",
        )
    if isinstance(value, list):  # a repeated flag (route --header) arrives as a list
        for item in value:
            check_flag(flag, kind, item)
        return
    raw = str(value)
    if not kind.parse(raw):
        raise ValueProblem(
            f"invalid --{flag} value `{raw}`",
            f"help: --{flag} takes {kind.describe()}, e.g. --{flag}={kind.example}",
        )


def check_flags(command: str, options: dict) -> None:
    """The value of every typed flag in `options`, global ones included."""
    for flag, kind in {**GLOBAL_FLAG_KINDS, **FLAG_KINDS.get(command, {})}.items():
        if flag in options:
            check_flag(flag, kind, options[flag])


def check_args(command: str, args: list, options: dict) -> None:
    """Typed positionals, then flag combinations that need the positionals to judge."""
    for i, entry in enumerate(ARG_KINDS.get(command, [])):
        if entry is None or i >= len(args):
            continue
        name, kind = entry
        raw = str(args[i])
        if not kind.parse(raw):
            raise ValueProblem(
                f"invalid {name} value `{raw}` for `{command}`",
                f"help: {name} takes {kind.describe()}, e.g. {command} {_example_args(command)}",
                positional=True,
            )
    _check_combinations(command, args, options)


# The untyped positionals of a command whose example needs more than a ref.
_EXAMPLE_ARGS = {"eval": '"el => el.textContent" e5'}


def _example_args(command: str) -> str:
    if command in _EXAMPLE_ARGS:
        return _EXAMPLE_ARGS[command]
    return " ".join("e5" if entry is None else str(entry[1].example) for entry in ARG_KINDS[command])


def check_command(command: str, args: list, options: dict) -> None:
    """Every shape check for one invocation, as the daemon runs it."""
    check_flags(command, options)
    check_args(command, args, options)


_ROLE_ONLY = ("name", "description", "exact")


PICK_LOCATOR_TRACES = "window.playwright, __pw_* globals, an <x-pw-glass> loop"


def _check_combinations(command: str, args: list, options: dict) -> None:
    if command == "pick-locator" and not options.get("cancel") and not options.get("detectable"):
        raise ValueProblem(
            f"pick-locator installs Playwright's recorder in every page of this session ({PICK_LOCATOR_TRACES}) "
            "until the session closes",
            "help: patchright-cli pick-locator --detectable",
        )
    if command == "find":
        if "role" in options:
            stray = [repr(a) for a in args] + [f"--{f}" for f in ("regex", "all") if f in options]
            if stray:
                raise ValueProblem(
                    f"find --role takes no <text>, --regex or --all; got {', '.join(stray)}",
                    "help: filter with --name/--description, e.g. find --role=button --name=Submit",
                    "help: or run a separate text find, e.g. find Submit",
                )
        else:
            stray = [f"--{f}" for f in _ROLE_ONLY if f in options]
            if stray:
                raise ValueProblem(
                    f"{', '.join(stray)} only works with --role=<role>",
                    "help: e.g. find --role=button --name=Submit",
                )
            if isinstance(options.get("regex"), str) and args:
                raise ValueProblem(
                    "find got a pattern twice: --regex=<pattern> and <text>",
                    "help: pass one, e.g. find --regex='^Sub' or find '^Sub' --regex",
                )
    elif command == "html" and args and "selector" in options:
        raise ValueProblem(
            "html takes either a ref or --selector=<css>, not both",
            "help: e.g. html e5, or html --selector='#main'",
        )
    elif command == "route" and "content-type" in options:
        given = options.get("header") or []
        names = [str(h).partition(":")[0].strip().lower() for h in (given if isinstance(given, list) else [given])]
        if "content-type" in names:
            raise ValueProblem(
                "route got the Content-Type twice: --content-type and --header=Content-Type:...",
                "help: pass one, e.g. route '**/api' --content-type=application/json",
            )
    elif command == "wait" and args and "url" in options:
        raise ValueProblem(
            "wait takes either a duration in ms or --url=<pattern>, not both",
            "help: e.g. wait 1000, or wait --url='**/dashboard'",
        )
