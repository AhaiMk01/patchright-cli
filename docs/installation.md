# patchright-cli Installation Guide

## Prerequisites

- Python 3.10+
- Google Chrome installed on the system

## Install

### Step 1: Install the package

```bash
pip install patchright-cli
```

Or with uv (recommended):

```bash
uv tool install patchright-cli
```

### Step 2: Install the Patchright browser

Patchright needs to download its patched Chromium binary on first use:

```bash
# macOS / Linux
python -m patchright install chromium

# Windows (PowerShell)
python -m patchright install chromium

# Or if using uv
uv run python -m patchright install chromium
```

> **Note:** If you have Google Chrome installed, patchright-cli will use it directly via `channel="chrome"` for maximum stealth. The Chromium install above is a fallback.

### Step 3: Verify

```bash
patchright-cli --version
patchright-cli open https://example.com
patchright-cli eval "document.title"
patchright-cli close
```

### Quick one-liner (no install)

```bash
uvx patchright-cli open https://example.com
```

### Troubleshooting

If you see `browser not found` errors:

```bash
# Check if Chrome is installed
google-chrome --version    # Linux
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --version  # macOS
reg query "HKLM\SOFTWARE\Google\Chrome\BLBeacon" /v version   # Windows

# If no Chrome, install the Patchright browser
python -m patchright install chromium
```

If you see `patchright-cli: command not found`:

```bash
# Ensure pip scripts directory is in PATH
python -m patchright_cli --help

# Or use uvx (no PATH needed)
uvx patchright-cli --help
```

## Agent Integration

The `patchright-cli` skill works with any AI coding agent that supports SKILL.md. Download the skill file and place it in the appropriate directory for your tool.

The `SKILL_URL` used below:
```
https://raw.githubusercontent.com/AhaiMk01/patchright-cli/main/skills/patchright-cli/SKILL.md
```

### macOS / Linux

```bash
# Claude Code
mkdir -p ~/.claude/skills/patchright-cli && curl -sL "$SKILL_URL" -o ~/.claude/skills/patchright-cli/SKILL.md

# OpenClaw
mkdir -p ~/.openclaw/skills/patchright-cli && curl -sL "$SKILL_URL" -o ~/.openclaw/skills/patchright-cli/SKILL.md

# OpenAI Codex CLI
mkdir -p ~/.codex/skills/patchright-cli && curl -sL "$SKILL_URL" -o ~/.codex/skills/patchright-cli/SKILL.md

# Gemini CLI
mkdir -p ~/.gemini/skills/patchright-cli && curl -sL "$SKILL_URL" -o ~/.gemini/skills/patchright-cli/SKILL.md

# OpenCode
mkdir -p ~/.opencode/skills/patchright-cli && curl -sL "$SKILL_URL" -o ~/.opencode/skills/patchright-cli/SKILL.md

# Cursor / Windsurf / Aider (per-project)
mkdir -p .cursor/skills/patchright-cli && curl -sL "$SKILL_URL" -o .cursor/skills/patchright-cli/SKILL.md
```

### Windows (PowerShell)

```powershell
$SKILL_URL = "https://raw.githubusercontent.com/AhaiMk01/patchright-cli/main/skills/patchright-cli/SKILL.md"

# Claude Code
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.claude\skills\patchright-cli" | Out-Null
Invoke-WebRequest -Uri $SKILL_URL -OutFile "$env:USERPROFILE\.claude\skills\patchright-cli\SKILL.md"

# OpenClaw
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.openclaw\skills\patchright-cli" | Out-Null
Invoke-WebRequest -Uri $SKILL_URL -OutFile "$env:USERPROFILE\.openclaw\skills\patchright-cli\SKILL.md"

# OpenAI Codex CLI
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.codex\skills\patchright-cli" | Out-Null
Invoke-WebRequest -Uri $SKILL_URL -OutFile "$env:USERPROFILE\.codex\skills\patchright-cli\SKILL.md"

# Gemini CLI
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.gemini\skills\patchright-cli" | Out-Null
Invoke-WebRequest -Uri $SKILL_URL -OutFile "$env:USERPROFILE\.gemini\skills\patchright-cli\SKILL.md"

# OpenCode
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.opencode\skills\patchright-cli" | Out-Null
Invoke-WebRequest -Uri $SKILL_URL -OutFile "$env:USERPROFILE\.opencode\skills\patchright-cli\SKILL.md"
```

### Windows (Git Bash)

Same as macOS / Linux commands above — Git Bash supports `mkdir -p`, `curl`, and `~`.

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
