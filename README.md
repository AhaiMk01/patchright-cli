# patchright-cli

Anti-detect browser automation CLI. Same command interface as [Microsoft's playwright-cli](https://github.com/microsoft/playwright-cli) but using [Patchright](https://github.com/AhaiMk01/patchright-python) (undetected Playwright fork) to bypass bot detection.

## Why

Regular Playwright and Chrome DevTools get blocked by Akamai, Cloudflare, and other anti-bot systems. Patchright patches Chromium's detection vectors (`navigator.webdriver`, WebGL fingerprints, etc.) so automated browsers look like real users.

This CLI wraps Patchright in the same command interface as playwright-cli, so AI agents (Claude Code, etc.) can automate anti-bot-protected sites with the same workflow they'd use for regular Playwright.

## Install

```bash
cd patchright-cli
uv venv && uv pip install -e .
```

Or run directly:
```bash
uv run python -m patchright_cli open https://example.com
```

## Quick Start

```bash
# Launch undetected Chrome and navigate
patchright-cli open https://example.com

# Take a snapshot to see interactive elements with refs
patchright-cli snapshot

# Interact using refs from the snapshot
patchright-cli click e2
patchright-cli fill e5 "search query"
patchright-cli press Enter

# Take a screenshot
patchright-cli screenshot

# Close the browser
patchright-cli close
```

## Architecture

```
┌─────────────┐     TCP/JSON      ┌──────────────┐     CDP      ┌─────────┐
│  CLI client  │ ◄──────────────► │    Daemon     │ ◄──────────► │ Chrome  │
│  (cli.py)    │   localhost:9321  │  (daemon.py)  │  Patchright  │(stealth)│
└─────────────┘                   └──────────────┘              └─────────┘
```

- **Daemon** (`daemon.py`): Long-running Python process managing browser sessions via Patchright. Listens on `localhost:9321`. Auto-starts on first `open` command.
- **CLI** (`cli.py`): Thin client. Parses args, sends JSON to daemon, prints result. Each invocation connects and disconnects — the browser stays open between commands.
- **Snapshot** (`snapshot.py`): Walks the DOM with `TreeWalker` in document order, assigns `data-patchright-ref` attributes, outputs a flat YAML list of interactive elements.

## Commands

### Core
```bash
patchright-cli open [url]              # Launch browser
patchright-cli open --persistent       # With persistent profile
patchright-cli goto <url>              # Navigate
patchright-cli click <ref>             # Click element
patchright-cli dblclick <ref>          # Double-click
patchright-cli fill <ref> <value>      # Fill text input
patchright-cli type <text>             # Type via keyboard
patchright-cli hover <ref>             # Hover over element
patchright-cli select <ref> <value>    # Select dropdown option
patchright-cli check <ref>             # Check checkbox
patchright-cli uncheck <ref>           # Uncheck checkbox
patchright-cli drag <from> <to>        # Drag and drop
patchright-cli snapshot                # Accessibility snapshot
patchright-cli snapshot --filename=f   # Save to custom path
patchright-cli eval <expr>             # Run JavaScript
patchright-cli screenshot              # Full page screenshot
patchright-cli screenshot <ref>        # Element screenshot
patchright-cli screenshot --filename=f # Save to custom path
patchright-cli close                   # Close session
```

### Navigation
```bash
patchright-cli go-back
patchright-cli go-forward
patchright-cli reload
```

### Keyboard / Mouse
```bash
patchright-cli press Enter
patchright-cli keydown Shift
patchright-cli keyup Shift
patchright-cli mousemove 150 300
patchright-cli mousedown [button]
patchright-cli mouseup [button]
patchright-cli mousewheel 0 100
```

### Dialog
```bash
patchright-cli dialog-accept [text]    # Accept next alert/confirm/prompt
patchright-cli dialog-dismiss          # Dismiss next dialog
```

### Upload / Resize
```bash
patchright-cli upload ./file.pdf       # Upload to first file input
patchright-cli upload ./file.pdf e5    # Upload to specific input
patchright-cli resize 1920 1080        # Resize viewport
```

### Tabs
```bash
patchright-cli tab-list
patchright-cli tab-new [url]
patchright-cli tab-select <index>
patchright-cli tab-close [index]
```

### State Persistence
```bash
patchright-cli state-save [file]       # Save cookies + localStorage
patchright-cli state-load <file>       # Restore saved state
```

### Storage
```bash
# Cookies
patchright-cli cookie-list
patchright-cli cookie-list --domain=example.com
patchright-cli cookie-get <name>
patchright-cli cookie-set <name> <value>
patchright-cli cookie-set <name> <value> --domain=example.com --httpOnly --secure
patchright-cli cookie-delete <name>
patchright-cli cookie-clear

# localStorage
patchright-cli localstorage-list
patchright-cli localstorage-get <key>
patchright-cli localstorage-set <key> <value>
patchright-cli localstorage-delete <key>
patchright-cli localstorage-clear

# sessionStorage
patchright-cli sessionstorage-list
patchright-cli sessionstorage-get <key>
patchright-cli sessionstorage-set <key> <value>
patchright-cli sessionstorage-delete <key>
patchright-cli sessionstorage-clear
```

### Request Mocking
```bash
patchright-cli route "**/*.jpg" --status=404
patchright-cli route "https://api.example.com/**" --body='{"mock":true}'
patchright-cli route-list
patchright-cli unroute "**/*.jpg"
patchright-cli unroute                 # Remove all routes
```

### Tracing / PDF
```bash
patchright-cli tracing-start
patchright-cli tracing-stop            # Saves .zip trace file
patchright-cli pdf --filename=page.pdf
```

### DevTools
```bash
patchright-cli console                 # All console messages
patchright-cli console warning         # Filter by level
patchright-cli network                 # Network request log
```

### Sessions
```bash
patchright-cli -s=mysession open https://example.com --persistent
patchright-cli -s=mysession click e6
patchright-cli -s=mysession close
patchright-cli list                    # List all sessions
patchright-cli close-all
patchright-cli kill-all
patchright-cli delete-data             # Delete persistent profile
```

## Snapshots

After each state-changing command, the CLI outputs page info and a YAML snapshot:

```
### Page
- Page URL: https://example.com/
- Page Title: Example Domain
### Snapshot
[Snapshot](.patchright-cli/page-1774376207818.yml)
```

The snapshot lists interactive elements with refs:

```yaml
- ref: e1
  role: heading
  name: Example Domain
  level: 1
- ref: e2
  role: link
  name: Learn more
  url: "https://iana.org/domains/example"
```

Use refs in commands: `patchright-cli click e2`, `patchright-cli fill e5 "text"`.

## Anti-Detect Features

- Real Chrome browser (not Chromium)
- Patchright patches `navigator.webdriver` and other detection vectors
- Persistent profiles maintain cookies/sessions across runs
- No custom user-agent or headers (natural fingerprint)
- Headed by default (headless is more detectable)

## Claude Code Integration

Add the skill to your project:

```bash
cp -r skills/patchright-cli ~/.claude/skills/
```

Then use in Claude Code:
```
patchright-cli open https://protected-site.com
patchright-cli snapshot
patchright-cli fill e3 "username"
patchright-cli fill e4 "password"
patchright-cli click e5
```

## License

MIT
