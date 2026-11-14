# Request Mocking

## Route command

Answer the current tab's requests that match a URL pattern with a made-up response:

```bash
patchright-cli route "<pattern>" [options]
```

The pattern uses glob syntax (e.g., `**/*.jpg`, `https://api.example.com/**`).

A routed request never reaches the server. The reply is built from the flags
alone: status 200, an empty body and `text/plain` unless you say otherwise.
`route` cannot pass a real response through or edit one, so a broad pattern such
as `**/*` replaces every page, script and API call in that tab with an empty
response.

A route covers only the tab it was added in. Other tabs, and tabs opened later,
still reach the server, although `route-list` shows the route from any tab. Add
the route again in each tab that needs it.

### Options

| Flag | Effect |
|------|--------|
| `--status=N` | Status code of the mock response (default: 200) |
| `--body=<string>` | Body of the mock response (default: empty) |
| `--content-type=<mime>` | Its Content-Type (default: `text/plain`). `--header=Content-Type:<mime>` sets it too; giving both is a usage error |
| `--header=Name:Value` | Add a header to the mock response; repeat the flag for more. A name given more than once becomes one header with the values joined by `, ` (so it cannot send two `Set-Cookie` headers) |
| `--remove-header=Name` | Drop a header given with `--header` (comma-separated, any case). It does not remove a Content-Type set with `--content-type`, or any header of a real response |

### Examples

```bash
# Block all images (respond with 404)
patchright-cli route "**/*.jpg" --status=404

# Mock an API endpoint
patchright-cli route "https://api.example.com/**" --body='{"mock": true}' --content-type=application/json

# Mock an endpoint with extra response headers
patchright-cli route "https://api.example.com/me" --body='{"id":1}' --content-type=application/json --header=X-Request-Id:abc --header=Cache-Control:no-store

# Block all CSS (speeds up testing)
patchright-cli route "**/*.css" --status=404

# Mock a specific API response
patchright-cli route "https://api.example.com/users" --body='[{"id":1,"name":"Test"}]' --content-type=application/json --status=200
```

## Managing routes

```bash
patchright-cli route-list                  # List all active routes
patchright-cli unroute "**/*.jpg"           # Remove a specific route
patchright-cli unroute                     # Remove all routes
```

## Network state

Simulate offline/online conditions:

```bash
patchright-cli network-state-set offline   # Simulate offline
patchright-cli network-state-set online    # Restore connectivity
```

## Common patterns

### Block tracking and analytics

```bash
patchright-cli route "**/analytics/**" --status=204
patchright-cli route "**/tracking/**" --status=204
patchright-cli route "**/*.gif?*" --status=204    # Tracking pixels
```

### Mock authentication API

```bash
patchright-cli route "https://api.example.com/auth/login" \
  --body='{"token":"fake-jwt","user":{"id":1}}' \
  --content-type=application/json \
  --status=200
```

### Test error handling

```bash
# Simulate server error on API calls
patchright-cli route "https://api.example.com/**" --status=500 --body='{"error":"Internal Server Error"}'

# Simulate network timeout (offline mode)
patchright-cli network-state-set offline
```

## DevTools inspection

Monitor network activity and console output:

```bash
patchright-cli console                     # First call starts capture; later calls show the last 50 messages
patchright-cli console warning             # Filter by level
patchright-cli console --clear             # Clear buffer after printing (capture stays on)

patchright-cli network                     # Show requests (excludes static assets)
patchright-cli network --static            # Include images, fonts, scripts, etc.
patchright-cli network --clear             # Clear log after printing
```

Console capture starts on the first `console` call, so messages logged before
it are gone: run `console` before reproducing the issue, then again to read.
It covers every page of the session, later ones included. It uses CDP Runtime,
which a page can detect, and stays on until the session closes.
