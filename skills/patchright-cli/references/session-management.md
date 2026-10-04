# Browser Session Management

## Named sessions

By default, patchright-cli uses a session named `"default"`. Use `-s=name` to create or switch to a named session:

```bash
patchright-cli -s=session1 open https://site-a.com
patchright-cli -s=session2 open https://site-b.com
```

Each named session has its own independent browser context -- separate tabs, cookies, storage, and history.

### Default session via environment variable

Set `PATCHRIGHT_CLI_SESSION` to avoid passing `-s` on every command:

```bash
export PATCHRIGHT_CLI_SESSION=myproject
patchright-cli open https://example.com    # Uses "myproject" session
patchright-cli snapshot                    # Same session
```

## Profiles

Every session you `open` runs on a **profile**: a persistent store of cookies,
storage, logins and history. (Attached sessions use the browser's own data.) Profiles always persist -- close the session, open it
again later, and you are still logged in. (`--persistent` is accepted but
changes nothing.)

By default the profile is named after the session. `--profile=<name>` picks a
named profile instead, so any session can use it:

```bash
patchright-cli -s=agent1 open --profile=work https://app.example.com
# ... log in once, then close ...
patchright-cli -s=agent1 close
patchright-cli -s=agent2 open --profile=work https://app.example.com   # still logged in
```

- A value containing `/` or `\` is a directory instead of a name:
  `--profile=/path/to/dir`, `--profile=./browser` (relative to where you run
  the command). Names use letters, digits, `.`, `_`, `-`.
- A session name that isn't a valid profile name (e.g. has spaces) needs an
  explicit `--profile`.
- One session per profile at a time -- Chrome locks it. A second session asking
  for a busy profile is refused; run extra agents as `--tab`s on the session that
  holds it.
- `profile-list` shows every profile, its size, and which session uses it.
- `profile-delete <name>` deletes an idle named profile (never a path).
  `delete-data` deletes only the profile named after the session; for a
  shared profile use `profile-delete`.
- Profiles live at `~/.patchright-cli/profiles/<name>`.

## CDP attach

Connect to an already-running Chrome instance via Chrome DevTools Protocol instead of launching a new one:

```bash
patchright-cli attach --cdp=http://localhost:9222
```

This is useful for debugging or controlling a browser you launched manually with `--remote-debugging-port`.

By default `attach` opens a fresh Isolated Context inside that browser, so it
does not see the browser's existing cookies, localStorage or logins. To work in
the Host Context instead -- the browser's own context, e.g. a Chrome started
with a persistent `--user-data-dir` -- pass `--context=host`:

```bash
patchright-cli attach --cdp=http://localhost:9222 --context=host
```

With `--context=host`:

- Emulation flags (`--device`, `--mobile`, `--viewport-size`, `--locale`,
  `--timezone`, `--geolocation`, `--user-agent`, `--grant-permissions`) are
  rejected -- the Host Context's settings are fixed. The same keys in a config
  file are ignored for this attach.
- Only one session may use a given Host Context, however its endpoint is
  spelled. Run extra agents as `--tab`s on that session, or attach them with
  `--context=new`.
- The session only closes Pages it opened (`--tab`s, `tab-new`, and popups
  they spawn). Plain `close` and `delete-data` are refused, and `tab-close`
  refuses the Host's own tabs: use `detach`. `detach`, `close-all` and
  `kill-all` close your Pages and leave the Host's tabs and state untouched.
- If the Host restarts Chrome, re-run the same `attach`: the stale session is
  replaced automatically and the output says so.

## Session commands

```bash
patchright-cli list                        # List all active sessions
patchright-cli close                       # Close current session gracefully
patchright-cli close-all                   # Close all sessions gracefully
patchright-cli kill-all                    # Force-kill all sessions + stop daemon
patchright-cli delete-data                 # Delete default session's profile data
patchright-cli -s=mysession delete-data    # Delete named session's profile data
```

## Concurrent tabs (shared identity)

`--tab <name>` is the cheaper unit of concurrency: one browser, one login, but
a page, ref registry and navigation history per caller.

```bash
patchright-cli --tab orders-audit open https://app.example.com/a
patchright-cli --tab user-export open https://app.example.com/b
patchright-cli tab-list                      # labelled by owning tab
patchright-cli --tab orders-audit close      # frees one tab only
```

`close` releases the tab it is addressed to and the session ends when the last
tab closes, so concurrent agents clean up without coordinating. A session opened
only through `--tab` has no default tab in use, so closing the named tabs ends
it; one opened with a plain `open` keeps its default tab until that is closed
too. A command
addressed to a tab that was never opened is an error rather than a silent
fallback to the default tab -- a typo in a tab name should not quietly drive
someone else's page.

Named tabs are pinned to their own page, so `tab-select` and popups cannot move
them; `tab-select` addressed to a named tab is refused rather than silently
moving somebody else's page. The default tab still follows the index-based
`tab-select`.

## Concurrent sessions

You can run multiple sessions simultaneously for parallel workflows:

```bash
patchright-cli -s=scrape1 open https://site1.com
patchright-cli -s=scrape2 open https://site2.com
# Work with both independently
patchright-cli -s=scrape1 snapshot
patchright-cli -s=scrape2 snapshot
```

## Dashboard

The `show` command starts a local web dashboard that streams live screenshots of all active sessions:

```bash
patchright-cli show                        # Open at http://127.0.0.1:9322
patchright-cli show --show-port=9400       # Custom port
```

## Granting permissions

Grant browser permissions (geolocation, camera, etc.) to a running session:

```bash
patchright-cli grant-permissions geolocation,camera
patchright-cli grant-permissions notifications --origin=https://example.com
```

You can also grant permissions at launch with `--grant-permissions=geolocation,camera`.

## Daemon behavior

The daemon auto-starts on the first command and auto-shuts down after 30 minutes of inactivity
(`--timeout=<seconds>` after the command, or `$PATCHRIGHT_CLI_IDLE_TIMEOUT` up front). A command
that is still running does not count as inactivity, so a long `show --annotate` will not be cut
short. If the daemon ever crashes, the next command respawns it automatically.

## Cleanup best practices

- Use `close` when done with a session to free resources
- Use `close-all` at the end of a multi-session workflow
- Use `delete-data` to remove persistent profile data you no longer need
- Use `kill-all` as a last resort if sessions are stuck
- Persistent profiles accumulate data over time -- periodically clean up unused ones
