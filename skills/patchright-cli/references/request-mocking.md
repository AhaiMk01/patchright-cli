# Request Mocking

## Route command

Intercept and modify network requests matching a URL pattern:

```bash
patchright-cli route "<pattern>" [options]
```

The pattern uses glob syntax (e.g., `**/*.jpg`, `https://api.example.com/**`).

### Options

| Flag | Effect |
|------|--------|
| `--status=N` | Respond with HTTP status code N |
| `--body=<string>` | Respond with this body content |
| `--content-type=<mime>` | Set the Content-Type header |
| `--header=Name:Value` | Add or override a response header |
| `--remove-header=Name` | Remove a response header |

### Examples

```bash
# Block all images (respond with 404)
patchright-cli route "**/*.jpg" --status=404

# Mock an API endpoint
patchright-cli route "https://api.example.com/**" --body='{"mock": true}' --content-type=application/json

# Add a custom header to all responses
patchright-cli route "**/*" --header=X-Custom:value

# Remove a header from responses
patchright-cli route "**/*" --remove-header=Content-Type

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
patchright-cli console                     # Show console messages (last 50)
patchright-cli console warning             # Filter by level
patchright-cli console --clear             # Clear buffer after printing

patchright-cli network                     # Show requests (excludes static assets)
patchright-cli network --static            # Include images, fonts, scripts, etc.
patchright-cli network --clear             # Clear log after printing
```
