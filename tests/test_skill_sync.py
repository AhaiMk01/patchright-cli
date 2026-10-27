"""The generated command block in SKILL.md matches COMMAND_SPECS."""

import importlib.util
from pathlib import Path

import pytest

from patchright_cli import cli

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("gen_skill", ROOT / "scripts" / "gen_skill.py")
gen_skill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen_skill)


def test_committed_block_is_current(capsys):
    assert gen_skill.main(["--check"]) == 0, "run `python scripts/gen_skill.py` and commit SKILL.md"


def test_block_lists_every_command_once():
    block = gen_skill.render_block()
    lines = [line for line in block.splitlines() if line and not line.startswith(("#", "```", "<!--"))]
    names = [line.split()[0] for line in lines]
    assert sorted(names) == sorted(cli.COMMAND_SPECS)
    for line in lines:
        name = line.split()[0]
        assert line.endswith(f"# {cli.COMMAND_SPECS[name]['summary']}")
        for flag in cli.COMMAND_SPECS[name].get("flags", {}):
            assert f"[--{flag}]" in line


def test_categories_cover_every_command():
    listed = [name for _, names in cli.COMMAND_CATEGORIES for name in names]
    assert sorted(listed) == sorted(cli.COMMAND_SPECS)


def test_check_flags_a_stale_block_and_sync_fixes_it(tmp_path, capsys):
    skill = tmp_path / "SKILL.md"
    skill.write_bytes(f"intro\r\n{gen_skill.BEGIN}\r\nold\r\n{gen_skill.END}\r\noutro\r\n".encode())
    assert gen_skill.main(["--check", "--path", str(skill)]) == 1
    assert "stale" in capsys.readouterr().out
    assert gen_skill.main(["--path", str(skill)]) == 0
    text = skill.read_bytes().decode("utf-8")
    assert text.startswith("intro\r\n") and text.endswith("outro\r\n")
    assert "\n" not in text.replace("\r\n", "")
    assert gen_skill.main(["--check", "--path", str(skill)]) == 0


def test_missing_markers_fail(tmp_path):
    skill = tmp_path / "SKILL.md"
    skill.write_text("no markers here\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        gen_skill.main(["--path", str(skill)])


def test_bundled_skill_is_what_install_copies():
    """The stale-install warning compares against this same file."""
    assert cli._get_bundled_skills_dir() is not None
    bundled = cli._read_skill_text(cli._get_bundled_skills_dir() / "SKILL.md")
    assert gen_skill.BEGIN in bundled and gen_skill.END in bundled
