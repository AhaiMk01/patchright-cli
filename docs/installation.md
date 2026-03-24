# patchright-cli Installation Guide

## Prerequisites

- Python 3.10+
- Google Chrome installed on the system

## Install

```bash
pip install patchright-cli
```

Or with uv (recommended):

```bash
uv tool install patchright-cli
```

## Verify

```bash
patchright-cli --version
patchright-cli open https://example.com
patchright-cli eval "document.title"
patchright-cli close
```

## Claude Code Integration

Copy the skill into your Claude Code skills directory:

```bash
mkdir -p ~/.claude/skills/patchright-cli
curl -s https://raw.githubusercontent.com/AhaiMk01/patchright-cli/main/skills/patchright-cli/SKILL.md \
  -o ~/.claude/skills/patchright-cli/SKILL.md
```

Then restart Claude Code. The `patchright-cli` skill will be available automatically.

## Usage

```bash
# Open an anti-bot-protected site
patchright-cli open https://protected-site.com

# Take a snapshot to see interactive elements
patchright-cli snapshot

# Interact with elements using refs from the snapshot
patchright-cli fill e3 "username"
patchright-cli fill e4 "password"
patchright-cli click e5

# Save login state for reuse
patchright-cli state-save auth.json

# Later, restore the session
patchright-cli open https://protected-site.com --persistent
patchright-cli state-load auth.json

# Close when done
patchright-cli close
```

## All Commands

Run `patchright-cli --help` for the full command reference.
