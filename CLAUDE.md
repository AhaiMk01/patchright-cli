# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

patchright-cli is an anti-detect browser automation CLI built on [Patchright](https://github.com/kaliiiiiiiiii/patchright-python) (undetected Playwright fork). Its commands follow Microsoft's playwright-cli, but it bypasses bot detection (Akamai, Cloudflare, etc.) and is not a drop-in replacement: playwright-cli features Patchright cannot support (other browsers, extensions) are left out, and unknown flags are rejected with exit 2 rather than ignored.

## Development Setup

```bash
uv venv && uv pip install -e .
python -m patchright install chromium
pre-commit install
```

## Commands

```bash
# Lint (pre-commit runs ruff --fix + ruff-format)
pre-commit run --all-files

# Run the CLI directly
python -m patchright_cli <command> [args...]
# or after install:
patchright-cli <command> [args...]
```

## Testing

```bash
# Unit tests (no browser required)
pytest tests/ -v --ignore=tests/test_e2e.py

# E2E tests (requires Chrome)
pytest tests/test_e2e.py -v

# Lint
pre-commit run --all-files

# SKILL.md command block in sync with COMMAND_SPECS (rewrite without --check)
python scripts/gen_skill.py --check
```

## Architecture

Three-component client-daemon-browser design:

```
CLI (cli.py) --TCP:9321--> Daemon (daemon.py) --Patchright--> Chrome
```

- **CLI (`src/patchright_cli/cli.py`)**: Stateless thin client. Parses argv manually (not click subcommands), builds a JSON `{command, args, options, cwd}` message, sends it over a length-prefixed TCP socket to the daemon, prints the response, and exits. All commands are defined in the `COMMAND_SPECS` dict (usage, summary, flags, examples; `COMMANDS_HELP` and `ALL_COMMANDS` derive from it) and grouped for the manual in `COMMAND_CATEGORIES`. With no arguments it prints the home view (`_print_home`): live daemon state from an already-running daemon only, never starting one. `--toon` renders a reply's `data` field with `toon.py`.

- **Daemon (`src/patchright_cli/daemon.py`)**: Long-running async process managing browser sessions. Auto-spawned on `open` via `ensure_daemon_running()` which launches a subprocess. Uses `DaemonState` → multiple `Session` objects, each owning a Patchright persistent browser context. Command dispatch uses `@register()` decorated handlers in `COMMAND_HANDLERS` dict, dispatched by `handle_command()`. Console capture uses CDP sessions (not Playwright's `page.on('console')`) because Patchright suppresses it.

- **Snapshot (`src/patchright_cli/snapshot.py`)**: Uses Playwright's native `aria_snapshot()` to get the accessibility tree. `RefRegistry` annotates each node with sequential refs (`e1`, `e2`, ...). Snapshots are saved as YAML to `.patchright-cli/` in the caller's cwd.

## Key Design Decisions

- Always uses persistent browser context (`launch_persistent_context`) with real Chrome (`channel="chrome"`), not Chromium
- Headed by default (headless is more detectable)
- Default daemon port: 9321, profiles stored at `~/.patchright-cli/profiles/<profile-name>`; a session's profile defaults to its session name (`--profile=<name>` overrides, a value with `/` or `\` is a directory). One session per profile at a time
- CLI-daemon protocol: length-prefixed (4-byte big-endian `!I`) JSON over TCP; every message carries a `token` field read from `~/.patchright-cli/daemon-<port>.token` (written 0600 by the daemon before it listens), and the daemon rejects messages without the right one
- CLI parses argv manually in `main()` — click is only used for `click.echo()`, not subcommands. New commands need a `COMMAND_SPECS` entry, a place in `COMMAND_CATEGORIES`, and a `@register("<name>")` handler in daemon.py, then `python scripts/gen_skill.py` to refresh the generated command block in `skills/patchright-cli/SKILL.md` (`--check` exits 1 when stale; `tests/test_skill_sync.py` enforces it); only session and lifecycle commands (open, attach, close, kill-all, ...) live in `handle_command()` itself
- Version must be updated in `pyproject.toml`, `src/patchright_cli/__init__.py` and `.claude-plugin/marketplace.json` (then run `uv lock` to sync the lock file; `tests/test_packaging.py` checks they match)
- `python -m patchright_cli` works via `__main__.py`
- Uses hatchling as build backend, ruff for linting (line-length=120, target py310)
- Ruff rules: E, F, I, W, UP, B, SIM (ignores E501, SIM105, E402)
