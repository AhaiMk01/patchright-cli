"""Tests for `install --hooks` (session-start hook integration)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from patchright_cli import agent_hooks
from patchright_cli.agent_hooks import (
    apply_hooks,
    enable_codex_hooks_feature,
    format_results,
    hook_command,
    is_our_command,
)

CMD = "patchright-cli"


def _statuses(results):
    return {r.app: r.status for r in results}


@pytest.fixture
def home(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".config" / "opencode").mkdir(parents=True)
    return tmp_path


def _session_start(path: Path) -> list:
    return json.loads(path.read_text(encoding="utf-8"))["hooks"]["SessionStart"]


# ---------------------------------------------------------------------------
# Hook command
# ---------------------------------------------------------------------------


def test_hook_command_uses_path_name_when_it_resolves_to_us(tmp_path):
    exe = tmp_path / "patchright-cli.exe"
    exe.write_text("")
    assert hook_command(executable=exe, which=str(exe)) == "patchright-cli"


def test_hook_command_uses_absolute_path_when_path_points_elsewhere(tmp_path):
    exe = tmp_path / "mine" / "patchright-cli"
    other = tmp_path / "other" / "patchright-cli"
    for p in (exe, other):
        p.parent.mkdir()
        p.write_text("")
    assert hook_command(executable=exe, which=str(other)) == exe.as_posix()
    assert hook_command(executable=exe, which=None) == exe.as_posix()


def test_hook_command_quotes_paths_with_spaces(tmp_path):
    exe = tmp_path / "with space" / "patchright-cli.cmd"
    exe.parent.mkdir()
    exe.write_text("")
    assert hook_command(executable=exe, which=None) == f'"{exe.as_posix()}"'


def test_current_executable_finds_console_script_from_argv(tmp_path, monkeypatch):
    exe = tmp_path / "patchright-cli.exe"
    exe.write_text("")
    # Windows launchers may report argv[0] without the .exe suffix.
    monkeypatch.setattr(sys, "argv", [str(tmp_path / "patchright-cli")])
    assert agent_hooks._current_executable() == exe.resolve()


@pytest.mark.parametrize(
    ("command", "ours"),
    [
        ("patchright-cli", True),
        ("/usr/local/bin/patchright-cli", True),
        ('"C:/Program Files/py/Scripts/patchright-cli.exe"', True),
        ("C:\\py\\Scripts\\patchright-cli.cmd", True),
        ('"C:/py/python.exe" -m patchright_cli', True),
        ("patchright-cli list", False),
        ("other-tool", False),
        (None, False),
    ],
)
def test_is_our_command(command, ours):
    assert is_our_command(command) is ours


# ---------------------------------------------------------------------------
# Install / re-install / repair / uninstall
# ---------------------------------------------------------------------------


def test_install_writes_all_three_apps(home):
    results = apply_hooks(home=home, command=CMD)
    assert _statuses(results) == {"claude-code": "installed", "codex": "installed", "opencode": "installed"}

    assert _session_start(home / ".claude" / "settings.json") == [{"hooks": [{"type": "command", "command": CMD}]}]
    assert _session_start(home / ".codex" / "hooks.json") == [{"hooks": [{"type": "command", "command": CMD}]}]
    assert (home / ".codex" / "config.toml").read_text() == "[features]\nhooks = true\n"
    plugin = (home / ".config" / "opencode" / "plugins" / "patchright-cli.js").read_text(encoding="utf-8")
    assert plugin.startswith(agent_hooks.PLUGIN_MARKER)
    assert 'const COMMAND = "patchright-cli"' in plugin
    assert "experimental.chat.system.transform" in plugin


def test_reinstall_same_path_is_noop(home):
    apply_hooks(home=home, command=CMD)
    before = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
    results = apply_hooks(home=home, command=CMD)
    assert set(_statuses(results).values()) == {"no-op"}
    assert {p: p.read_bytes() for p in home.rglob("*") if p.is_file()} == before
    assert all(line.endswith("(no-op)") for line in format_results(results, CMD)[1:])


def test_path_repair_updates_existing_entry(home):
    apply_hooks(home=home, command="/old/bin/patchright-cli")
    results = apply_hooks(home=home, command=CMD)
    assert _statuses(results) == {"claude-code": "updated", "codex": "updated", "opencode": "updated"}
    assert _session_start(home / ".claude" / "settings.json") == [{"hooks": [{"type": "command", "command": CMD}]}]
    plugin = (home / ".config" / "opencode" / "plugins" / "patchright-cli.js").read_text(encoding="utf-8")
    assert "/old/bin" not in plugin


def test_path_repair_collapses_duplicate_entries(home):
    settings = home / ".claude" / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {"hooks": [{"type": "command", "command": "/a/patchright-cli"}]},
                        {"hooks": [{"type": "command", "command": "/b/patchright-cli"}]},
                    ]
                }
            }
        )
    )
    apply_hooks(home=home, command=CMD)
    assert _session_start(settings) == [{"hooks": [{"type": "command", "command": CMD}]}]


def test_install_preserves_unrelated_settings_and_hooks(home):
    settings = home / ".claude" / "settings.json"
    other_start = {"matcher": "startup", "hooks": [{"type": "command", "command": "other-tool status"}]}
    original = {
        "model": "opus",
        "hooks": {
            "SessionStart": [other_start],
            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "guard"}]}],
        },
    }
    settings.write_text(json.dumps(original))

    apply_hooks(home=home, command=CMD)
    data = json.loads(settings.read_text())
    assert data["model"] == "opus"
    assert data["hooks"]["PreToolUse"] == original["hooks"]["PreToolUse"]
    assert data["hooks"]["SessionStart"] == [other_start, {"hooks": [{"type": "command", "command": CMD}]}]

    apply_hooks(home=home, uninstall=True)
    assert json.loads(settings.read_text()) == original


def test_uninstall_removes_only_our_entries(home):
    apply_hooks(home=home, command=CMD)
    results = apply_hooks(home=home, uninstall=True)
    assert _statuses(results) == {"claude-code": "removed", "codex": "removed", "opencode": "removed"}
    assert json.loads((home / ".claude" / "settings.json").read_text()) == {}
    assert json.loads((home / ".codex" / "hooks.json").read_text()) == {}
    assert not (home / ".config" / "opencode" / "plugins" / "patchright-cli.js").exists()
    # The feature flag stays: other Codex hooks may depend on it.
    assert "hooks = true" in (home / ".codex" / "config.toml").read_text()

    again = apply_hooks(home=home, uninstall=True)
    assert set(_statuses(again).values()) == {"no-op"}


def test_uninstall_keeps_other_hooks_in_shared_group(home):
    settings = home / ".claude" / "settings.json"
    group = {"hooks": [{"type": "command", "command": "other-tool"}, {"type": "command", "command": CMD}]}
    settings.write_text(json.dumps({"hooks": {"SessionStart": [group]}}))
    apply_hooks(home=home, uninstall=True)
    assert _session_start(settings) == [{"hooks": [{"type": "command", "command": "other-tool"}]}]


def test_only_apps_with_config_dir_get_hooks(tmp_path):
    (tmp_path / ".codex").mkdir()
    results = apply_hooks(home=tmp_path, command=CMD)
    assert _statuses(results) == {"claude-code": "skipped", "codex": "installed", "opencode": "skipped"}
    assert not (tmp_path / ".claude").exists()
    assert not (tmp_path / ".config").exists()
    lines = format_results(results, CMD)
    assert lines[0] == "hook: patchright-cli"
    assert "claude-code: skipped (~/.claude not found)" in lines


def test_invalid_json_is_reported_and_left_untouched(home):
    settings = home / ".claude" / "settings.json"
    settings.write_text("{not json")
    results = apply_hooks(home=home, command=CMD)
    statuses = _statuses(results)
    assert statuses["claude-code"] == "error"
    assert statuses["codex"] == "installed"
    assert settings.read_text() == "{not json"
    error_line = next(line for line in format_results(results, CMD) if line.startswith("error:"))
    assert "~/.claude/settings.json is not valid JSON" in error_line


def test_foreign_opencode_plugin_is_not_overwritten(home):
    plugin = home / ".config" / "opencode" / "plugins" / "patchright-cli.js"
    plugin.parent.mkdir()
    plugin.write_text("// my own plugin\n")
    results = apply_hooks(home=home, command=CMD)
    assert _statuses(results)["opencode"] == "error"
    assert plugin.read_text() == "// my own plugin\n"


# ---------------------------------------------------------------------------
# Codex config.toml feature flag
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("", "[features]\nhooks = true\n"),
        ('model = "o3"\n', 'model = "o3"\n\n[features]\nhooks = true\n'),
        ('model = "o3"', 'model = "o3"\n\n[features]\nhooks = true\n'),
        ("[features]\nweb_search = true\n", "[features]\nhooks = true\nweb_search = true\n"),
        ("[features]\nhooks = false # off\n[mcp]\nx = 1\n", "[features]\nhooks = true\n[mcp]\nx = 1\n"),
        ("features.hooks = false\n", "features.hooks = true\n"),
        (
            "# top\n[mcp_servers.a]\nhooks = false\n",
            "# top\n[mcp_servers.a]\nhooks = false\n\n[features]\nhooks = true\n",
        ),
    ],
)
def test_enable_codex_hooks_feature_edits(before, after):
    text, changed = enable_codex_hooks_feature(before)
    assert changed
    assert text == after


@pytest.mark.parametrize(
    "text",
    ["[features]\nhooks = true\n", "[features]\n  hooks   =   true   # yes\n", "features.hooks = true\n"],
)
def test_enable_codex_hooks_feature_noop(text):
    assert enable_codex_hooks_feature(text) == (text, False)


def test_codex_hook_present_but_feature_off_counts_as_update(home):
    apply_hooks(home=home, command=CMD)
    (home / ".codex" / "config.toml").write_text("[features]\nhooks = false\n")
    results = apply_hooks(home=home, command=CMD)
    assert _statuses(results)["codex"] == "updated"
    assert (home / ".codex" / "config.toml").read_text() == "[features]\nhooks = true\n"


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def _run_install(monkeypatch, capsys, *args):
    from patchright_cli import cli

    monkeypatch.setattr(agent_hooks, "hook_command", lambda: CMD)
    code = 0
    try:
        cli._handle_install(list(args))
    except SystemExit as e:
        code = e.code or 0
    return code, capsys.readouterr().out


def test_cli_install_hooks_and_noop(home, monkeypatch, capsys):
    code, out = _run_install(monkeypatch, capsys, "--hooks", f"--home={home}")
    assert code == 0
    assert out.splitlines() == [
        "hook: patchright-cli",
        "claude-code: installed ~/.claude/settings.json",
        "codex: installed ~/.codex/hooks.json",
        "opencode: installed ~/.config/opencode/plugins/patchright-cli.js",
    ]
    code, out = _run_install(monkeypatch, capsys, "--hooks", "--home", str(home))
    assert code == 0
    assert "claude-code: ~/.claude/settings.json (no-op)" in out


def test_cli_install_hooks_uninstall(home, monkeypatch, capsys):
    _run_install(monkeypatch, capsys, "--hooks", f"--home={home}")
    code, out = _run_install(monkeypatch, capsys, "--hooks", "--uninstall", f"--home={home}")
    assert code == 0
    assert "claude-code: removed ~/.claude/settings.json" in out
    assert not out.startswith("hook:")


def test_cli_install_hooks_error_exits_1(home, monkeypatch, capsys):
    (home / ".claude" / "settings.json").write_text("[")
    code, out = _run_install(monkeypatch, capsys, "--hooks", f"--home={home}")
    assert code == 1
    assert "error: claude-code: ~/.claude/settings.json is not valid JSON" in out


def test_cli_install_rejects_unknown_flag(home, monkeypatch, capsys):
    code, out = _run_install(monkeypatch, capsys, "--hook", f"--home={home}")
    assert code == 2
    assert "error: unknown flag --hook for `install`" in out
    assert "--hooks" in out
    assert not (home / ".claude" / "settings.json").exists()


def test_cli_install_uninstall_requires_hooks(monkeypatch, capsys):
    code, out = _run_install(monkeypatch, capsys, "--uninstall")
    assert code == 2
    assert "error: --uninstall only applies to --hooks" in out


def test_cli_install_skills_honours_home(tmp_path, monkeypatch, capsys):
    (tmp_path / ".claude").mkdir()
    code, out = _run_install(monkeypatch, capsys, "--skills", f"--home={tmp_path}")
    assert code == 0
    assert (tmp_path / ".claude" / "skills" / "patchright-cli" / "SKILL.md").exists()
