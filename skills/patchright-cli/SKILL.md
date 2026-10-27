---
name: patchright-cli
description: Anti-detect browser automation using Patchright (undetected Playwright fork). Use when you need to interact with websites that block regular Playwright/Chrome DevTools, such as Akamai/Cloudflare-protected sites. Provides the same command interface as playwright-cli but bypasses bot detection. Use this skill whenever the user asks to automate a browser, scrape a website, fill forms, log into sites, take screenshots, or do anything involving Chrome automation — especially if the target site has bot protection like Cloudflare or Akamai.
---

# Anti-Detect Browser Automation with patchright-cli

patchright-cli drives a real Chrome browser that passes bot detection (Cloudflare, Akamai, etc.). It works as a CLI: you issue commands, get back page state. This is the tool to reach for whenever regular Playwright or Chrome DevTools gets blocked.

## Quick start

If `patchright-cli` is not on PATH yet, install it first -- one of:

```bash
uvx patchright-cli --version                 # run without installing
pip install patchright-cli                   # or install it
python -m patchright install chromium        # then fetch the browser, once
```

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

## Shell quoting

Quote any URL containing `&`. Unquoted, the shell eats it before patchright-cli
sees it:

```bash
# WRONG -- PowerShell 7 treats `&` as the background operator: the command is
# detached and everything after `&` is run as a separate command.
# cmd.exe treats it as a command separator. Either way the URL is truncated.
patchright-cli goto https://example.com/search?q=cats&page=2

# RIGHT -- on any shell
patchright-cli goto "https://example.com/search?q=cats&page=2"
```

The same applies to `#` (a bash comment), spaces, and `%` (expanded by
cmd.exe), and to any argument holding shell metacharacters. Quoting works for
the installed `patchright-cli.exe` in bash, PowerShell and cmd.exe; leave it on.

On Windows, two cases need more than quotes:

```
# cmd.exe without quotes: escape the & as ^&. Inside quotes ^ is kept
# literally, so use one or the other, never both.
patchright-cli goto https://example.com/search?q=cats^&page=2

# PowerShell calling a .cmd/.bat wrapper (such as the repo's patchright-cli.cmd):
# PowerShell drops the quotes and cmd.exe splits at &. Pass the rest verbatim
# with --%. Don't use --% with patchright-cli.exe: in PowerShell 7 the quotes end up in the URL.
.\patchright-cli.cmd --% goto "https://example.com/search?q=cats&page=2"
```

## Global options

These go before the command:

```bash
--headless              # Run headless (default: headed -- headed is less detectable)
--profile=<name>        # Named profile any session can use (default: the session name);
                        # profiles always persist. A value with / or \ is a directory
--proxy=<url>           # Proxy server (http, https, socks5) -- supports user:pass@host auth
-s=mysession            # Named session (default: "default", or PATCHRIGHT_CLI_SESSION env var)
--port=9322             # Custom daemon port (default: 9321)
--config=<path>         # Load options from JSON config file
--cdp=<url>             # Attach to Chrome via CDP endpoint (use with `attach` command)
--context=new|host      # With `attach`: fresh isolated context (default) or the browser's own
--device="iPhone 15"    # Emulate a device
--mobile                # Emulate a generic mobile device (Pixel 7); mobile pages
                        # are lighter, so snapshots are smaller and cheaper
--viewport-size=1280x720 # Set viewport size
--locale=en-US          # Browser locale
--timezone=America/New_York # Timezone ID
--geolocation=40.7,-74.0   # Geolocation override (lat,lon)
--user-agent=<ua>       # Custom user agent string
--grant-permissions=geolocation,camera  # Grant permissions at launch
--show-port=9322        # Dashboard port (default: 9322)
--raw                   # Strip page/snapshot decorations from output
--json                  # Wrap full response as JSON ({success, output, ...})
--toon                  # TOON for results with structured data (list, tab-list, cookie-list,
                        # find, console, network, url, title, ...); others print as usual
```

These go *after* the command instead:

```bash
--timeout=1800          # Daemon idle timeout in seconds before it exits and
                        # takes the browser (and its login) with it. Default 1800.
                        # Also settable up front via $PATCHRIGHT_CLI_IDLE_TIMEOUT.
--timeout-action=10000  # Default action timeout (ms)
--timeout-navigation=30000 # Default navigation timeout (ms)
```

---

## Core commands

### Browser lifecycle

```bash
open [url]                    # Launch browser (optionally navigate)
open --profile=work           # Run on a named profile (logins persist; one session at a time)
open --headless               # Headless mode
attach --cdp=<url>            # Attach to existing Chrome via CDP (fresh, isolated context)
attach --cdp=<url> --context=host  # Use the Host Context: the browser's own logins, cookies, storage
close                         # Close session (closes the browser); refused on --context=host -- use detach
detach                        # Detach an attached session (keeps external browser running)
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
snapshot --selector="#main"   # Subtree of a CSS selector -- must match exactly
                              # one element. No ref needed, so
                              # this works before the first snapshot
snapshot --depth=N            # Limit depth
snapshot -i                   # Interactive elements only
snapshot --boxes              # Append [box=x,y,w,h] to each [ref=eN] line
```

### Searching instead of snapshotting

On a large page, `find` is the cheaper first move — a full snapshot of a
Wikipedia article is ~87k tokens; `find` returns a few hundred.

```bash
patchright-cli find "Sign in"              # substring, case-insensitive
patchright-cli find --regex "sign (in|up)" # regex, case-insensitive
patchright-cli find --regex "/Sign In/"    # slash form carries flags (i, m, s)
patchright-cli find "price" --all          # include text/paragraph nodes
patchright-cli find "hide" --limit=50      # default limit is 20
```

`find` searches links, buttons, inputs and headings by default — the things
you can act on. Use `--all` when you are looking for page *content* rather
than a control.

Each hit prints a `#` breadcrumb showing where it sits, so identical
controls stay distinguishable:

```
  # table > rowgroup > cell "407 points by pluc 4 hours ago | hide | 321 ..."
- link "hide": [ref=e49]
  - /url: hide?id=49489982
```

Refs from `find` are numbered across the whole page and are immediately
usable with `click`, `fill`, and the rest. They stay valid until the next
`snapshot` or `find`.

When output says `Found 20 of 340 matches`, narrow the query rather than
raising `--limit`.

### Interaction

```bash
click <ref>                   # Left-click (smart: navigates links directly)
click <ref> right             # Right-click
click <ref> --modifiers=Alt   # Click with modifier keys
dblclick <ref>                # Double-click
hover <ref>                   # Hover
drag <ref> <target-ref>       # Drag element to target
drop <ref> --path=./file.png  # Drop file onto element (HTML5 drop from outside)
drop <ref> --data="text/plain=hello"  # Drop arbitrary MIME data
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
wait --url="**/dashboard"     # Wait until the URL matches -- the right tool
                              # after a login or any redirect. `*` does NOT
                              # cross a `/`, so use `**`; `/regex/i` also works
wait-for <ref>                # Wait until element visible
wait-for <ref> --state=hidden # Wait until hidden
press <key>                   # Keypress (Enter, ArrowDown, etc.)
keydown <key> / keyup <key>   # Hold/release key
```

### Tabs

```bash
tab-list                      # List open tabs
tab-new <url>                 # Open new tab
tab-select <index>            # Switch to tab (default tab only; a --tab
                              # caller is pinned to its own page)
tab-close [index]             # Close tab (with --context=host, only tabs this session opened)
```

### Parallel agents: --tab

`--tab <name>` gives a caller its own page, its own refs and its own history
inside one shared browser -- same fingerprint, same cookies, same login. It is
the cheap way to parallelise: one Chrome total, not one per agent.

```bash
patchright-cli --tab inbox-scan open https://app.example.com/inbox
patchright-cli --tab report-pull open https://app.example.com/reports
patchright-cli --tab inbox-scan snapshot     # refs belong to this tab only
patchright-cli tab-list                      # every tab, labelled by owner
patchright-cli --tab inbox-scan close        # frees this tab; browser stays
```

**Choose the name once, with a shell-generated suffix.** Nothing enforces
uniqueness -- two agents on the same name share one page and clobber each
other. Do not invent the random part yourself; LLM-chosen "random" characters
collide.

```bash
TAB="price-scan-$(openssl rand -hex 2)" && echo "$TAB"
patchright-cli --tab "$TAB" open https://example.com
# reuse that exact printed name in every later command
```

`$PATCHRIGHT_CLI_TAB` sets it for a whole shell, the way `$PATCHRIGHT_CLI_SESSION`
does for sessions.

Cleanup needs no coordination: every agent runs `close` addressed to its own
tab, and the browser exits when the last tab goes. On an `attach --context=host`
session the host's browser never exits: closing tabs leaves it attached, and
whoever attached runs `detach` at the end.

**`--tab` vs `-s`.** `--tab` shares identity, one browser (~50-150MB per extra
tab), for agents acting as the same logged-in user. `-s` isolates identity --
its own profile, cookies and fingerprint, a full Chrome each (~300-500MB) --
for multi-account work. Tabs share one browser process, so commands from
different tabs queue behind a slow navigation; separate sessions run fully in
parallel.

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
network                       # Show network requests (each line is `#<id> METHOD STATUS URL`)
requests                      # Alias for `network`
network --static              # Include static assets
request <id>                  # Full detail for one request (headers + body)
request <id> --body           # Also fetch response body
generate-locator <ref>        # Emit Playwright locator string for a ref
highlight <ref>               # Persistent overlay on element [--style=CSS]
highlight <ref> --hide        # Remove overlay; `highlight --hide` clears all
```

### Recording and capture

```bash
screenshot / pdf              # Static capture
video-start / video-stop      # Video recording
video-show-actions            # Label each action in the recording [--duration=ms] [--position=P]
video-hide-actions            # Stop labelling actions
tracing-start / tracing-stop  # Playwright trace
codegen / codegen-stop        # Record interactions as script
resize <w> <h>                # Viewport resize
upload <file> [ref]           # File upload
show                          # Live dashboard
show --annotate               # Ask a human to mark up the page and wait for it
```

### Emulation

Each setting holds for the whole session: every open tab, and tabs and popups
opened later (a popup gets it just after it opens). `clear-*` drops just that
one, back to the browser default.

```bash
set-color-scheme dark         # dark | light | no-preference
clear-color-scheme
set-reduced-motion reduce     # reduce | no-preference
clear-reduced-motion
set-forced-colors active      # active | none
clear-forced-colors
set-contrast more             # more | no-preference
clear-contrast
set-media print               # screen | print
clear-media
```

### Network mocking

`route "<pattern>"` mocks matching requests (`--status`, `--body`,
`--content-type`, `--header`); `route-list` and `unroute` manage them, and
`network-state-set offline|online` simulates connectivity.

## Command reference

Every command, its flags and what it does. `patchright-cli <command> --help`
shows one command's flag defaults and examples; `patchright-cli` with no
arguments shows whether the daemon is up and which sessions are open.

<!-- BEGIN GENERATED: commands (scripts/gen_skill.py from COMMAND_SPECS; do not edit by hand) -->
```bash
# Core
open [url] [--block-networks] [--headed]  # Open browser (starts daemon if needed)
attach --cdp=<url> [--cdp-headers] [--cdp-timeout] [--block-networks]  # Attach to running Chrome via CDP
goto <url>                              # Navigate to URL
click <ref> [button] [--modifiers]      # Click element
dblclick <ref> [button] [--modifiers]   # Double-click element
fill <ref> <value> [--submit]           # Fill text into element
type <text> [--submit]                  # Type text via keyboard
hover <ref>                             # Hover over element
select <ref> <value>                    # Select dropdown option
check <ref>                             # Check checkbox/radio
uncheck <ref>                           # Uncheck checkbox/radio
snapshot [ref] [--selector] [--filename] [--depth] [--interactive] [--boxes]  # Take accessibility snapshot
find <text> [--regex] [--all] [--limit]  # Search the snapshot for matching elements
eval <expr> [ref] [--file]              # Evaluate JavaScript (expression, --file or stdin)
text <ref|selector>                     # Get text content of element
screenshot [ref] [--full-page] [--hires] [--filename]  # Save screenshot
drag <from> <to>                        # Drag element to target
drop <ref> [--path] [--data]            # Drop file/data onto element
close                                   # Close browser session (or the --tab it is addressed to)
detach                                  # Detach attached session (keep external browser running)

# Navigation
go-back                                 # Go back
go-forward                              # Go forward
reload                                  # Reload page
url                                     # Print current URL
title                                   # Print page title

# Keyboard
press <key>                             # Press key
keydown <key>                           # Key down
keyup <key>                             # Key up

# Mouse
mousemove <x> <y>                       # Move mouse
mousedown [button]                      # Mouse button down
mouseup [button]                        # Mouse button up
mousewheel <dx> <dy>                    # Scroll mouse wheel

# Scroll & Wait
scroll <dx> <dy>                        # Scroll by pixel offset
scroll-to <ref>                         # Scroll element into view
wait [ms] [--url]                       # Wait for milliseconds, or for a URL
wait-for <ref> [--state]                # Wait for element to reach a state

# Tabs
tab-list                                # List tabs
tab-new [url]                           # Open new tab
tab-close [index]                       # Close tab
tab-select <index>                      # Switch to tab (default tab only)

# Dialog
dialog-accept [text]                    # Accept next dialog
dialog-dismiss                          # Dismiss next dialog

# Upload/Resize
upload <file> [ref]                     # Upload file to input
resize <w> <h>                          # Resize viewport

# Emulation
set-color-scheme <dark|light|no-preference>  # Emulate prefers-color-scheme
clear-color-scheme                      # Stop emulating prefers-color-scheme
set-reduced-motion <reduce|no-preference>  # Emulate prefers-reduced-motion
clear-reduced-motion                    # Stop emulating prefers-reduced-motion
set-forced-colors <active|none>         # Emulate forced-colors
clear-forced-colors                     # Stop emulating forced-colors
set-contrast <more|no-preference>       # Emulate prefers-contrast
clear-contrast                          # Stop emulating prefers-contrast
set-media <screen|print>                # Emulate CSS media type
clear-media                             # Stop emulating the CSS media type

# State
state-save [file]                       # Save cookies+storage to JSON
state-load <file>                       # Load saved state

# Storage
cookie-list [--domain] [--path]         # List cookies
cookie-get <name>                       # Get cookie
cookie-set <name> <value> [--domain] [--path] [--expires] [--httpOnly] [--secure] [--sameSite]  # Set cookie
cookie-delete <name>                    # Delete cookie
cookie-clear                            # Clear all cookies
cookie-import <file>                    # Import cookies from JSON
cookie-export [file]                    # Export cookies to JSON
localstorage-list                       # List localStorage
localstorage-get <key>                  # Get localStorage item
localstorage-set <key> <value>          # Set localStorage item
localstorage-delete <key>               # Delete localStorage item
localstorage-clear                      # Clear localStorage
sessionstorage-list                     # List sessionStorage
sessionstorage-get <key>                # Get sessionStorage item
sessionstorage-set <key> <value>        # Set sessionStorage item
sessionstorage-delete <key>             # Delete sessionStorage item
sessionstorage-clear                    # Clear sessionStorage

# Permissions
grant-permissions <perms> [--origin]    # Grant comma-separated permissions

# Route
route <pattern> [--status] [--body] [--content-type] [--header] [--remove-header]  # Mock requests matching a URL pattern
route-list                              # List active routes
unroute [pattern]                       # Remove route(s)
network-state-set <online|offline>      # Set online/offline

# Code
run-code <code> [--file]                # Run raw JS in page context (code, --file or stdin)

# Tracing
tracing-start                           # Start Playwright tracing
tracing-stop                            # Stop tracing and save

# Video
video-start                             # Start video recording
video-stop [--filename]                 # Stop recording and save
video-chapter <title>                   # Add chapter marker to video
video-show-actions [--duration] [--position]  # Annotate actions in the recording
video-hide-actions                      # Stop annotating actions

# PDF
pdf [--filename]                        # Save page as PDF

# DevTools
console [level] [--clear]               # Show console messages
network [--static] [--clear]            # Show network requests
requests [--static] [--clear]           # Alias for `network`
request <id> [--body]                   # Show full detail for a network request
generate-locator <ref>                  # Emit Playwright locator expression for a ref
highlight <ref> [--style] [--hide]      # Show overlay on element

# Session
list                                    # List sessions
close-all                               # Close all sessions
kill-all                                # Kill all sessions and stop the daemon
delete-data                             # Delete this session's profile

# Profiles
profile-list                            # List profiles, their size and which session uses them
profile-delete <name>                   # Delete a named profile

# Dashboard
show [--annotate] [--wait] [--no-open]  # Open session dashboard

# Codegen
codegen [file]                          # Start recording interactions
codegen-stop [file]                     # Stop recording and save script

# Setup
install --skills | --hooks [--uninstall]  # Install agent skill files, or session-start hooks
mcp [--http] [--host=HOST] [--port=N]   # Start MCP server (stdio by default)
daemon-info --json                      # Show daemon client protocol and token path without starting it
```
<!-- END GENERATED -->

---

## Common patterns

### Login flow

```bash
patchright-cli open --profile=myapp https://example.com/login
patchright-cli snapshot
patchright-cli fill e3 "user@example.com"
patchright-cli fill e5 "password123"
patchright-cli click e8
patchright-cli snapshot
# The login persists in profile "myapp" for any later session that opens it
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
patchright-cli wait --url="**/dashboard"  # Wait for a redirect to land
# For custom conditions, use run-code (see references/running-code.md)
```

---

## Asking a human what to change

`show --annotate` is for design and UI review: it opens a page in the user's
browser showing a live capture of the current page, they draw on it and write
what they want changed, and the command returns their drawing plus their notes.

```bash
patchright-cli show --annotate                  # waits up to 5 minutes
patchright-cli show --annotate --wait=900       # give them longer
patchright-cli show --annotate --no-open        # headless box: do not launch a browser
```

The command **blocks** until they submit, so it opens the review link for them
rather than printing it. The link is also written to
`.patchright-cli/annotate-url.txt`, which is how you get it when `--no-open`
suppressed the browser. You get back the annotated screenshot saved under
`.patchright-cli/`, their notes, and a fresh snapshot of the page -- enough to
act on the feedback without asking follow-up questions.

Reach for it when the decision is aesthetic or subjective and you would
otherwise be guessing: which of two layouts, where an element belongs, whether
spacing reads right. Do not use it for anything you can check yourself.

## Anti-detect notes

- Uses real Chrome (not Chromium) -- this is what makes it undetectable
- Patchright patches `navigator.webdriver` and other detection vectors automatically
- Headed by default -- headless mode is more detectable, use only when necessary
- No custom user-agent or headers by default -- preserves Chrome's natural fingerprint
- Persistent profiles maintain realistic browser history and cookies
- The daemon architecture means Chrome stays running between commands, behaving like a real user's browser
- `attach --cdp` lets you connect to an existing Chrome instance; add `--context=host` to reuse its logins,
  cookies and storage (see references/session-management.md)

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

When an installed copy of this skill drifts from the tool version,
patchright-cli prints a one-line warning on stderr -- once a day per
version, so a chained run is not buried in it. Re-run
`patchright-cli install --skills` to resync, or set
`PATCHRIGHT_CLI_NO_SKILL_CHECK=1` to silence the check.

`open` also checks PyPI once a day for a newer patchright-cli and prints a
one-line note when there is one. Set `PATCHRIGHT_CLI_NO_VERSION_CHECK=1` to
turn that off.

## Specific tasks

* **Snapshots and element refs** [references/snapshot-refs.md](references/snapshot-refs.md)
* **Browser session management** [references/session-management.md](references/session-management.md)
* **Request mocking** [references/request-mocking.md](references/request-mocking.md)
* **Storage state (cookies, localStorage)** [references/storage-state.md](references/storage-state.md)
* **Running JavaScript** [references/running-code.md](references/running-code.md)
* **Tracing** [references/tracing.md](references/tracing.md)
* **Video recording & capture** [references/video-recording.md](references/video-recording.md)
