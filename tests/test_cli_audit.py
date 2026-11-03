"""AXI audit: extra positionals, flag values and daemon replies fail loud, before the daemon."""

import re
import sys

import pytest

from patchright_cli import cli

# Commands main() handles before positional validation.
_SKIP = {"install", "mcp", "daemon-info"}


def _run(argv, monkeypatch, capsys, reply=None):
    """Run main(); return (exit code, stdout lines, what reached the daemon or None)."""
    sent = {}

    def fake_send(command, args, options, port=0):
        sent.update(command=command, args=args, options=options)
        return reply if reply is not None else {"success": True, "output": ""}

    monkeypatch.setattr(cli, "ensure_daemon_running", lambda *a, **k: False)
    monkeypatch.setattr(cli, "_send_command", fake_send)
    monkeypatch.setattr(cli, "_kill_all", lambda *a, **k: fake_send("kill-all", [], {}))
    monkeypatch.setattr(cli, "_warn_stale_skills", lambda: None)
    monkeypatch.setattr(cli, "_warn_outdated_version", lambda: None)
    monkeypatch.setattr(cli, "_load_config", lambda p: {})
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys, "argv", ["patchright-cli", *argv])
    code = 0
    try:
        cli.main()
    except SystemExit as exc:
        code = exc.code or 0
    out, _ = capsys.readouterr()
    return code, out.splitlines(), (sent or None)


def _arity(usage: str) -> int:
    return sum(1 for token in usage.split()[1:] if token[:1] in "<[")


# -- B. a positional too many exits 2 -----------------------------------------


@pytest.mark.parametrize("name", [n for n in cli.COMMAND_SPECS if n not in _SKIP])
def test_every_command_rejects_one_positional_too_many(name, monkeypatch, capsys):
    spec = cli.COMMAND_SPECS[name]
    most = cli._max_args(spec)
    assert most == spec.get("max_args", _arity(spec["usage"]))
    args = ["a1", "a2", "a3", "a4"][:most] + ["extra"]
    code, lines, sent = _run([name, *args], monkeypatch, capsys)
    assert code == 2, lines
    assert sent is None
    assert lines[0] == f"error: `{name}` takes at most {most} argument(s), got {most + 1}: extra"
    assert lines[-1] == f"help: patchright-cli {spec['usage']}"


@pytest.mark.parametrize(
    "argv",
    [
        ["video-start", "foo.webm"],
        ["html", "e1", "e2"],
        ["pick-locator", "extra"],
        ["passkey-list", "extra"],
        ["debugger-status", "extra"],
        ["reload", "extra"],
        ["type", "hello", "world"],
    ],
)
def test_measured_extra_positionals(argv, monkeypatch, capsys):
    code, lines, sent = _run(argv, monkeypatch, capsys)
    assert code == 2 and sent is None
    assert re.match(r"error: `[a-z-]+` takes at most \d argument\(s\), got \d: ", lines[0])


def test_video_start_path_points_at_video_stop(monkeypatch, capsys):
    _, lines, _ = _run(["video-start", "foo.webm"], monkeypatch, capsys)
    assert any("video-stop --filename=" in line for line in lines)


def test_the_most_positionals_still_pass(monkeypatch, capsys):
    code, _, sent = _run(["click", "e1", "right"], monkeypatch, capsys)
    assert code == 0 and sent["args"] == ["e1", "right"]


def test_text_after_double_dash_is_still_counted(monkeypatch, capsys):
    code, lines, _ = _run(["type", "--", "-a", "-b"], monkeypatch, capsys)
    assert code == 2 and "got 2: -b" in lines[0]


# -- C. bad flag and positional values exit 2 before the daemon ---------------

from patchright_cli import values  # noqa: E402

_TYPED_FLAGS = [(c, f, k) for c, kinds in values.FLAG_KINDS.items() for f, k in kinds.items()]
_GLOBAL_TYPED = list(values.GLOBAL_FLAG_KINDS.items())


def _required_args(name):
    spec = cli.COMMAND_SPECS[name]
    return ["e1"] * cli._min_args(spec)


def test_every_typed_flag_is_a_declared_flag():
    for command, flag, _ in _TYPED_FLAGS:
        assert flag in cli.COMMAND_SPECS[command].get("flags", {}), (command, flag)
    for flag, _ in _GLOBAL_TYPED:
        assert flag in cli.GLOBAL_FLAGS, flag


def test_every_numeric_or_enum_flag_has_a_kind():
    """A flag whose default is a number, or whose description lists choices, must be typed."""
    numeric_default = re.compile(r"\(default: -?\d")
    for command, spec in cli.COMMAND_SPECS.items():
        for flag, desc in spec.get("flags", {}).items():
            looks_typed = numeric_default.search(desc) or re.search(r"\w\|\w", desc) or " in ms" in desc
            if looks_typed and command != "open" and flag != "block-networks":
                assert flag in values.FLAG_KINDS.get(command, {}), (command, flag, desc)


def test_every_positional_kind_matches_the_usage():
    for command, entries in values.ARG_KINDS.items():
        names = [t for t in cli.COMMAND_SPECS[command]["usage"].split()[1:] if t[:1] in "<["]
        assert len(entries) <= len(names), command
        for entry, name in zip(entries, names, strict=False):
            if entry is not None:
                assert entry[0][0] == name[0], (command, entry[0], name)


@pytest.mark.parametrize(("command", "flag", "kind"), _TYPED_FLAGS, ids=lambda v: str(v)[:20])
def test_every_typed_flag_rejects_a_bad_value(command, flag, kind, monkeypatch, capsys):
    bad = f"--{flag}" if isinstance(kind, values.Text) else f"--{flag}=zz-bogus"
    code, lines, sent = _run([command, *_required_args(command), bad], monkeypatch, capsys)
    assert code == 2, lines
    assert sent is None
    assert lines[0] in (f"error: invalid --{flag} value `zz-bogus`", f"error: --{flag} needs a value")
    assert lines[1].startswith(f"help: --{flag} takes ")


@pytest.mark.parametrize(("flag", "kind"), _GLOBAL_TYPED)
def test_global_typed_flags_reject_a_bad_value(flag, kind, monkeypatch, capsys):
    code, lines, sent = _run(["snapshot", f"--{flag}=zz-bogus"], monkeypatch, capsys)
    assert (code, sent) == (2, None)
    assert lines[0] == f"error: invalid --{flag} value `zz-bogus`"


@pytest.mark.parametrize("command", list(values.ARG_KINDS))
def test_every_typed_positional_rejects_a_bad_value(command, monkeypatch, capsys):
    entries = values.ARG_KINDS[command]
    args = ["e1" if e is None else str(e[1].example) for e in entries]
    position = next(i for i, e in enumerate(entries) if e is not None)
    args[position] = "zz-bogus"
    code, lines, sent = _run([command, *args], monkeypatch, capsys)
    assert (code, sent) == (2, None), lines
    assert lines[0] == f"error: invalid {entries[position][0]} value `zz-bogus` for `{command}`"
    assert lines[-1] == f"help: patchright-cli {cli.COMMAND_SPECS[command]['usage']}"
    # The example values in the help line are themselves valid.
    code, _, sent = _run([command, *(["e1" if e is None else str(e[1].example) for e in entries])], monkeypatch, capsys)
    assert code == 0 and sent is not None


@pytest.mark.parametrize(("command", "flag", "kind"), _TYPED_FLAGS, ids=lambda v: str(v)[:20])
def test_every_typed_flag_example_is_accepted(command, flag, kind, monkeypatch, capsys):
    code, lines, _ = _run([command, *_required_args(command), f"--{flag}={kind.example}"], monkeypatch, capsys)
    rejected = (f"error: invalid --{flag} value", f"error: --{flag} needs a value")
    assert not (code == 2 and lines[0].startswith(rejected)), lines


@pytest.mark.parametrize(
    "argv",
    [
        ["video-start", "--fps=abc"],
        ["html", "--max-chars=x"],
        ["click", "e1", "--scroll=bogus"],
        ["pick-locator", "--wait=abc"],
        ["show", "--annotate", "--wait=abc"],
        ["passkey-add", "x.com", "--sign-count=x"],
        ["find", "--role=button", "stray"],
        ["find", "Sign", "--name=x"],
        ["pick-locator", "--wait", "5"],
    ],
)
def test_measured_bad_values(argv, monkeypatch, capsys):
    code, lines, sent = _run(argv, monkeypatch, capsys)
    assert (code, sent) == (2, None), lines
    assert lines[0].startswith("error: ") and lines[1].startswith("help: ")


# -- D. every failed daemon reply prints one way -------------------------------


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("Error: stale element", ["error: stale element"]),
        ("Invalid --fps value: 'abc'.", ["error: invalid --fps value: 'abc'"]),
        (
            "Session 'w' is not open. Run 'open' first.",
            ["error: session 'w' is not open", "help: run 'open' first"],
        ),
        (
            "No page open. Run 'tab-new' to create one, or 'close' and 'open' again.",
            ["error: no page open", "help: run 'tab-new' to create one, or 'close' and 'open' again"],
        ),
        (
            "Usage: network-state-set <online|offline>",
            [
                "error: `cmd` got missing or invalid arguments",
                "help: patchright-cli network-state-set <online|offline>",
            ],
        ),
        (
            "find requires a search term. Usage: find <text> [--regex]",
            ["error: find requires a search term", "help: patchright-cli find <text> [--regex]"],
        ),
        (
            "Error: element is outside of the viewport\nhelp: drop --scroll=none or scroll first",
            ["error: element is outside of the viewport", "help: drop --scroll=none or scroll first"],
        ),
        (
            'Error: Timeout\nOptions:\n- option "Red"\nhelp: pick one',
            ["error: timeout", "Options:", '- option "Red"', "help: pick one"],
        ),
        ("WebMCP is not enabled in this browser.", ["error: WebMCP is not enabled in this browser"]),
        ("", ["error: `cmd` failed"]),
    ],
)
def test_failure_lines_normalize_every_style(output, expected):
    message, details, helps = cli._failure_lines("cmd", output)
    assert [f"error: {message}", *details, *helps] == expected


def test_failed_reply_prints_error_then_help(monkeypatch, capsys):
    reply = {"success": False, "output": "Session 'w' is not open. Run 'open' first."}
    code, lines, _ = _run(["-s=w", "snapshot"], monkeypatch, capsys, reply=reply)
    assert code == 1
    assert lines == ["error: session 'w' is not open", "help: run 'open' first"]


def test_failed_usage_reply_keeps_exit_2(monkeypatch, capsys):
    reply = {"success": False, "output": 'Error: "zz" is not a ref', "exit_code": 2}
    code, lines, _ = _run(["click", "zz"], monkeypatch, capsys, reply=reply)
    assert code == 2 and lines == ['error: "zz" is not a ref']


def test_failed_json_reply_gains_an_error_field(monkeypatch, capsys):
    import json

    reply = {"success": False, "output": "Session 'w' is not open. Run 'open' first.", "data": []}
    code, lines, _ = _run(["--json", "tab-list"], monkeypatch, capsys, reply=reply)
    payload = json.loads(lines[0])
    assert code == 1
    assert payload["output"] == reply["output"] and payload["data"] == []
    assert payload["error"] == "session 'w' is not open"
    assert payload["help"] == ["run 'open' first"]


def test_failed_json_reply_keeps_the_error_code(monkeypatch, capsys):
    import json

    reply = {"success": False, "output": "Session 'w' is not open. Run 'open' first.", "code": "session_not_open"}
    _, lines, _ = _run(["--json", "-s=w", "snapshot"], monkeypatch, capsys, reply=reply)
    assert json.loads(lines[0])["code"] == "session_not_open"


def test_failed_toon_reply_prints_the_error_format(monkeypatch, capsys):
    reply = {"success": False, "output": "Error: boom", "data": []}
    code, lines, _ = _run(["--toon", "tab-list"], monkeypatch, capsys, reply=reply)
    assert code == 1 and lines == ["error: boom"]


def test_console_level_must_be_one_the_capture_reports(monkeypatch, capsys):
    code, lines, sent = _run(["console", "bogus"], monkeypatch, capsys)
    assert (code, sent) == (2, None), lines
    assert lines[0] == "error: invalid [level] value `bogus` for `console`"
    assert lines[1].startswith("help: [level] takes one of: log, info, warning, warn, error, debug")
    for level in ("error", "warning", "warn", "log", "debug", "info"):
        code, lines, sent = _run(["console", level], monkeypatch, capsys)
        assert code == 0 and sent["args"] == [level], lines
