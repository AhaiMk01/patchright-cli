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
  --expires=4102444800                                  # Full options; a past --expires sets nothing lasting
```

#### Cookie-set flags

| Flag | Effect |
|------|--------|
| `--domain=D` | Cookie domain |
| `--path=P` | Cookie path |
| `--httpOnly` | HTTP-only flag |
| `--secure` | Secure flag |
| `--sameSite=Strict\|Lax\|None` | SameSite attribute |
| `--expires=UNIX_TS` | Expiration as Unix timestamp in seconds (default: a session cookie) |

### Delete

```bash
patchright-cli cookie-delete session_id                 # Delete specific cookie
patchright-cli cookie-delete session_id --domain=www.example.com   # Only on that domain
patchright-cli cookie-clear                             # Clear all cookies
patchright-cli cookie-clear --domain=www.example.com    # Clear one domain's cookies
patchright-cli cookie-clear '--domain=/(^|\.)example\.com$/'   # Regex: .example.com and its subdomains
```

`--domain` is the cookie's exact domain as `cookie-list` shows it (`www.example.com` and
`.example.com` are different domains); write `/pattern/` to match several. Other sites'
cookies are left untouched, so this logs one site out of a shared session.

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

`state-load` replaces the session's state: it first clears the existing cookies, localStorage, IndexedDB and OPFS data, then applies the file. localStorage is applied to every origin in the file; you do not need to visit an origin first.

It also deletes the session's virtual passkeys, and when the file holds none it
uninstalls the virtual authenticator. `passkey-export` the ones you need first,
and run a `passkey-*` command again before the page's next WebAuthn call.

## Cookie persistence login pattern

A profile keeps cookies that have an expiry, localStorage and IndexedDB across a
`close`. A session cookie (one with no expiry) ends with the browser, so a login
a site keeps in one is gone after `close`. `state-save` records session cookies
too and `state-load` puts them back: use the pair for such a login, and to carry
any login to another profile or machine:

```bash
# First time: log in and save state
patchright-cli open https://app.example.com
patchright-cli snapshot
patchright-cli fill e3 "user@example.com"
patchright-cli fill e5 "password123"
patchright-cli click e8
patchright-cli state-save auth.json

# Later, in another session: restore state without logging in again
patchright-cli -s=other open https://app.example.com
patchright-cli -s=other state-load auth.json
patchright-cli -s=other goto https://app.example.com/dashboard
```

When the site keeps its login in cookies with an expiry or in storage, the same
session or profile needs no saving (`--persistent` is accepted but has no
effect):

```bash
patchright-cli -s=myapp open https://app.example.com
# ... log in once ...
patchright-cli -s=myapp close
# Next time:
patchright-cli -s=myapp open https://app.example.com
# Still logged in, unless the site keeps the login in a session cookie
```
