"""The shipped skill package must stay consistent with the tool."""

import json
from pathlib import Path

import pytest

from patchright_cli import __version__

REPO = Path(__file__).resolve().parent.parent
MARKETPLACE = REPO / ".claude-plugin" / "marketplace.json"
SKILL = REPO / "skills" / "patchright-cli" / "SKILL.md"


def test_marketplace_version_matches_the_package():
    manifest = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    assert manifest["metadata"]["version"] == __version__


def test_marketplace_points_at_a_skill_that_exists():
    manifest = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    for plugin in manifest["plugins"]:
        for rel in plugin["skills"]:
            assert (REPO / rel).joinpath("SKILL.md").is_file(), rel


def test_skill_frontmatter_has_the_fields_the_installer_needs():
    # `npx skills add` discovers a skill by its name/description frontmatter.
    lines = SKILL.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "---"
    end = lines.index("---", 1)
    keys = [line.split(":", 1)[0] for line in lines[1:end] if ":" in line]
    assert "name" in keys
    assert "description" in keys


@pytest.mark.parametrize("ref", ["references/snapshot-refs.md", "references/video-recording.md"])
def test_skill_references_ship_with_the_skill(ref):
    assert (SKILL.parent / ref).is_file()
