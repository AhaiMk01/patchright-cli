"""Public daemon discovery stays local and never exposes credentials."""

import json
import sys

import pytest

from patchright_cli import __version__, cli, daemon


@pytest.mark.parametrize(
    "flags, port", [([], daemon.DEFAULT_PORT), (["--port=9456"], 9456), (["--port", "9457"], 9457)]
)
@pytest.mark.parametrize("before", [True, False])
def test_daemon_info(flags, port, before, tmp_path, monkeypatch, capsys):
    token_dir = tmp_path / "not-created"
    monkeypatch.setattr(daemon, "TOKEN_DIR", token_dir)

    def forbidden(*args, **kwargs):
        pytest.fail("discovery must not start/connect to a daemon, read a token/config, or check updates")

    for name in (
        "ensure_daemon_running",
        "_send_command",
        "read_daemon_token",
        "_load_config",
        "_warn_stale_skills",
        "_warn_outdated_version",
    ):
        monkeypatch.setattr(cli, name, forbidden)
    command = ["daemon-info", "--json"]
    monkeypatch.setattr(sys, "argv", ["patchright-cli"] + (flags + command if before else command + flags))
    cli.main()
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "version": __version__,
        "protocol": 1,
        "port": port,
        "token_path": str(daemon.token_path(port).resolve()),
    }
    assert not token_dir.exists()


def test_daemon_info_does_not_print_existing_token(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(daemon, "TOKEN_DIR", tmp_path)
    secret = "private-test-credential"
    daemon.token_path(9321).write_text(secret)
    monkeypatch.setattr(sys, "argv", ["patchright-cli", "daemon-info", "--json"])
    cli.main()
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err
    assert daemon.token_path(9321).read_text() == secret
