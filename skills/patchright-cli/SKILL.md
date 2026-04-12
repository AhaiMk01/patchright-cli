---
name: patchright-cli
description: Anti-detect browser automation using Patchright (undetected Playwright fork). Use when you need to interact with websites that block regular Playwright/Chrome DevTools, such as Akamai/Cloudflare-protected sites. Provides the same command interface as playwright-cli but bypasses bot detection. Use this skill whenever the user asks to automate a browser, scrape a website, fill forms, log into sites, take screenshots, or do anything involving Chrome automation — especially if the target site has bot protection like Cloudflare or Akamai.
---

# Anti-Detect Browser Automation with patchright-cli

patchright-cli drives a real Chrome browser that passes bot detection (Cloudflare, Akamai, etc.). It works as a CLI: you issue commands, get back page state. This is the tool to reach for whenever regular Playwright or Chrome DevTools gets blocked.

## How it works: the snapshot-driven loop

Every interaction follows the same cycle:

```
open browser → snapshot → read refs → interact → snapshot again → repeat
```

1. **Open** a browser session — this launches a real Chrome window
2. **Snapshot** the page — this scans the DOM and assigns refs (`e1`, `e2`, ...) to interactive elements
3. **Read the snapshot** — it's a YAML file showing the element tree with refs
4. **Interact** using refs — `click e5`, `fill e3 "hello"`, etc.
5. **Check the result** — most commands auto-snapshot, so you get updated refs

Refs are ephemeral. They change every time the page updates. If a command fails with "Could not locate element for ref", the page has changed — just run `snapshot` to get fresh refs.

## Quick start

```bash
patchright-cli open https://example.com     # launch browser + navigate
patchright-cli snapshot                      # get element refs
# read the snapshot YAML to find the right ref, then:
patchright-cli click e5                      # interact by ref
patchright-cli fill e3 "search query"        # type into an input
patchright-cli press Enter                   # press a key
patchright-cli screenshot                    # capture the page
patchright-cli close                         # done
```

## Installation

### CLI tool

```bash
# Recommended — always runs latest version
uvx patchright-cli <command>

# Or install globally
pip install patchright-cli
```

### Skill (for AI coding agents)

Install the skill so your agent knows how to use patchright-cli. The same command also works to update to the latest version.

**macOS / Linux (bash/zsh):**

| Agent | Install / update command |
|-------|--------------------------|
| **Claude Code** | `mkdir -p ~/.claude/skills/patchright-cli && curl -sL https://raw.githubusercontent.com/AhaiMk01/patchright-cli/main/skills/patchright-cli/SKILL.md -o ~/.claude/skills/patchright-cli/SKILL.md` |
| **Gemini CLI** | `mkdir -p ~/.gemini/skills/patchright-cli && curl -sL https://raw.githubusercontent.com/AhaiMk01/patchright-cli/main/skills/patchright-cli/SKILL.md -o ~/.gemini/skills/patchright-cli/SKILL.md` |
| **Codex CLI** | `mkdir -p ~/.codex/skills/patchright-cli && curl -sL https://raw.githubusercontent.com/AhaiMk01/patchright-cli/main/skills/patchright-cli/SKILL.md -o ~/.codex/skills/patchright-cli/SKILL.md` |
| **OpenCode** | `mkdir -p ~/.opencode/skills/patchright-cli && curl -sL https://raw.githubusercontent.com/AhaiMk01/patchright-cli/main/skills/patchright-cli/SKILL.md -o ~/.opencode/skills/patchright-cli/SKILL.md` |

**Windows (PowerShell):**

| Agent | Install / update command |
|-------|--------------------------|
| **Claude Code** | `New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.claude\skills\patchright-cli" | Out-Null; Invoke-WebRequest -Uri "https://raw.githubusercontent.com/AhaiMk01/patchright-cli/main/skills/patchright-cli/SKILL.md" -OutFile "$env:USERPROFILE\.claude\skills\patchright-cli\SKILL.md"` |
| **Gemini CLI** | `New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.gemini\skills\patchright-cli" | Out-Null; Invoke-WebRequest -Uri "https://raw.githubusercontent.com/AhaiMk01/patchright-cli/main/skills/patchright-cli/SKILL.md" -OutFile "$env:USERPROFILE\.gemini\skills\patchright-cli\SKILL.md"` |
| **Codex CLI** | `New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.codex\skills\patchright-cli" | Out-Null; Invoke-WebRequest -Uri "https://raw.githubusercontent.com/AhaiMk01/patchright-cli/main/skills/patchright-cli/SKILL.md" -OutFile "$env:USERPROFILE\.codex\skills\patchright-cli\SKILL.md"` |
| **OpenCode** | `New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.opencode\skills\patchright-cli" | Out-Null; Invoke-WebRequest -Uri "https://raw.githubusercontent.com/AhaiMk01/patchright-cli/main/skills/patchright-cli/SKILL.md" -OutFile "$env:USERPROFILE\.opencode\skills\patchright-cli\SKILL.md"` |

**IDE-based agents (Cursor / Windsurf / Aider):** Copy `SKILL.md` to `.<tool>/skills/patchright-cli/` in your project.

**Or just tell your agent:**
> Install patchright-cli skill from https://raw.githubusercontent.com/AhaiMk01/patchright-cli/main/skills/patchright-cli/SKILL.md

## Global options

These go before the command:

```bash
--headless          # Run headless (default: headed — headed is less detectable)
--persistent        # Use persistent profile (keeps cookies/storage across sessions)
--profile=/path     # Custom profile directory
--proxy=<url>       # Proxy server (http, https, socks5)
-s=mysession        # Named session (default: "default")
--port=9322         # Custom daemon port (default: 9321)
```

---

## Core commands

### Browser lifecycle

```bash
patchright-cli open                            # Launch browser
patchright-cli open https://example.com        # Launch and navigate
patchright-cli open --persistent               # Keep cookies/storage between runs
patchright-cli open --headless                 # Headless mode
patchright-cli open --profile=/path/to/dir     # Custom profile directory
patchright-cli --proxy=http://host:port open   # Route traffic through HTTP proxy
patchright-cli --proxy=socks5://host:port open # SOCKS5 proxy
patchright-cli close                           # Close session
```

### Navigation

```bash
patchright-cli goto https://example.com
patchright-cli go-back
patchright-cli go-forward
patchright-cli reload
```

### Snapshots

Snapshots are how you discover what's on the page. They produce a YAML tree of interactive elements, each tagged with a ref like `e1`, `e5`, `e12`.

```bash
patchright-cli snapshot                        # Full page snapshot
patchright-cli snapshot e3                     # Subtree of a specific element
patchright-cli snapshot --filename=snap.yml    # Save to custom path
```

After each state-changing command (click, fill, goto, etc.), a snapshot is automatically taken and returned. You don't need to manually snapshot after every action.

### Clicking and interacting

```bash
patchright-cli click e3                        # Left-click
patchright-cli click e3 right                  # Right-click
patchright-cli click e3 --modifiers=Alt,Shift  # Click with modifier keys
patchright-cli dblclick e7                     # Double-click
patchright-cli dblclick e7 --modifiers=Shift
patchright-cli hover e4
patchright-cli drag e2 e8                      # Drag element to target
```

### Form input

```bash
patchright-cli fill e5 "user@example.com"      # Clear field and type value
patchright-cli fill e5 "query" --submit        # Fill and press Enter
patchright-cli type "search query"             # Type via keyboard (no target element)
patchright-cli type "query" --submit           # Type and press Enter
patchright-cli select e9 "option-value"        # Select dropdown option
patchright-cli check e12                       # Check checkbox/radio
patchright-cli uncheck e12
```

`fill` targets a specific input by ref and replaces its contents. `type` types via the keyboard into whatever is focused.

### Screenshots

```bash
patchright-cli screenshot                      # Page screenshot
patchright-cli screenshot e3                   # Element screenshot
patchright-cli screenshot --filename=page.png  # Custom filename
patchright-cli screenshot --full-page          # Full scrollable page
```

Screenshots save to `.patchright-cli/` in the current directory.

### Keyboard and mouse

```bash
# Keyboard
patchright-cli press Enter                     # Single keypress
patchright-cli press ArrowDown
patchright-cli keydown Shift                   # Hold key down
patchright-cli keyup Shift                     # Release key

# Mouse
patchright-cli mousemove 150 300               # Move to coordinates
patchright-cli mousedown                       # Left button down
patchright-cli mousedown right                 # Right button down
patchright-cli mouseup
patchright-cli mousewheel 0 100                # Scroll (dx, dy)
```

### Tabs

```bash
patchright-cli tab-list                        # List open tabs
patchright-cli tab-new https://example.com     # Open new tab
patchright-cli tab-select 0                    # Switch to tab by index
patchright-cli tab-close                       # Close current tab
patchright-cli tab-close 2                     # Close tab by index
```

---

## JavaScript execution

**Always use `--file` or stdin for `eval` and `run-code`.** Inline JS breaks because quotes get mangled through shell layers (bash -> uvx -> python).

```bash
# WRONG — nested quotes break
patchright-cli eval "document.querySelector('a').href"

# RIGHT — write to a temp file
cat > /tmp/check.js << 'JSEOF'
JSON.stringify({title: document.title, links: document.querySelectorAll('a').length})
JSEOF
patchright-cli eval --file=/tmp/check.js

# RIGHT — pipe via stdin (simple expressions)
echo 'document.title' | patchright-cli eval
```

`eval` returns the expression result. `run-code` wraps your code in `async () => { ... }` so you can use `return` and `await`.

```bash
# run-code example
cat > /tmp/scroll.js << 'JSEOF'
window.scrollTo(0, document.body.scrollHeight);
return document.body.scrollHeight;
JSEOF
patchright-cli run-code --file=/tmp/scroll.js
```

---

## Common patterns

### Using a proxy

```bash
# HTTP proxy
patchright-cli --proxy=http://host:port open https://example.com

# SOCKS5 proxy
patchright-cli --proxy=socks5://host:port open https://example.com

# Authenticated proxy
patchright-cli --proxy=http://user:pass@host:port open https://example.com

# Combine with persistent profile and named session
patchright-cli --proxy=http://host:port -s=proxied open https://example.com --persistent
```

The proxy is set at browser launch and applies to all traffic in that session. Note: `--proxy` is a global option and must come before the command.

### Login flow

```bash
patchright-cli open https://example.com/login --persistent
patchright-cli snapshot
# Find the username/password fields and login button in the snapshot
patchright-cli fill e3 "user@example.com"
patchright-cli fill e5 "password123"
patchright-cli click e8                        # Click login button
# Wait a moment for redirect, then snapshot to verify
patchright-cli snapshot
# With --persistent, cookies survive across sessions
```

### Extracting data from a page

```bash
patchright-cli open https://example.com/data
patchright-cli snapshot                        # Get the page structure
# Use eval to extract structured data
cat > /tmp/extract.js << 'JSEOF'
JSON.stringify(
  [...document.querySelectorAll('table tr')].map(row =>
    [...row.cells].map(cell => cell.textContent.trim())
  )
)
JSEOF
patchright-cli eval --file=/tmp/extract.js
```

### Waiting for dynamic content

The page may not be fully loaded when you snapshot. If elements are missing, try:

```bash
# Option 1: Just snapshot again after a short pause
# (most commands auto-wait for navigation to settle)
patchright-cli snapshot

# Option 2: Use run-code to wait for a specific condition
cat > /tmp/wait.js << 'JSEOF'
for (let i = 0; i < 30; i++) {
  if (document.querySelector('.results-loaded')) return true;
  await new Promise(r => setTimeout(r, 500));
}
return false;
JSEOF
patchright-cli run-code --file=/tmp/wait.js
patchright-cli snapshot
```

### Multi-page workflow with persistent session

```bash
# Use a named persistent session to maintain state
patchright-cli -s=myproject open https://app.example.com --persistent
patchright-cli -s=myproject snapshot
patchright-cli -s=myproject click e5
# ... do work ...
patchright-cli -s=myproject close
# Later, reopen — cookies and localStorage are preserved
patchright-cli -s=myproject open https://app.example.com --persistent
```

### Handling dialogs (alert/confirm/prompt)

Dialogs must be pre-armed *before* the action that triggers them:

```bash
patchright-cli dialog-accept              # Accept next dialog
patchright-cli click e5                   # This click triggers the dialog
# Or:
patchright-cli dialog-accept "OK"         # Accept with text input
patchright-cli dialog-dismiss             # Dismiss instead
```

---

## Storage and state

### Cookies

```bash
patchright-cli cookie-list                              # All cookies
patchright-cli cookie-list --domain=example.com         # Filter by domain
patchright-cli cookie-list --path=/api                  # Filter by path
patchright-cli cookie-get session_id                    # Get specific cookie
patchright-cli cookie-set session_id abc123             # Set cookie (current page URL)
patchright-cli cookie-set token xyz --domain=example.com --path=/ --httpOnly --secure --sameSite=Lax --expires=1735689600
patchright-cli cookie-delete session_id
patchright-cli cookie-clear                             # Clear all
```

### localStorage / sessionStorage

```bash
# localStorage
patchright-cli localstorage-list
patchright-cli localstorage-get theme
patchright-cli localstorage-set theme dark
patchright-cli localstorage-delete theme
patchright-cli localstorage-clear

# sessionStorage (same pattern)
patchright-cli sessionstorage-list
patchright-cli sessionstorage-get step
patchright-cli sessionstorage-set step 3
patchright-cli sessionstorage-delete step
patchright-cli sessionstorage-clear
```

### Save/load full state (cookies + storage)

```bash
patchright-cli state-save auth.json        # Save cookies + localStorage
patchright-cli state-load auth.json        # Restore (navigate to matching origin first for localStorage)
```

---

## Network tools

### Request mocking

```bash
patchright-cli route "**/*.jpg" --status=404                          # Block images
patchright-cli route "https://api.example.com/**" --body='{"mock": true}'  # Mock API
patchright-cli route "**/*" --content-type=application/json --body='{"ok":true}'
patchright-cli route "**/*" --header=X-Custom:value
patchright-cli route "**/*" --remove-header=Content-Type
patchright-cli route-list                                             # List active routes
patchright-cli unroute "**/*.jpg"                                     # Remove specific route
patchright-cli unroute                                                # Remove all routes
```

### Network state

```bash
patchright-cli network-state-set offline   # Simulate offline
patchright-cli network-state-set online    # Restore connectivity
```

### DevTools inspection

```bash
patchright-cli console                     # Show console messages (last 50)
patchright-cli console warning             # Filter by level
patchright-cli console --clear             # Clear buffer after printing

patchright-cli network                     # Show requests (excludes static assets)
patchright-cli network --static            # Include images, fonts, scripts, etc.
patchright-cli network --clear             # Clear log after printing
```

---

## Recording and capture

```bash
# Tracing (Playwright trace format)
patchright-cli tracing-start
patchright-cli tracing-stop                # Saves .zip to .patchright-cli/

# Video (CDP screencast, requires ffmpeg for .webm output)
patchright-cli video-start
patchright-cli video-stop                  # Save as .webm (or frames if no ffmpeg)
patchright-cli video-stop --filename=rec.webm

# PDF
patchright-cli pdf                         # Save page as PDF
patchright-cli pdf --filename=page.pdf

# File upload
patchright-cli upload ./document.pdf       # Upload to first file input
patchright-cli upload ./photo.jpg e5       # Upload to specific input

# Viewport resize
patchright-cli resize 1920 1080
```

---

## Session management

```bash
# Named sessions allow multiple independent browsers
patchright-cli -s=session1 open https://site-a.com --persistent
patchright-cli -s=session2 open https://site-b.com --persistent

patchright-cli list                        # List all active sessions
patchright-cli close-all                   # Gracefully close all
patchright-cli kill-all                    # Force-kill all + stop daemon

# Delete persistent profile data
patchright-cli delete-data                 # Delete default session's profile
patchright-cli -s=mysession delete-data    # Delete named session's profile
```

---

## Anti-detect notes

- Uses real Chrome (not Chromium) — this is what makes it undetectable
- Patchright patches `navigator.webdriver` and other detection vectors automatically
- Headed by default — headless mode is more detectable, use only when necessary
- No custom user-agent or headers — preserves Chrome's natural fingerprint
- Persistent profiles maintain realistic browser history and cookies
- The daemon architecture means Chrome stays running between commands, behaving like a real user's browser
