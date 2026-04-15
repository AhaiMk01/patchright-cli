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

## Persistent profiles

Use `--persistent` to keep cookies, localStorage, and browser data across sessions:

```bash
patchright-cli open https://example.com --persistent
# ... log in, do work ...
patchright-cli close
# Later:
patchright-cli open https://example.com --persistent
# Cookies and storage are restored
```

Profiles are stored at `~/.patchright-cli/profiles/<session-name>`. Use `--profile=/path` to specify a custom profile directory:

```bash
patchright-cli open --persistent --profile=/path/to/my/profile https://example.com
```

## CDP attach

Connect to an already-running Chrome instance via Chrome DevTools Protocol instead of launching a new one:

```bash
patchright-cli attach --cdp=http://localhost:9222
```

This is useful for debugging or controlling a browser you launched manually with `--remote-debugging-port`.

## Session commands

```bash
patchright-cli list                        # List all active sessions
patchright-cli close                       # Close current session gracefully
patchright-cli close-all                   # Close all sessions gracefully
patchright-cli kill-all                    # Force-kill all sessions + stop daemon
patchright-cli delete-data                 # Delete default session's profile data
patchright-cli -s=mysession delete-data    # Delete named session's profile data
```

## Concurrent sessions

You can run multiple sessions simultaneously for parallel workflows:

```bash
patchright-cli -s=scrape1 open https://site1.com --persistent
patchright-cli -s=scrape2 open https://site2.com --persistent
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

The daemon auto-starts on the first command and auto-shuts down after 5 minutes of inactivity. If it ever crashes, the next command will respawn it automatically.

## Cleanup best practices

- Use `close` when done with a session to free resources
- Use `close-all` at the end of a multi-session workflow
- Use `delete-data` to remove persistent profile data you no longer need
- Use `kill-all` as a last resort if sessions are stuck
- Persistent profiles accumulate data over time -- periodically clean up unused ones
