# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

patchright-cli is an anti-detect browser automation CLI built on [Patchright](https://github.com/kaliiiiiiiiii/patchright-python) (undetected Playwright fork). It provides the same command interface as Microsoft's playwright-cli but bypasses bot detection (Akamai, Cloudflare, etc.).

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

There are no automated tests yet — changes are tested manually via the CLI.

## Architecture

Three-component client-daemon-browser design:

```
CLI (cli.py) --TCP:9321--> Daemon (daemon.py) --Patchright--> Chrome
```

- **CLI (`src/patchright_cli/cli.py`)**: Stateless thin client. Parses argv manually (not click subcommands), builds a JSON `{command, args, options, cwd}` message, sends it over a length-prefixed TCP socket to the daemon, prints the response, and exits. All commands are defined in the `COMMANDS_HELP` dict and `ALL_COMMANDS` list.

- **Daemon (`src/patchright_cli/daemon.py`)**: Long-running async process managing browser sessions. Auto-spawned on `open` via `ensure_daemon_running()` which launches a subprocess. Uses `DaemonState` → multiple `Session` objects, each owning a Patchright persistent browser context. Command dispatch is a large `handle_command()` function with if/elif branches. Console capture uses CDP sessions (not Playwright's `page.on('console')`) because Patchright suppresses it.

- **Snapshot (`src/patchright_cli/snapshot.py`)**: Two-pass DOM scanner injected as JavaScript: Pass 1 uses TreeWalker to assign `data-patchright-ref` attributes (`e1`, `e2`, ...) in document order; Pass 2 builds a nested tree. Python side converts to flat YAML. Snapshots are saved to `.patchright-cli/` in the caller's cwd.

## Key Design Decisions

- Always uses persistent browser context (`launch_persistent_context`) with real Chrome (`channel="chrome"`), not Chromium
- Headed by default (headless is more detectable)
- Default daemon port: 9321, profiles stored at `~/.patchright-cli/profiles/<session-name>`
- CLI-daemon protocol: length-prefixed (4-byte big-endian `!I`) JSON over TCP
- CLI parses argv manually in `main()` — click is only used for `click.echo()`, not subcommands. New commands must be added to both `COMMANDS_HELP` dict and the `handle_command()` if/elif chain in daemon.py
- Version must be updated in both `pyproject.toml` and `src/patchright_cli/__init__.py` (then run `uv lock` to sync the lock file)
- `python -m patchright_cli` works via `__main__.py`
- Uses hatchling as build backend, ruff for linting (line-length=120, target py310)
- Ruff rules: E, F, I, W, UP, B, SIM (ignores E501, SIM105, E402)
