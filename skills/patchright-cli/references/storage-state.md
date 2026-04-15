# Storage State (Cookies, localStorage)

## Cookies

### List and inspect

```bash
patchright-cli cookie-list                              # All cookies
patchright-cli cookie-list --domain=example.com         # Filter by domain
patchright-cli cookie-list --path=/api                  # Filter by path
patchright-cli cookie-get session_id                    # Get a specific cookie by name
```

### Set and modify

```bash
patchright-cli cookie-set session_id abc123             # Set cookie (uses current page URL)
patchright-cli cookie-set token xyz \
  --domain=example.com \
  --path=/ \
  --httpOnly \
  --secure \
  --sameSite=Lax \
  --expires=1735689600                                  # Full options
```

#### Cookie-set flags

| Flag | Effect |
|------|--------|
| `--domain=D` | Cookie domain |
| `--path=P` | Cookie path |
| `--httpOnly` | HTTP-only flag |
| `--secure` | Secure flag |
| `--sameSite=Strict\|Lax\|None` | SameSite attribute |
| `--expires=UNIX_TS` | Expiration as Unix timestamp |

### Delete

```bash
patchright-cli cookie-delete session_id                 # Delete specific cookie
patchright-cli cookie-clear                             # Clear all cookies
```

### Import / Export

```bash
patchright-cli cookie-export cookies.json               # Export all cookies to JSON
patchright-cli cookie-import cookies.json               # Import cookies from JSON file
```

## localStorage

```bash
patchright-cli localstorage-list                        # List all keys and values
patchright-cli localstorage-get theme                   # Get value by key
patchright-cli localstorage-set theme dark              # Set key-value pair
patchright-cli localstorage-delete theme                # Delete a key
patchright-cli localstorage-clear                       # Clear all localStorage
```

## sessionStorage

Same pattern as localStorage:

```bash
patchright-cli sessionstorage-list
patchright-cli sessionstorage-get step
patchright-cli sessionstorage-set step 3
patchright-cli sessionstorage-delete step
patchright-cli sessionstorage-clear
```

## Save / Load full state

Save or restore both cookies and localStorage in a single file:

```bash
patchright-cli state-save auth.json                     # Save cookies + localStorage
patchright-cli state-load auth.json                     # Restore state
```

When loading state, navigate to the matching origin first so localStorage can be applied correctly.

## Cookie persistence login pattern

Use persistent profiles with cookies for login flows that survive across sessions:

```bash
# First time: log in and save state
patchright-cli open https://app.example.com --persistent
patchright-cli snapshot
patchright-cli fill e3 "user@example.com"
patchright-cli fill e5 "password123"
patchright-cli click e8
patchright-cli state-save auth.json

# Later: restore state without logging in again
patchright-cli open https://app.example.com --persistent
patchright-cli state-load auth.json
patchright-cli goto https://app.example.com/dashboard
```

Or simply use `--persistent` -- cookies and storage are automatically preserved in the profile:

```bash
patchright-cli -s=myapp open https://app.example.com --persistent
# ... log in once ...
patchright-cli -s=myapp close
# Next time:
patchright-cli -s=myapp open https://app.example.com --persistent
# Already logged in
```
