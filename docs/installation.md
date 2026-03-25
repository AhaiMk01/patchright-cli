# patchright-cli Installation Guide

## Prerequisites

- Python 3.10+
- Google Chrome installed on the system

## Install

### Step 1: Ensure uv is installed

uv is the recommended Python package manager. Check if it's installed:

```bash
uv --version
```

If not installed:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### Step 2: Run patchright-cli

With uvx (recommended — always uses the latest version, no install needed):

```bash
uvx patchright-cli open https://example.com
uvx patchright-cli snapshot
uvx patchright-cli close
```

> **Note:** If you have Google Chrome installed, patchright-cli will use it directly via `channel="chrome"` for maximum stealth. If Chrome is not found, install the Patchright fallback browser: `uvx patchright install chromium`

### Step 3: Verify

```bash
uvx patchright-cli --version
uvx patchright-cli open https://example.com
uvx patchright-cli eval "document.title"
uvx patchright-cli close
```

<details>
<summary><b>Alternative: pip install</b></summary>

```bash
pip install patchright-cli
patchright-cli open https://example.com
patchright-cli close
```

</details>

### Troubleshooting

If you see `browser not found` errors:

```bash
# Check if Chrome is installed
google-chrome --version    # Linux
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --version  # macOS
reg query "HKLM\SOFTWARE\Google\Chrome\BLBeacon" /v version   # Windows

# If no Chrome, install the Patchright browser
uvx patchright install chromium
```

If you see `patchright-cli: command not found`:

```bash
# Use uvx instead (no install or PATH needed)
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
uvx patchright-cli open https://protected-site.com

# Take a snapshot to see interactive elements
uvx patchright-cli snapshot

# Interact with elements using refs from the snapshot
uvx patchright-cli fill e3 "username"
uvx patchright-cli fill e4 "password"
uvx patchright-cli click e5

# Save login state for reuse
uvx patchright-cli state-save auth.json

# Later, restore the session
uvx patchright-cli open https://protected-site.com --persistent
uvx patchright-cli state-load auth.json

# Close when done
uvx patchright-cli close
```

## All Commands

Run `patchright-cli --help` for the full command reference.
