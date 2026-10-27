"""Session-start hook integration for coding agents (``install --hooks``).

Registers the bare ``patchright-cli`` command as a SessionStart hook so each
agent session starts with patchright-cli's live state already in context:

- Claude Code: ``hooks.SessionStart`` in ``~/.claude/settings.json``
- Codex: ``hooks.SessionStart`` in ``~/.codex/hooks.json``, plus
  ``[features] hooks = true`` in ``~/.codex/config.toml``
- OpenCode: a managed plugin at ``~/.config/opencode/plugins/patchright-cli.js``

Only apps whose config directory already exists are touched. Every edit is a
merge: entries that are not ours are left exactly as they were.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

BINARY = "patchright-cli"
_BINARY_NAMES = {BINARY, f"{BINARY}.exe", f"{BINARY}.cmd", f"{BINARY}.bat"}
_MODULE_SUFFIX = " -m patchright_cli"
PLUGIN_MARKER = "// Managed by patchright-cli (install --hooks)."


class HookError(Exception):
    """A config file we cannot safely edit; the message is shown to the user."""


@dataclass
class AppResult:
    app: str
    status: str  # installed | updated | no-op | removed | skipped | error
    detail: str


# ---------------------------------------------------------------------------
# Hook command
# ---------------------------------------------------------------------------


def _current_executable() -> Path | None:
    """The patchright-cli console script running right now, if there is one."""
    argv0 = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
    if argv0 is not None and argv0.name.lower() in _BINARY_NAMES | {BINARY}:
        for candidate in (argv0, argv0.with_name(argv0.name + ".exe")):
            if candidate.is_file():
                return candidate.resolve()
    # `python -m patchright_cli`: look for the console script next to the interpreter.
    bin_dir = Path(sys.executable).parent
    for folder in (bin_dir, bin_dir / "Scripts", bin_dir / "bin"):
        for name in (f"{BINARY}.exe", f"{BINARY}.cmd", BINARY):
            candidate = folder / name
            if candidate.is_file():
                return candidate.resolve()
    return None


def _same_file(a: str | Path, b: str | Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _quote(path: str) -> str:
    return f'"{path}"' if " " in path else path


def hook_command(executable: Path | None = None, which: str | None | bool = False) -> str:
    """The command a hook runs: ``patchright-cli`` when PATH resolves to us, else an absolute path.

    ``executable`` and ``which`` override discovery for tests (``which=False``
    means "look it up").
    """
    exe = executable if executable is not None else _current_executable()
    on_path = shutil.which(BINARY) if which is False else which
    if exe is None:
        # No console script at all (e.g. a source checkout): run the module.
        return f"{_quote(Path(sys.executable).as_posix())}{_MODULE_SUFFIX}"
    if on_path and _same_file(on_path, exe):
        return BINARY
    # Forward slashes work in cmd.exe, PowerShell and the bash Claude Code uses on Windows.
    return _quote(Path(exe).as_posix())


def is_our_command(command: object) -> bool:
    """True for a bare patchright-cli invocation (any path, no args)."""
    if not isinstance(command, str):
        return False
    cmd = command.strip()
    if cmd.endswith(_MODULE_SUFFIX):
        return True
    cmd = cmd.strip("\"'")
    return re.split(r"[\\/]", cmd)[-1].lower() in _BINARY_NAMES


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------


def _display(path: Path, home: Path) -> str:
    try:
        return "~/" + path.relative_to(home).as_posix()
    except ValueError:
        return str(path)


def _write_atomic(path: Path, text: str) -> None:
    # Write through a symlink (dotfiles managers link these configs), and keep
    # the file's permission bits: os.replace would swap in a fresh file.
    if path.is_symlink():
        path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".patchright-cli.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    with contextlib.suppress(OSError):
        os.chmod(tmp, stat.S_IMODE(path.stat().st_mode))
    os.replace(tmp, path)


def _load_json(path: Path, home: Path) -> dict:
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as e:
        raise HookError(f"cannot read {_display(path, home)}: {e.strerror or e}") from None
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise HookError(f"{_display(path, home)} is not valid JSON (line {e.lineno}); fix it and re-run") from None
    if not isinstance(data, dict):
        raise HookError(f"{_display(path, home)} must contain a JSON object")
    return data


def _dump_json(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------
# hooks.SessionStart merging (Claude Code and Codex share the schema)
# ---------------------------------------------------------------------------


def _session_start_groups(data: dict, path: Path, home: Path) -> list:
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise HookError(f'"hooks" in {_display(path, home)} must be an object')
    groups = hooks.setdefault("SessionStart", [])
    if not isinstance(groups, list):
        raise HookError(f'"hooks.SessionStart" in {_display(path, home)} must be a list')
    return groups


def _our_entries(groups: list) -> list[tuple[dict, dict]]:
    found = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            continue
        for hook in group["hooks"]:
            if (
                isinstance(hook, dict)
                and hook.get("type", "command") == "command"
                and is_our_command(hook.get("command"))
            ):
                found.append((group, hook))
    return found


def _drop_entries(groups: list, entries: list[tuple[dict, dict]]) -> None:
    for group, hook in entries:
        group["hooks"].remove(hook)
        if not group["hooks"] and group in groups:
            groups.remove(group)


def merge_session_start(data: dict, command: str, path: Path, home: Path) -> str:
    """Add or repair our SessionStart entry in ``data``; returns installed/updated/no-op."""
    groups = _session_start_groups(data, path, home)
    found = _our_entries(groups)
    if not found:
        groups.append({"hooks": [{"type": "command", "command": command}]})
        return "installed"
    (_, keep), extra = found[0], found[1:]
    if keep["command"] == command and not extra:
        return "no-op"
    keep["command"] = command
    _drop_entries(groups, extra)
    return "updated"


def remove_session_start(data: dict, path: Path, home: Path) -> bool:
    """Remove our SessionStart entries from ``data``; returns whether anything changed."""
    hooks = data.get("hooks")
    if not isinstance(hooks, dict) or not isinstance(hooks.get("SessionStart"), list):
        return False
    groups = hooks["SessionStart"]
    found = _our_entries(groups)
    if not found:
        return False
    _drop_entries(groups, found)
    if not groups:
        del hooks["SessionStart"]
    if not hooks:
        del data["hooks"]
    return True


def _apply_json_hook(path: Path, home: Path, command: str | None) -> str:
    """Install (command given) or uninstall (None) our hook in one JSON file."""
    data = _load_json(path, home)
    if command is None:
        if not remove_session_start(data, path, home):
            return "no-op"
        _write_atomic(path, _dump_json(data))
        return "removed"
    status = merge_session_start(data, command, path, home)
    if status != "no-op":
        _write_atomic(path, _dump_json(data))
    return status


# ---------------------------------------------------------------------------
# Codex config.toml: [features] hooks = true
# ---------------------------------------------------------------------------

_TABLE_RE = re.compile(r"^\s*\[\[?\s*([^\]]+?)\s*\]\]?\s*(#.*)?$")
# TOML lets a bare key also be written quoted: hooks, "hooks" and 'hooks' are one key.
_FEATURES = r"""(?:features|"features"|'features')"""
_HOOKS = r"""(?:hooks|"hooks"|'hooks')"""
_HOOKS_KEY_RE = re.compile(rf"^(\s*){_HOOKS}\s*=\s*(.*?)\s*(#.*)?$")
_DOTTED_RE = re.compile(rf"^\s*{_FEATURES}\s*\.\s*{_HOOKS}\s*=\s*(.*?)\s*(#.*)?$")
_INLINE_FEATURES_RE = re.compile(rf"^\s*{_FEATURES}\s*=")
_CODEX_MANUAL = "add `hooks = true` under `[features]` in it by hand, then rerun"


def _table_name(raw: str) -> str:
    """A table header's name with whitespace and quotes around each part removed."""
    return ".".join(part.strip().strip("\"'") for part in raw.split("."))


def enable_codex_hooks_feature(text: str) -> tuple[str, bool]:
    """Return ``text`` with ``[features] hooks = true`` set, and whether it changed.

    A minimal line-based edit: it keeps every other line (comments included)
    byte-for-byte, so no TOML library is needed.
    """
    lines = text.splitlines(keepends=True)
    table = None  # None = root table
    features_header = None
    for i, line in enumerate(lines):
        header = _TABLE_RE.match(line)
        if header:
            table = _table_name(header.group(1))
            if table == "features" and line.lstrip().startswith("[") and not line.lstrip().startswith("[["):
                features_header = i
            continue
        if table is None:
            if _INLINE_FEATURES_RE.match(line) and not _DOTTED_RE.match(line):
                # `features = { ... }` cannot be reopened as a table or edited line by line.
                raise HookError(f"`features` is an inline table; {_CODEX_MANUAL}")
            dotted = _DOTTED_RE.match(line)
            if dotted:
                if dotted.group(1) == "true":
                    return text, False
                lines[i] = "features.hooks = true\n"
                return "".join(lines), True
        if table == "features":
            key = _HOOKS_KEY_RE.match(line)
            if key:
                if key.group(2) == "true":
                    return text, False
                lines[i] = f"{key.group(1)}hooks = true\n"
                return "".join(lines), True
    if features_header is not None:
        lines.insert(features_header + 1, "hooks = true\n")
        return "".join(lines), True
    prefix = text
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    if prefix.strip():
        prefix += "\n"
    return prefix + "[features]\nhooks = true\n", True


def _check_codex_toml(new_text: str) -> None:
    """Refuse to write a config.toml that no longer parses or still lacks the flag (Python 3.11+)."""
    try:
        import tomllib
    except ImportError:  # Python 3.10: the line edit is all there is
        return
    try:
        features = tomllib.loads(new_text).get("features")
    except tomllib.TOMLDecodeError:
        features = None
    if not isinstance(features, dict) or features.get("hooks") is not True:
        raise HookError(f"could not enable the hooks feature safely; {_CODEX_MANUAL}")


def _ensure_codex_feature(path: Path, home: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError as e:
        raise HookError(f"cannot read {_display(path, home)}: {e.strerror or e}") from None
    try:
        new_text, changed = enable_codex_hooks_feature(text)
        if changed:
            _check_codex_toml(new_text)
    except HookError as e:
        raise HookError(f"{_display(path, home)}: {e}") from None
    if changed:
        _write_atomic(path, new_text)
    return changed


# ---------------------------------------------------------------------------
# OpenCode plugin
# ---------------------------------------------------------------------------


def opencode_plugin_source(command: str) -> str:
    """A plugin that runs ``command`` once per session and adds its stdout to the system prompt."""
    return f"""{PLUGIN_MARKER}
// Re-run `patchright-cli install --hooks` to repair it, or add --uninstall to remove it.
import {{ execSync }} from "node:child_process"

const COMMAND = {json.dumps(command)}

export const PatchrightCliPlugin = async ({{ directory }}) => {{
  const bySession = new Map()
  return {{
    "experimental.chat.system.transform": async (input, output) => {{
      const key = input.sessionID ?? ""
      if (!bySession.has(key)) {{
        let text = ""
        try {{
          text = execSync(COMMAND, {{
            cwd: directory,
            encoding: "utf8",
            timeout: 10000,
            stdio: ["ignore", "pipe", "ignore"],
          }}).trim()
        }} catch {{}}
        bySession.set(key, text)
      }}
      const text = bySession.get(key)
      if (text) output.system.push(text)
    }},
  }}
}}
"""


def _apply_opencode_plugin(path: Path, home: Path, command: str | None) -> str:
    existing = None
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8")
        except OSError as e:
            raise HookError(f"cannot read {_display(path, home)}: {e.strerror or e}") from None
        if not existing.startswith(PLUGIN_MARKER):
            raise HookError(f"{_display(path, home)} was not written by patchright-cli; move it aside and re-run")
    if command is None:
        if existing is None:
            return "no-op"
        path.unlink()
        return "removed"
    source = opencode_plugin_source(command)
    if existing == source:
        return "no-op"
    _write_atomic(path, source)
    return "installed" if existing is None else "updated"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _config_home(home: Path, home_overridden: bool) -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg and not home_overridden:
        return Path(xdg)
    return home / ".config"


def apply_hooks(
    home: Path | None = None,
    uninstall: bool = False,
    command: str | None = None,
) -> list[AppResult]:
    """Install (or remove) the session-start integration for every detected app."""
    home_overridden = home is not None
    home = Path(home) if home is not None else Path.home()
    if not uninstall and command is None:
        command = hook_command()
    wanted = None if uninstall else command

    # Both apps let users move their config dir; --home (tests) ignores that.
    env = {} if home_overridden else os.environ
    claude_dir = Path(env["CLAUDE_CONFIG_DIR"]).expanduser() if env.get("CLAUDE_CONFIG_DIR") else home / ".claude"
    codex_dir = Path(env["CODEX_HOME"]).expanduser() if env.get("CODEX_HOME") else home / ".codex"
    opencode_dir = _config_home(home, home_overridden) / "opencode"

    results: list[AppResult] = []

    def run(app: str, config_dir: Path, action) -> None:
        if not config_dir.is_dir():
            results.append(AppResult(app, "skipped", f"{_display(config_dir, home)} not found"))
            return
        try:
            status, path = action()
        except HookError as e:
            results.append(AppResult(app, "error", str(e)))
        except OSError as e:
            results.append(AppResult(app, "error", f"cannot write {e.filename or config_dir}: {e.strerror or e}"))
        else:
            results.append(AppResult(app, status, _display(path, home)))

    def claude():
        path = claude_dir / "settings.json"
        return _apply_json_hook(path, home, wanted), path

    def codex():
        path = codex_dir / "hooks.json"
        status = _apply_json_hook(path, home, wanted)
        if wanted is not None and _ensure_codex_feature(codex_dir / "config.toml", home) and status == "no-op":
            # The hook was already there but Codex would never have run it.
            status = "updated"
        return status, path

    def opencode():
        path = opencode_dir / "plugins" / f"{BINARY}.js"
        return _apply_opencode_plugin(path, home, wanted), path

    run("claude-code", claude_dir, claude)
    run("codex", codex_dir, codex)
    run("opencode", opencode_dir, opencode)
    return results


def format_results(results: list[AppResult], command: str | None) -> list[str]:
    """One compact line per app, plus the hook command when installing."""
    lines = [f"hook: {command}"] if command is not None else []
    for r in results:
        if r.status == "error":
            lines.append(f"error: {r.app}: {r.detail}")
        elif r.status == "no-op":
            lines.append(f"{r.app}: {r.detail} (no-op)")
        elif r.status == "skipped":
            lines.append(f"{r.app}: skipped ({r.detail})")
        else:
            lines.append(f"{r.app}: {r.status} {r.detail}")
            if r.app == "codex" and r.status in ("installed", "updated"):
                # Codex skips a new or changed non-managed hook until it is trusted.
                lines.append("help: in Codex, run /hooks and trust the patchright-cli hook, or it will not run")
    return lines
