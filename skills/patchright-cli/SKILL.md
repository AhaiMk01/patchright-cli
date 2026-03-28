---
name: patchright-cli
description: Anti-detect browser automation using Patchright (undetected Playwright fork). Use when you need to interact with websites that block regular Playwright/Chrome DevTools, such as Akamai/Cloudflare-protected sites. Provides the same command interface as playwright-cli but bypasses bot detection.
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

## Global Options

```bash
patchright-cli --headless ...           # Run headless
patchright-cli --persistent ...         # Use persistent profile
patchright-cli --profile=/path ...      # Custom profile directory
patchright-cli -s=mysession ...         # Named session
patchright-cli --port=9322 ...          # Custom daemon port (default: 9321)
```

## Commands

### Core

```bash
patchright-cli open                            # Launch browser
patchright-cli open https://example.com/       # Launch and navigate
patchright-cli open --persistent               # With persistent profile
patchright-cli open --headless                 # Run headless
patchright-cli open --profile=/path/to/dir     # Custom profile directory
patchright-cli goto https://example.com
patchright-cli type "search query"
patchright-cli type "search query" --submit    # Type and press Enter
patchright-cli click e3
patchright-cli click e3 right                  # Right-click
patchright-cli click e3 --modifiers=Alt,Shift  # Click with modifiers
patchright-cli dblclick e7
patchright-cli dblclick e7 --modifiers=Shift   # Double-click with modifiers
patchright-cli fill e5 "user@example.com"
patchright-cli fill e5 "query" --submit        # Fill and press Enter
patchright-cli drag e2 e8
patchright-cli hover e4
patchright-cli select e9 "option-value"
patchright-cli check e12
patchright-cli uncheck e12
patchright-cli snapshot
patchright-cli snapshot e3                     # Partial snapshot of element subtree
patchright-cli snapshot --filename=snap.yml    # Save to custom path
patchright-cli eval "document.title"
patchright-cli eval --file=script.js           # Read JS from file (avoids shell quoting)
cat script.js | patchright-cli eval            # Read JS from stdin
patchright-cli run-code "return document.querySelectorAll('a').length"
patchright-cli run-code --file=script.js       # Read JS from file
cat script.js | patchright-cli run-code        # Read JS from stdin
patchright-cli screenshot
patchright-cli screenshot e3                   # Element screenshot
patchright-cli screenshot --filename=page.png
patchright-cli screenshot --full-page          # Full page screenshot
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
patchright-cli mousedown                       # Left button (default)
patchright-cli mousedown right                 # Right button
patchright-cli mouseup
patchright-cli mouseup right
patchright-cli mousewheel 0 100
```

### Tabs

```bash
patchright-cli tab-list
patchright-cli tab-new https://example.com
patchright-cli tab-select 0
patchright-cli tab-close              # Close current tab
patchright-cli tab-close 2            # Close tab by index
```

### Storage

```bash
# Cookies
patchright-cli cookie-list
patchright-cli cookie-list --domain=example.com
patchright-cli cookie-list --path=/api
patchright-cli cookie-get session_id
patchright-cli cookie-set session_id abc123
patchright-cli cookie-set session_id abc123 --domain=example.com --path=/ --httpOnly --secure --sameSite=Lax --expires=1735689600
patchright-cli cookie-delete session_id
patchright-cli cookie-clear
# localStorage
patchright-cli localstorage-list
patchright-cli localstorage-get theme
patchright-cli localstorage-set theme dark
patchright-cli localstorage-delete theme
patchright-cli localstorage-clear
```

### Dialog

```bash
patchright-cli dialog-accept
patchright-cli dialog-accept "OK"
patchright-cli dialog-dismiss
```

### Upload / Resize

```bash
patchright-cli upload ./document.pdf
patchright-cli upload ./photo.jpg e5
patchright-cli resize 1920 1080
```

### State Persistence

```bash
patchright-cli state-save auth.json
patchright-cli state-load auth.json
```

### Session Storage

```bash
patchright-cli sessionstorage-list
patchright-cli sessionstorage-get step
patchright-cli sessionstorage-set step 3
patchright-cli sessionstorage-delete step
patchright-cli sessionstorage-clear
```

### Request Mocking

```bash
patchright-cli route "**/*.jpg" --status=404
patchright-cli route "https://api.example.com/**" --body='{"mock": true}'
patchright-cli route "**/*" --content-type=application/json --body='{"ok":true}'
patchright-cli route "**/*" --header=X-Custom:value
patchright-cli route "**/*" --remove-header=Content-Type
patchright-cli route-list
patchright-cli unroute "**/*.jpg"
patchright-cli unroute                         # Remove all routes
patchright-cli network-state-set offline       # Simulate offline mode
patchright-cli network-state-set online        # Restore connectivity
```

### Tracing / Video / PDF

```bash
patchright-cli tracing-start
patchright-cli tracing-stop
patchright-cli video-start                     # Start video recording (CDP screencast)
patchright-cli video-stop                      # Stop and save video (requires ffmpeg for .webm)
patchright-cli video-stop --filename=rec.webm  # Save to custom path
patchright-cli pdf --filename=page.pdf
```

### DevTools

```bash
patchright-cli console
patchright-cli console warning                 # Filter by level
patchright-cli console --clear                 # Clear message buffer after printing
patchright-cli network                         # Show requests (excludes static resources)
patchright-cli network --static                # Include images, fonts, scripts, etc.
patchright-cli network --clear                 # Clear network log after printing
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
patchright-cli close-all                       # Gracefully close all sessions
patchright-cli kill-all                        # Force-kill all sessions and stop daemon
# Delete persistent profile data
patchright-cli delete-data
patchright-cli -s=mysession delete-data
```

## Running JavaScript (eval / run-code)

**Always use `--file` for `eval` and `run-code`**. Inline JS breaks because quotes get mangled through bash → uvx → python shell layers.

```bash
# WRONG — nested quotes break through shell layers
patchright-cli eval "JSON.stringify({x: document.querySelector('a[href*=\"foo\"]')})"

# RIGHT — write JS to a temp file, pass with --file
cat > /tmp/check.js << 'JSEOF'
JSON.stringify({x: !!document.querySelector('a[href*="foo"]')})
JSEOF
patchright-cli eval --file=/tmp/check.js

# RIGHT — pipe via stdin (for simple expressions only)
echo 'document.title' | patchright-cli eval
```

`--file` also avoids the OS argument length limit (`ARG_MAX`) for large scripts.

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
# Recommended — always runs latest version, no install needed
uvx patchright-cli open https://example.com

# Or install via pip
pip install patchright-cli

# Or from source
cd patchright-cli && uv pip install -e .
```
