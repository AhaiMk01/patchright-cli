# Contributing to patchright-cli

Thanks for your interest in contributing! Here's how to get started.

## Getting Started

```bash
git clone https://github.com/AhaiMk01/patchright-cli.git
cd patchright-cli
uv venv && uv pip install -e .
python -m patchright install chromium
pre-commit install
```

## Making Changes

1. Fork the repo and create a branch from `main`
2. Make your changes
3. Run `pre-commit run --all-files` to check linting
4. Test your changes manually with `patchright-cli`
5. Commit and open a PR

## What to Contribute

- Bug fixes
- New commands (check playwright-cli for parity gaps)
- Snapshot accuracy improvements
- Platform compatibility fixes (Windows/macOS/Linux)
- Documentation improvements
- Tests

## Code Style

- Python 3.10+
- Formatted with [Ruff](https://github.com/astral-sh/ruff) (enforced via pre-commit)
- Keep the daemon lightweight — avoid heavy dependencies

## Reporting Issues

Open an issue with:
- What you expected
- What happened
- Steps to reproduce
- OS and Python version
