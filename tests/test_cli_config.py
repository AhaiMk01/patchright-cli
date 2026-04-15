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
