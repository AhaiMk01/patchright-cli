---
name: patchright-cli
description: Anti-detect browser automation using Patchright (undetected Playwright fork). Use when you need to interact with websites that block regular Playwright/Chrome DevTools, such as Akamai/Cloudflare-protected sites. Provides the same command interface as playwright-cli but bypasses bot detection. Use this skill whenever the user asks to automate a browser, scrape a website, fill forms, log into sites, take screenshots, or do anything involving Chrome automation — especially if the target site has bot protection like Cloudflare or Akamai.
---

# Anti-Detect Browser Automation with patchright-cli

patchright-cli drives a real Chrome browser that passes bot detection (Cloudflare, Akamai, etc.). It works as a CLI: you issue commands, get back page state. This is the tool to reach for whenever regular Playwright or Chrome DevTools gets blocked.

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

Every interaction follows: **open -> snapshot -> read refs -> interact -> snapshot again -> repeat**. Refs are ephemeral -- they change on every page update. If a ref fails, re-snapshot.

## Command chaining

Commands can be chained with `&&`. The browser persists via a background daemon, so chaining is safe.

```bash
# Chain when you don't need intermediate output
patchright-cli open https://example.com && patchright-cli screenshot

# Run separately when you need to read refs first
patchright-cli snapshot          # read refs from output
patchright-cli click e5          # use discovered ref
```

## Global options

These go before the command:

```bash
--headless              # Run headless (default: headed -- headed is less detectable)
--persistent            # Use persistent profile (keeps cookies/storage across sessions)
--profile=/path         # Custom profile directory
--proxy=<url>           # Proxy server (http, https, socks5) -- supports user:pass@host auth
-s=mysession            # Named session (default: "default", or PATCHRIGHT_CLI_SESSION env var)
--port=9322             # Custom daemon port (default: 9321)
--config=<path>         # Load options from JSON config file
--cdp=<url>             # Attach to Chrome via CDP endpoint (use with `attach` command)
--device="iPhone 15"    # Emulate a device
--viewport-size=1280x720 # Set viewport size
--locale=en-US          # Browser locale
--timezone=America/New_York # Timezone ID
--geolocation=40.7,-74.0   # Geolocation override (lat,lon)
--user-agent=<ua>       # Custom user agent string
--grant-permissions=geolocation,camera  # Grant permissions at launch
--timeout-action=10000  # Default action timeout (ms)
--timeout-navigation=30000 # Default navigation timeout (ms)
--show-port=9322        # Dashboard port (default: 9322)
```

---

## Core commands

### Browser lifecycle

```bash
open [url]                    # Launch browser (optionally navigate)
open --persistent             # Keep cookies/storage between runs
open --headless               # Headless mode
attach --cdp=<url>            # Attach to existing Chrome via CDP
close                         # Close session
```

### Navigation

```bash
goto <url>                    # Navigate to URL
go-back / go-forward          # History navigation
reload                        # Reload page
url                           # Print current URL
title                         # Print page title
```

### Snapshots

```bash
snapshot                      # Full page snapshot
snapshot <ref>                # Subtree of a specific element
snapshot --depth=N            # Limit depth
snapshot -i                   # Interactive elements only
```

### Interaction

```bash
click <ref>                   # Left-click (smart: navigates links directly)
click <ref> right             # Right-click
click <ref> --modifiers=Alt   # Click with modifier keys
dblclick <ref>                # Double-click
hover <ref>                   # Hover
drag <ref> <target-ref>       # Drag element to target
```

### Form input

```bash
fill <ref> "value"            # Clear field and type value
fill <ref> "value" --submit   # Fill and press Enter
type "text"                   # Type via keyboard (no target)
type "text" --submit          # Type and press Enter
select <ref> "option"         # Select dropdown option
check <ref> / uncheck <ref>   # Checkbox/radio
```

### Text and screenshots

```bash
text <ref>                    # Get text content by ref
text "css-selector"           # Get text by CSS selector
screenshot                    # Page screenshot
screenshot <ref>              # Element screenshot
screenshot --full-page        # Full scrollable page
screenshot --filename=F       # Custom filename
```

### Scroll, wait, and keyboard

```bash
scroll <dx> <dy>              # Scroll by pixels
scroll-to <ref>               # Scroll element into view
wait <ms>                     # Wait N milliseconds
wait-for <ref>                # Wait until element visible
wait-for <ref> --state=hidden # Wait until hidden
press <key>                   # Keypress (Enter, ArrowDown, etc.)
keydown <key> / keyup <key>   # Hold/release key
```

### Mouse

```bash
mousemove <x> <y>             # Move to coordinates
mousedown / mouseup           # Button down/up
mousewheel <dx> <dy>          # Scroll wheel
```

### Tabs

```bash
tab-list                      # List open tabs
tab-new <url>                 # Open new tab
tab-select <index>            # Switch to tab
tab-close [index]             # Close tab
```

### Dialogs

Dialogs must be pre-armed *before* the action that triggers them:

```bash
dialog-accept                 # Accept next dialog
dialog-accept "OK"            # Accept with text input
dialog-dismiss                # Dismiss next dialog
```

### DevTools

```bash
console                       # Show console messages
console warning               # Filter by level
network                       # Show network requests
network --static              # Include static assets
```

### Recording and capture

```bash
screenshot / pdf              # Static capture
video-start / video-stop      # Video recording
tracing-start / tracing-stop  # Playwright trace
codegen / codegen-stop        # Record interactions as script
resize <w> <h>                # Viewport resize
upload <file> [ref]           # File upload
show                          # Live dashboard
```

### Storage

```bash
cookie-list / cookie-get / cookie-set / cookie-delete / cookie-clear
cookie-export / cookie-import
localstorage-list / localstorage-get / localstorage-set / localstorage-delete / localstorage-clear
sessionstorage-list / sessionstorage-get / sessionstorage-set / sessionstorage-delete / sessionstorage-clear
state-save <file> / state-load <file>
```

### Network

```bash
route "<pattern>" [options]   # Mock requests (--status, --body, --content-type, --header)
route-list / unroute          # Manage routes
network-state-set offline     # Simulate offline
network-state-set online      # Restore connectivity
```

### Sessions

```bash
list                          # List active sessions
close-all                     # Close all sessions
kill-all                      # Force-kill all + stop daemon
delete-data                   # Delete session profile data
grant-permissions <perms>     # Grant browser permissions
```

---

## Common patterns

### Login flow

```bash
patchright-cli open https://example.com/login --persistent
patchright-cli snapshot
patchright-cli fill e3 "user@example.com"
patchright-cli fill e5 "password123"
patchright-cli click e8
patchright-cli snapshot
# With --persistent, cookies survive across sessions
```

### Extracting data

```bash
patchright-cli open https://example.com/data
patchright-cli snapshot
cat > /tmp/extract.js << 'JSEOF'
JSON.stringify(
  [...document.querySelectorAll('table tr')].map(row =>
    [...row.cells].map(cell => cell.textContent.trim())
  )
)
JSEOF
patchright-cli eval --file=/tmp/extract.js
```

### Using a proxy

```bash
patchright-cli --proxy=http://host:port open https://example.com
patchright-cli --proxy=socks5://host:port open https://example.com
patchright-cli --proxy=http://user:pass@host:port open https://example.com
```

### Handling dialogs

```bash
patchright-cli dialog-accept              # Arm before triggering action
patchright-cli click e5                   # This click triggers the dialog
```

### Waiting for dynamic content

```bash
patchright-cli wait-for e12               # Wait for element
patchright-cli wait 1000                  # Fixed wait
# For custom conditions, use run-code (see references/running-code.md)
```

---

## Anti-detect notes

- Uses real Chrome (not Chromium) -- this is what makes it undetectable
- Patchright patches `navigator.webdriver` and other detection vectors automatically
- Headed by default -- headless mode is more detectable, use only when necessary
- No custom user-agent or headers by default -- preserves Chrome's natural fingerprint
- Persistent profiles maintain realistic browser history and cookies
- The daemon architecture means Chrome stays running between commands, behaving like a real user's browser
- `attach --cdp` lets you connect to an existing Chrome instance

## Config files

Create `.patchright-cli/config.json` in your project directory (auto-discovered) or pass `--config=<path>`:

```json
{
  "headless": false,
  "persistent": true,
  "proxy": "http://proxy:8080",
  "device": "iPhone 15",
  "locale": "en-US",
  "timezone": "America/New_York"
}
```

CLI flags always override config file values.

## Installation

### CLI tool

```bash
# Recommended -- always runs latest version
uvx patchright-cli <command>

# Or install globally
pip install patchright-cli
```

### Skill (for AI coding agents)

```bash
# Install for all detected agents (Claude Code, Cursor, Gemini, Codex, 40+ more)
npx skills add AhaiMk01/patchright-cli
```

Other methods:

```bash
# Via patchright-cli itself
pip install patchright-cli && patchright-cli install --skills

# Or just tell your agent:
# "Install patchright-cli skill from https://raw.githubusercontent.com/AhaiMk01/patchright-cli/main/skills/patchright-cli/SKILL.md"
```

## Specific tasks

* **Snapshots and element refs** [references/snapshot-refs.md](references/snapshot-refs.md)
* **Browser session management** [references/session-management.md](references/session-management.md)
* **Request mocking** [references/request-mocking.md](references/request-mocking.md)
* **Storage state (cookies, localStorage)** [references/storage-state.md](references/storage-state.md)
* **Running JavaScript** [references/running-code.md](references/running-code.md)
* **Tracing** [references/tracing.md](references/tracing.md)
* **Video recording & capture** [references/video-recording.md](references/video-recording.md)
