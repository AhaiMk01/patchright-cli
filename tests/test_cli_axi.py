"""AXI argument layer: strict flags, per-command help, usage errors, version fast path."""

import subprocess
import sys
import time

import pytest

from patchright_cli import __version__, cli, mcp_server


def _run(argv, monkeypatch, capsys, stdin_tty=True):
    """Run main() with the daemon side forbidden; return (exit code, stdout, stderr, sent)."""
    sent = {}

    def no_daemon(*a, **k):
        raise AssertionError("contacted the daemon")

    def fake_send(command, args, options, port=0):
        sent.update(command=command, args=args, options=options)
        return {"success": True, "output": ""}

    monkeypatch.setattr(cli, "ensure_daemon_running", lambda *a, **k: False)
    monkeypatch.setattr(cli, "_send_command", fake_send)
    monkeypatch.setattr(cli, "_warn_stale_skills", lambda: None)
    monkeypatch.setattr(cli, "_warn_outdated_version", lambda: None)
    monkeypatch.setattr(cli, "_load_config", lambda p: {})
    monkeypatch.setattr(sys.stdin, "isatty", lambda: stdin_tty, raising=False)
    monkeypatch.setattr(sys, "argv", ["patchright-cli", *argv])
    code = 0
    try:
        cli.main()
    except SystemExit as exc:
        code = exc.code or 0
    out, err = capsys.readouterr()
    return code, out, err, sent


def _forbid_daemon(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("contacted the daemon")

    monkeypatch.setattr(cli, "ensure_daemon_running", boom)
    monkeypatch.setattr(cli, "_send_command", boom)


# -- 1. strict per-command flags ---------------------------------------------


def test_unknown_flag_is_rejected_before_the_daemon(monkeypatch, capsys):
    _forbid_daemon(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["patchright-cli", "snapshot", "--dept=3"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    out, err = capsys.readouterr()
    lines = out.splitlines()
    assert lines[0] == "error: unknown flag --dept for `snapshot`"
    assert lines[1].startswith("help: valid flags for `snapshot`: --selector, --filename, --depth")
    assert lines[1].endswith("(--help always allowed)")
    assert "usage: patchright-cli snapshot" in out
    assert err == ""


def test_unknown_flag_in_front_of_the_command_belongs_to_it(monkeypatch, capsys):
    code, out, _, _ = _run(["--dept=3", "snapshot"], monkeypatch, capsys)
    assert code == 2
    assert out.startswith("error: unknown flag --dept for `snapshot`")


def test_command_without_flags_says_none(monkeypatch, capsys):
    code, out, _, _ = _run(["url", "--full"], monkeypatch, capsys)
    assert code == 2
    assert "help: valid flags for `url`: none (--help always allowed)" in out


def test_declared_flags_reach_the_daemon(monkeypatch, capsys):
    code, _, _, sent = _run(["snapshot", "--depth=3", "--boxes", "-i"], monkeypatch, capsys)
    assert code == 0
    assert sent["options"]["depth"] == "3"
    assert sent["options"]["boxes"] is True
    assert sent["options"]["interactive"] is True


def test_interactive_only_where_relevant(monkeypatch, capsys):
    code, out, _, _ = _run(["click", "e1", "-i"], monkeypatch, capsys)
    assert code == 2
    assert out.startswith("error: unknown flag -i for `click`")


@pytest.mark.parametrize(
    "argv",
    [
        ["url", "--timeout-action=5", "--timeout-navigation=5", "--timeout=60", "--web-only", "--toon"],
        ["--headless", "--mobile", "--locale=en-US", "--port=9421", "title", "--json", "--raw"],
        ["--tab", "t", "-s=w1", "--profile=w1", "--context=new", "--cdp=x", "--show-port=1", "reload"],
    ],
)
def test_global_flags_pass_on_every_command(argv, monkeypatch, capsys):
    code, out, _, _ = _run(argv, monkeypatch, capsys)
    assert code == 0, out


@pytest.mark.parametrize(
    "command, flag, hint",
    [
        ("cookie-set", "--http-only", "--httpOnly"),
        ("cookie-set", "--same_site=Lax", "--sameSite"),
        ("screenshot", "--fullPage", "--full-page"),
        ("route", "--contentType=x", "--content-type"),
        ("wait-for", "--hidden", "--state=hidden"),
    ],
)
def test_renamed_flags_point_at_the_accepted_spelling(command, flag, hint, monkeypatch, capsys):
    args = {"cookie-set": ["a", "b"], "route": ["**"], "wait-for": ["e1"]}.get(command, [])
    code, out, _, _ = _run([command, *args, flag], monkeypatch, capsys)
    assert code == 2
    assert f"help: `{command}` takes {hint}, not --" in out.splitlines()[1]


def test_rename_hint_only_when_valid_for_the_command(monkeypatch, capsys):
    # --httpOnly is cookie-set's flag, not snapshot's.
    code, out, _, _ = _run(["snapshot", "--http-only"], monkeypatch, capsys)
    assert code == 2
    assert out.splitlines()[1].startswith("help: valid flags for `snapshot`")


def test_double_dash_still_ends_validation(monkeypatch, capsys):
    code, _, _, sent = _run(["fill", "e1", "--", "--not-a-flag"], monkeypatch, capsys)
    assert code == 0
    assert sent["args"] == ["e1", "--not-a-flag"]


def test_install_and_mcp_are_not_validated():
    assert cli.COMMAND_SPECS["install"]["validate"] is False
    assert cli.COMMAND_SPECS["mcp"]["validate"] is False
    cli._validate_flags("install", {"hooks": True, "uninstall": True, "home": "/x"})


def test_every_spec_is_complete():
    for name, spec in cli.COMMAND_SPECS.items():
        assert spec["usage"].split()[0] == name, name
        assert spec["summary"], name
        assert 1 <= len(spec["examples"]) <= 3, name
        assert not set(spec.get("flags", {})) & cli.GLOBAL_FLAGS, name


def test_every_mcp_tool_passes_validation(monkeypatch, tmp_path):
    """The MCP server drives the CLI; strict flags must not reject what it sends."""
    from tests.test_mcp import _tool_argv

    monkeypatch.setattr(mcp_server, "UNRESTRICTED_FILESYSTEM", False)
    for tool in mcp_server.TOOL_SPECS:
        if tool == "patchright_install_skills":  # refused outright without unrestricted filesystem
            continue
        argv = _tool_argv(monkeypatch, tmp_path, tool)
        argv = [a for a in argv if not a.startswith(("--port=", "--config="))]
        command = next(a for a in argv if not a.startswith("-"))
        if not cli.COMMAND_SPECS[command].get("validate", True):
            continue
        rest = argv[argv.index(command) + 1 :]
        opts = {}
        for a in rest:
            if a == "--":
                break
            if a.startswith("--"):
                opts[a[2:].split("=", 1)[0]] = True
        cli._validate_flags(command, opts)


# -- 2. per-command --help ---------------------------------------------------


@pytest.mark.parametrize("argv", [["snapshot", "--help"], ["snapshot", "-h"], ["--help", "snapshot"]])
def test_command_help_is_concise(argv, monkeypatch, capsys):
    _forbid_daemon(monkeypatch)
    code, out, _, _ = _run(argv, monkeypatch, capsys)
    assert code == 0
    assert out.startswith("usage: patchright-cli snapshot [ref] [flags]")
    assert "--depth" in out and "(default: unlimited)" in out
    assert "examples:" in out
    assert "Commands:" not in out  # not the full manual


def test_help_wins_over_unknown_flags(monkeypatch, capsys):
    code, out, _, _ = _run(["snapshot", "--dept=3", "--help"], monkeypatch, capsys)
    assert code == 0
    assert out.startswith("usage: patchright-cli snapshot")


def test_bare_help_keeps_the_full_manual(monkeypatch, capsys):
    code, out, _, _ = _run(["--help"], monkeypatch, capsys)
    assert code == 0
    assert "Commands:" in out and "snapshot" in out


# -- 3. usage errors exit 2 without a traceback ------------------------------


@pytest.mark.parametrize(
    "argv, message",
    [
        (["--viewport-size=bad", "open"], "error: invalid --viewport-size value `bad`"),
        (["--viewport-size", "12x", "open"], "error: invalid --viewport-size value `12x`"),
        (["--geolocation=bad", "open"], "error: invalid --geolocation value `bad`"),
        (["--port=abc", "url"], "error: invalid --port value `abc`"),
        (["--port", "70000", "url"], "error: invalid --port value `70000`"),
        (["eval"], "error: `eval` requires a JS expression"),
        (["run-code"], "error: `run-code` requires a JS expression"),
        (["daemon-info"], "error: `daemon-info` takes only --json"),
        (["fill", "e1"], "error: `fill` requires <ref> <value>"),
        (["goto"], "error: `goto` requires <url>"),
    ],
)
def test_usage_errors(argv, message, monkeypatch, capsys):
    _forbid_daemon(monkeypatch)
    code, out, err, _ = _run(argv, monkeypatch, capsys)
    assert code == 2
    assert out.startswith(message)
    assert "help: " in out
    assert "Traceback" not in out + err


def test_valid_emulation_values_still_parse(monkeypatch, capsys):
    code, _, _, sent = _run(["--viewport-size=1280x800", "--geolocation=37.7,-122.4", "open"], monkeypatch, capsys)
    assert code == 0
    assert sent["options"]["viewport"] == {"width": "1280", "height": "800"}
    assert sent["options"]["geolocation"] == {"lat": "37.7", "lon": "-122.4"}


def test_unexpected_exception_is_one_error_line(monkeypatch, capsys):
    def explode(*a, **k):
        raise ValueError("bad response")

    monkeypatch.delenv("PATCHRIGHT_CLI_DEBUG", raising=False)
    monkeypatch.setattr(cli, "_send_command", explode)
    monkeypatch.setattr(cli, "ensure_daemon_running", lambda *a, **k: False)
    monkeypatch.setattr(cli, "_warn_stale_skills", lambda: None)
    monkeypatch.setattr(cli, "_load_config", lambda p: {})
    monkeypatch.setattr(sys, "argv", ["patchright-cli", "url"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    out, err = capsys.readouterr()
    assert out.splitlines()[0] == "error: bad response"
    assert "Traceback" not in out + err


def test_debug_env_puts_the_traceback_on_stderr(monkeypatch, capsys):
    def explode(*a, **k):
        raise ValueError("bad response")

    monkeypatch.setenv("PATCHRIGHT_CLI_DEBUG", "1")
    monkeypatch.setattr(cli, "_send_command", explode)
    monkeypatch.setattr(cli, "ensure_daemon_running", lambda *a, **k: False)
    monkeypatch.setattr(cli, "_warn_stale_skills", lambda: None)
    monkeypatch.setattr(cli, "_load_config", lambda p: {})
    monkeypatch.setattr(sys, "argv", ["patchright-cli", "url"])
    with pytest.raises(SystemExit):
        cli.main()
    out, err = capsys.readouterr()
    assert "Traceback" in err and "Traceback" not in out


# -- 4. CLI errors on stdout -------------------------------------------------


def test_connection_error_goes_to_stdout(monkeypatch, capsys):
    def refuse(*a, **k):
        raise ConnectionError("Connection closed by daemon")

    monkeypatch.setattr(cli, "_send_command", refuse)
    monkeypatch.setattr(cli, "ensure_daemon_running", lambda *a, **k: False)
    monkeypatch.setattr(cli, "_warn_stale_skills", lambda: None)
    monkeypatch.setattr(cli, "_load_config", lambda p: {})
    monkeypatch.setattr(sys, "argv", ["patchright-cli", "--port=9421", "url"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    out, err = capsys.readouterr()
    assert out.startswith("error: cannot reach the daemon on port 9421")
    assert "\nhelp: " in out
    assert err == ""


def test_daemon_start_failure_goes_to_stdout(monkeypatch, capsys):
    def fail(*a, **k):
        raise RuntimeError("Failed to start daemon on port 9421")

    monkeypatch.setattr(cli, "ensure_daemon_running", fail)
    monkeypatch.setattr(cli, "_warn_stale_skills", lambda: None)
    monkeypatch.setattr(cli, "_load_config", lambda p: {})
    monkeypatch.setattr(sys, "argv", ["patchright-cli", "--port=9421", "url"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    out, err = capsys.readouterr()
    assert out.startswith("error: Failed to start daemon on port 9421\nhelp: ")
    assert err == ""


def test_missing_eval_file_goes_to_stdout(monkeypatch, capsys, tmp_path):
    code, out, err, _ = _run(["eval", f"--file={tmp_path / 'nope.js'}"], monkeypatch, capsys)
    assert code == 1
    assert out.startswith("error: file not found: ")
    assert err == ""


# -- 5. unknown command ------------------------------------------------------


def test_unknown_command_suggests_a_close_match(monkeypatch, capsys):
    _forbid_daemon(monkeypatch)
    code, out, err, _ = _run(["snapshto"], monkeypatch, capsys)
    assert code == 2
    assert out.splitlines() == [
        "error: unknown command `snapshto`",
        "help: did you mean `snapshot`?",
        "help: run `patchright-cli --help` for all commands",
    ]
    assert err == ""


def test_unknown_command_without_a_close_match(monkeypatch, capsys):
    code, out, _, _ = _run(["zzzzqqq"], monkeypatch, capsys)
    assert code == 2
    assert out.splitlines() == [
        "error: unknown command `zzzzqqq`",
        "help: run `patchright-cli --help` for all commands",
    ]


# -- 6. version --------------------------------------------------------------


@pytest.mark.parametrize("flag", ["-v", "-V", "--version"])
def test_version_flags_print_the_bare_version(flag, monkeypatch, capsys):
    code, out, _, _ = _run([flag], monkeypatch, capsys)
    assert code == 0
    assert out == f"{__version__}\n"


@pytest.mark.parametrize("flag", ["-v", "-V", "--version"])
def test_entry_fast_path_skips_the_cli(flag):
    probe = (
        "import sys; from patchright_cli import _entry; sys.argv = ['patchright-cli', sys.argv[1]]; "
        "_entry.main(); print('cli' if 'patchright_cli.cli' in sys.modules else 'fast')"
    )
    out = subprocess.run([sys.executable, "-c", probe, flag], capture_output=True, text=True, check=True).stdout
    assert out.splitlines() == [__version__, "fast"]


def test_console_script_and_module_use_the_fast_entry():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    assert 'patchright-cli = "patchright_cli._entry:main"' in (root / "pyproject.toml").read_text(encoding="utf-8")
    main_py = (root / "src" / "patchright_cli" / "__main__.py").read_text(encoding="utf-8")
    assert "from patchright_cli._entry import main" in main_py


def _best_of(cmd, runs=3):
    best = float("inf")
    for _ in range(runs):
        start = time.perf_counter()
        subprocess.run(cmd, capture_output=True, check=True)
        best = min(best, time.perf_counter() - start)
    return best


def test_version_is_close_to_the_interpreter_floor():
    """`--version` must not pay for click, the daemon or Patchright."""
    floor = _best_of([sys.executable, "-c", "print(1)"])
    version = _best_of([sys.executable, "-m", "patchright_cli", "--version"])
    # Generous: importing the full CLI costs several floors on its own.
    assert version < floor * 3 + 0.15, f"--version took {version:.3f}s against a {floor:.3f}s floor"


# -- 7. --toon renders structured `data` ------------------------------------


def _run_reply(argv, reply, monkeypatch, capsys):
    """Run main() against a daemon that answers every command with `reply`."""
    monkeypatch.setattr(cli, "ensure_daemon_running", lambda *a, **k: False)
    monkeypatch.setattr(cli, "_send_command", lambda *a, **k: reply)
    monkeypatch.setattr(cli, "_warn_stale_skills", lambda: None)
    monkeypatch.setattr(cli, "_load_config", lambda p: {})
    monkeypatch.setattr(sys, "argv", ["patchright-cli", *argv])
    code = 0
    try:
        cli.main()
    except SystemExit as exc:
        code = exc.code or 0
    return code, capsys.readouterr().out


TAB_REPLY = {
    "success": True,
    "output": "### Tabs\n- 0: (current) [Example] (https://example.com/)",
    "data": [{"index": 0, "title": "Example", "url": "https://example.com/", "active": True}],
}


def test_toon_renders_a_data_list(monkeypatch, capsys):
    code, out = _run_reply(["--toon", "tab-list"], TAB_REPLY, monkeypatch, capsys)
    assert code == 0
    assert out.splitlines() == ["tabs[1]{index,title,url,active}:", '  0,Example,"https://example.com/",true']


def test_toon_renders_a_data_dict(monkeypatch, capsys):
    reply = {"success": True, "output": "https://example.com/", "data": {"url": "https://example.com/"}}
    code, out = _run_reply(["url", "--toon"], reply, monkeypatch, capsys)
    assert code == 0
    assert out == 'url: "https://example.com/"\n'


def test_toon_empty_list_is_explicit(monkeypatch, capsys):
    reply = {"success": True, "output": "0 cookies in this session", "data": []}
    _, out = _run_reply(["--toon", "cookie-list"], reply, monkeypatch, capsys)
    assert out == "cookies: []\n"


def test_toon_without_data_falls_back_to_text(monkeypatch, capsys):
    reply = {"success": True, "output": "### Page\n- Page URL: about:blank"}
    _, plain = _run_reply(["snapshot"], reply, monkeypatch, capsys)
    _, toon = _run_reply(["--toon", "snapshot"], reply, monkeypatch, capsys)
    assert toon == plain == "### Page\n- Page URL: about:blank\n"


def test_toon_keeps_unicode_line_separators_inside_a_value(monkeypatch, capsys):
    # TOON does not escape U+2028/U+0085; splitting the document with
    # splitlines() broke the value across two lines.
    reply = {"success": True, "output": "A\u2028B\u0085C", "data": {"title": "A\u2028B\u0085C"}}
    _, out = _run_reply(["title", "--toon"], reply, monkeypatch, capsys)
    assert out == "title: A\u2028B\u0085C\n"


def test_toon_leaves_failures_alone(monkeypatch, capsys):
    reply = {"success": False, "output": "Session 'default' is not open", "data": []}
    code, out = _run_reply(["--toon", "tab-list"], reply, monkeypatch, capsys)
    assert code == 1
    assert out == "Session 'default' is not open\n"


def test_toon_truncated_list_carries_total_and_a_hint(monkeypatch, capsys):
    reply = {
        "success": True,
        "output": "Found 1 of 340 matches",
        "data": [{"ref": "e1", "role": "link", "name": "Sign in"}],
        "total": 340,
    }
    _, out = _run_reply(["--toon", "find", "Sign"], reply, monkeypatch, capsys)
    lines = out.splitlines()
    assert lines[:2] == ["matches[1]{ref,role,name}:", "  e1,link,Sign in"]
    assert "total: 340" in lines
    assert lines[-1] == "help: showing 1 of 340; narrow the query, or raise --limit=N"


def test_default_output_ignores_data(monkeypatch, capsys):
    _, out = _run_reply(["tab-list"], TAB_REPLY, monkeypatch, capsys)
    assert out == TAB_REPLY["output"] + "\n"


def test_json_and_toon_conflict(monkeypatch, capsys):
    _forbid_daemon(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["patchright-cli", "--json", "--toon", "tab-list"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert capsys.readouterr().out.startswith("error: --json and --toon are mutually exclusive")


def test_highlight_hide_without_a_ref_reaches_the_daemon(monkeypatch, capsys):
    # The daemon clears every overlay for `highlight --hide`; only a bare
    # `highlight` still needs its ref.
    code, _, _, sent = _run(["highlight", "--hide"], monkeypatch, capsys)
    assert (code, sent["command"], sent["args"]) == (0, "highlight", [])
    code, out, _, _ = _run(["highlight"], monkeypatch, capsys)
    assert code == 2
    assert out.startswith("error: `highlight` requires <ref>")


# -- review fixes: find --regex=, empty stdin, --toon fallback, bad config ----


def test_find_regex_value_needs_no_text_argument(monkeypatch, capsys):
    # `find --regex=<pattern>` carries its text in the flag; bare --regex still needs <text>.
    code, _, _, sent = _run(["find", "--regex=Sign.*"], monkeypatch, capsys)
    assert (code, sent["command"]) == (0, "find")
    code, out, _, _ = _run(["find", "--regex"], monkeypatch, capsys)
    assert code == 2
    assert out.startswith("error: `find` requires <text>")


def test_eval_with_empty_piped_stdin_is_a_usage_error(monkeypatch, capsys):
    import io

    for argv in (["eval"], ["eval", "-"]):
        monkeypatch.setattr(sys, "stdin", io.StringIO("  "))
        code, out, _, sent = _run(argv, monkeypatch, capsys, stdin_tty=False)
        assert code == 2, argv
        assert out.startswith("error: `eval` requires a JS expression")
        assert sent == {}


def test_toon_falls_back_to_text_when_it_cannot_encode(monkeypatch, capsys):
    # A lone surrogate in a page title cannot be encoded; the text output still can.
    title = "A" + chr(0xD800) + "B"
    reply = {"success": True, "output": "Example", "data": {"title": title}}
    code, out = _run_reply(["title", "--toon"], reply, monkeypatch, capsys)
    assert (code, out) == (0, "Example" + chr(10))


def test_malformed_config_is_a_usage_error_naming_the_file(tmp_path):
    config = tmp_path / "config.json"
    for text in ("{not json", "[]"):
        config.write_text(text)
        with pytest.raises(cli.UsageError, match="config file"):
            cli._load_config(str(config))


def test_malformed_config_does_not_block_kill_all(tmp_path, monkeypatch, capsys):
    (tmp_path / ".patchright-cli").mkdir()
    (tmp_path / ".patchright-cli" / "config.json").write_text("{not json")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_kill_all", lambda port, options, force: {"success": True, "output": "Stopped."})
    monkeypatch.setattr(cli, "_warn_stale_skills", lambda: None)
    monkeypatch.setattr(sys, "argv", ["patchright-cli", "kill-all"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
        raise SystemExit(0)
    assert (exc.value.code or 0, capsys.readouterr().out.strip()) == (0, "Stopped.")


@pytest.mark.parametrize("content", ["[]", "null", '{"at": "soon", "version": "x"}'])
def test_foreign_stamp_files_do_not_break_commands(tmp_path, monkeypatch, content):
    monkeypatch.setattr(cli, "_skill_check_stamp", lambda: tmp_path / "skill-check.json")
    (tmp_path / "skill-check.json").write_text(content)
    assert cli._skill_warning_is_throttled() is False
