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

Install the patchright-cli skill so your AI coding agent knows how to use it. Works with Claude Code, Cursor, Gemini CLI, Codex, and [40+ other agents](https://github.com/vercel-labs/skills).

### Recommended: Skills CLI

```bash
npx skills add AhaiMk01/patchright-cli
```

This auto-detects your installed agents and installs the skill (including reference docs) to all of them.

### Alternative: patchright-cli built-in

```bash
pip install patchright-cli
patchright-cli install --skills
```

### Alternative: Tell your agent

Just paste this into your agent and it will handle the rest:

> Install patchright-cli skill from https://raw.githubusercontent.com/AhaiMk01/patchright-cli/main/skills/patchright-cli/SKILL.md

### Manual install

Copy the skill file to your agent's skills directory:

```bash
# Example for Claude Code
mkdir -p ~/.claude/skills/patchright-cli
curl -sL https://raw.githubusercontent.com/AhaiMk01/patchright-cli/main/skills/patchright-cli/SKILL.md \
  -o ~/.claude/skills/patchright-cli/SKILL.md
```

Replace `~/.claude` with `~/.gemini`, `~/.codex`, `~/.opencode`, `.cursor`, etc. for other agents.

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
