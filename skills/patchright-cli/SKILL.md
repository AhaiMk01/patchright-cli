---
name: patchright-cli
description: Anti-detect browser automation using Patchright (undetected Playwright fork). Use when you need to interact with websites that block regular Playwright/Chrome DevTools, such as Akamai/Cloudflare-protected sites. Provides the same command interface as playwright-cli but bypasses bot detection.
allowed-tools: Bash(patchright-cli:*), Bash(python -m patchright_cli:*)
---

# Anti-Detect Browser Automation with patchright-cli

Uses Patchright (undetected Chrome) instead of regular Playwright. Same commands as playwright-cli.

## Quick start

```bash
# open browser (launches undetected Chrome)
patchright-cli open
# navigate to a page
patchright-cli goto https://example.com
# interact using refs from snapshot
patchright-cli click e15
patchright-cli fill e5 "search query"
patchright-cli press Enter
# take a screenshot
patchright-cli screenshot
# close browser
patchright-cli close
```

## Commands

### Core

```bash
patchright-cli open
patchright-cli open https://example.com/
patchright-cli goto https://example.com
patchright-cli type "search query"
patchright-cli click e3
patchright-cli dblclick e7
patchright-cli fill e5 "user@example.com"
patchright-cli drag e2 e8
patchright-cli hover e4
patchright-cli select e9 "option-value"
patchright-cli check e12
patchright-cli uncheck e12
patchright-cli snapshot
patchright-cli eval "document.title"
patchright-cli eval "el => el.textContent" e5
patchright-cli screenshot
patchright-cli screenshot --filename=page.png
patchright-cli close
```

### Navigation

```bash
patchright-cli go-back
patchright-cli go-forward
patchright-cli reload
```

### Keyboard

```bash
patchright-cli press Enter
patchright-cli press ArrowDown
patchright-cli keydown Shift
patchright-cli keyup Shift
```

### Mouse

```bash
patchright-cli mousemove 150 300
patchright-cli mousedown
patchright-cli mouseup
patchright-cli mousewheel 0 100
```

### Tabs

```bash
patchright-cli tab-list
patchright-cli tab-new https://example.com
patchright-cli tab-select 0
patchright-cli tab-close
```

### Storage

```bash
patchright-cli cookie-list
patchright-cli cookie-get session_id
patchright-cli cookie-set session_id abc123
patchright-cli cookie-delete session_id
patchright-cli cookie-clear
patchright-cli localstorage-list
patchright-cli localstorage-get theme
patchright-cli localstorage-set theme dark
patchright-cli localstorage-clear
```

### DevTools

```bash
patchright-cli console
patchright-cli network
```

### Sessions

```bash
# Named sessions
patchright-cli -s=mysession open https://example.com --persistent
patchright-cli -s=mysession click e6
patchright-cli -s=mysession close
# List all sessions
patchright-cli list
# Close all browsers
patchright-cli close-all
patchright-cli kill-all
```

## Anti-detect features

- Uses real Chrome browser (not Chromium)
- Patchright patches `navigator.webdriver` and other detection vectors
- Persistent profiles for maintaining sessions/cookies
- No custom headers or user-agent (natural fingerprint)
- Headed by default (headless is more detectable)

## Snapshots

After each command, outputs page state and a YAML snapshot file:

```
### Page
- Page URL: https://example.com/
- Page Title: Example Domain
### Snapshot
[Snapshot](.patchright-cli/page-2026-03-22T12-00-00.yml)
```

## Installation

```bash
cd patchright-cli && uv pip install -e .
```

Or run directly:
```bash
python -m patchright_cli open https://example.com
python -m patchright_cli click e1
```
