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

Or run without installing:

```bash
uvx patchright-cli open https://example.com
```

## Verify

```bash
patchright-cli --version
patchright-cli open https://example.com
patchright-cli eval "document.title"
patchright-cli close
```

## Agent Integration

The `patchright-cli` skill works with any AI coding agent that supports SKILL.md. Download the skill file and place it in the appropriate directory for your tool.

```bash
# Download the skill
curl -sL https://raw.githubusercontent.com/AhaiMk01/patchright-cli/main/skills/patchright-cli/SKILL.md -o SKILL.md
```

### Claude Code

```bash
mkdir -p ~/.claude/skills/patchright-cli
curl -sL https://raw.githubusercontent.com/AhaiMk01/patchright-cli/main/skills/patchright-cli/SKILL.md \
  -o ~/.claude/skills/patchright-cli/SKILL.md
```

### OpenClaw

```bash
mkdir -p ~/.openclaw/skills/patchright-cli
curl -sL https://raw.githubusercontent.com/AhaiMk01/patchright-cli/main/skills/patchright-cli/SKILL.md \
  -o ~/.openclaw/skills/patchright-cli/SKILL.md
```

### OpenAI Codex CLI

```bash
mkdir -p ~/.codex/skills/patchright-cli
curl -sL https://raw.githubusercontent.com/AhaiMk01/patchright-cli/main/skills/patchright-cli/SKILL.md \
  -o ~/.codex/skills/patchright-cli/SKILL.md
```

### Gemini CLI

```bash
mkdir -p ~/.gemini/skills/patchright-cli
curl -sL https://raw.githubusercontent.com/AhaiMk01/patchright-cli/main/skills/patchright-cli/SKILL.md \
  -o ~/.gemini/skills/patchright-cli/SKILL.md
```

### Cursor / Windsurf / Aider

Place the skill in your project's `.cursor/skills/`, `.windsurf/skills/`, or `.aider/skills/` directory:

```bash
mkdir -p .cursor/skills/patchright-cli
curl -sL https://raw.githubusercontent.com/AhaiMk01/patchright-cli/main/skills/patchright-cli/SKILL.md \
  -o .cursor/skills/patchright-cli/SKILL.md
```

### Any SKILL.md-compatible agent

The skill file is a standard SKILL.md. Copy it to wherever your agent reads skills from:

```bash
curl -sL https://raw.githubusercontent.com/AhaiMk01/patchright-cli/main/skills/patchright-cli/SKILL.md
```

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
