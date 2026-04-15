import json
import os
import tempfile

from patchright_cli.cli import _load_config


def test_load_config_defaults():
    result = _load_config(None)
    assert result == {}


def test_load_config_from_path():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"headless": True, "proxy": "http://proxy"}, f)
        path = f.name
    try:
        result = _load_config(path)
        assert result["headless"] is True
        assert result["proxy"] == "http://proxy"
    finally:
        os.unlink(path)


def test_load_config_from_default_location():
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, ".patchright-cli", "config.json")
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w") as f:
            json.dump({"persistent": True}, f)
        old_cwd = os.getcwd()
        os.chdir(tmpdir)
        try:
            result = _load_config(None)
            assert result["persistent"] is True
        finally:
            os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# New tests for v0.4.1 features
# ---------------------------------------------------------------------------

from patchright_cli.cli import _merge_config_with_options


def test_merge_config_with_options():
    config = {"headless": True, "proxy": "http://proxy", "locale": "en-US"}
    options = {"proxy": "http://other", "device": "iPhone 15"}
    result = _merge_config_with_options(config, options)
    assert result["headless"] is True  # from config
    assert result["proxy"] == "http://other"  # CLI overrides
    assert result["locale"] == "en-US"  # from config
    assert result["device"] == "iPhone 15"  # new from CLI


def test_merge_config_none_values_preserved():
    config = {"proxy": "http://proxy"}
    options = {"proxy": None}
    result = _merge_config_with_options(config, options)
    assert result["proxy"] == "http://proxy"  # None doesn't override


def test_session_env_var():
    """PATCHRIGHT_CLI_SESSION env var should be used as session default."""
    old = os.environ.get("PATCHRIGHT_CLI_SESSION")
    os.environ["PATCHRIGHT_CLI_SESSION"] = "test-session"
    try:
        assert os.environ.get("PATCHRIGHT_CLI_SESSION", "default") == "test-session"
    finally:
        if old is None:
            os.environ.pop("PATCHRIGHT_CLI_SESSION", None)
        else:
            os.environ["PATCHRIGHT_CLI_SESSION"] = old


def test_raw_flag_strips_page_info():
    """--raw flag should strip ### Page and ### Snapshot decorations."""
    output = (
        "### Page\n"
        "- Page URL: https://example.com/\n"
        "- Page Title: Example\n"
        "### Snapshot\n"
        "[Snapshot](.patchright-cli/page-123.yml)"
    )
    from patchright_cli.cli import _strip_raw_output

    result = _strip_raw_output(output)
    assert result == ""


def test_raw_flag_preserves_result_content():
    output = '{"title": "Example"}'
    from patchright_cli.cli import _strip_raw_output

    result = _strip_raw_output(output)
    assert result == '{"title": "Example"}'


def test_raw_flag_strips_decorations_from_mixed():
    output = (
        "### Page\n"
        "- Page URL: https://example.com/\n"
        "- Page Title: Example\n"
        "some actual result\n"
        "### Snapshot\n"
        "[Snapshot](.patchright-cli/page-123.yml)"
    )
    from patchright_cli.cli import _strip_raw_output

    result = _strip_raw_output(output)
    assert result == "some actual result"


def test_raw_flag_whitespace_only():
    from patchright_cli.cli import _strip_raw_output

    assert _strip_raw_output("   \n\n  ") == ""


def test_raw_flag_empty_string():
    from patchright_cli.cli import _strip_raw_output

    assert _strip_raw_output("") == ""


def test_raw_flag_snapshot_without_link():
    """### Snapshot header with no [Snapshot] link following."""
    from patchright_cli.cli import _strip_raw_output

    output = "### Snapshot\nsome result"
    result = _strip_raw_output(output)
    assert result == "some result"


def test_raw_flag_extra_metadata_lines():
    """Future metadata lines (starting with '- ') after ### Page should be stripped."""
    from patchright_cli.cli import _strip_raw_output

    output = "### Page\n- Page URL: https://example.com/\n- Page Title: Example\n- Page Status: 200\nactual result"
    result = _strip_raw_output(output)
    assert result == "actual result"


def test_raw_flag_unknown_section_header():
    """Unknown ### headers should also be stripped."""
    from patchright_cli.cli import _strip_raw_output

    output = "### Console\n- warning: something\nmy data"
    result = _strip_raw_output(output)
    assert result == "my data"


# ---------------------------------------------------------------------------
# install --skills tests
# ---------------------------------------------------------------------------


def test_install_skills_detects_agents(tmp_path):
    """install --skills should detect agent directories and install skills."""
    from patchright_cli.cli import _detect_agent_dirs

    # Create a fake .claude directory
    (tmp_path / ".claude").mkdir()

    agents = _detect_agent_dirs(home=tmp_path)
    assert len(agents) == 1
    assert agents[0][0] == "Claude Code"
    assert agents[0][1] == tmp_path / ".claude"


def test_install_skills_detects_multiple_agents(tmp_path):
    """Should detect all agent directories that exist."""
    from patchright_cli.cli import _detect_agent_dirs

    (tmp_path / ".claude").mkdir()
    (tmp_path / ".gemini").mkdir()

    agents = _detect_agent_dirs(home=tmp_path)
    assert len(agents) == 2
    names = [a[0] for a in agents]
    assert "Claude Code" in names
    assert "Gemini CLI" in names


def test_install_skills_no_agents(tmp_path):
    """Should return empty list when no agent dirs exist."""
    from patchright_cli.cli import _detect_agent_dirs

    agents = _detect_agent_dirs(home=tmp_path)
    assert agents == []


def test_install_skills_copies_files(tmp_path):
    """install --skills should copy SKILL.md and references to target dir."""
    from patchright_cli.cli import _install_skills_to_dir

    target = tmp_path / "skills" / "patchright-cli"
    _install_skills_to_dir(target)

    assert (target / "SKILL.md").exists()
    assert (target / "references").is_dir()
    assert (target / "references" / "snapshot-refs.md").exists()
