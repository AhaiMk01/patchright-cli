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
