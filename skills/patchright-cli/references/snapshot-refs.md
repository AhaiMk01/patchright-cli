# Snapshots and Element Refs

## How the snapshot system works

patchright-cli uses Playwright's accessibility tree to build a YAML snapshot of all interactive elements on the page. Each element gets a short ref like `e1`, `e5`, `e12`. You use these refs to target elements in commands like `click`, `fill`, `hover`, etc.

```
open browser -> snapshot -> read refs -> interact -> snapshot again -> repeat
```

Refs are **ephemeral** -- they change every time the page updates. After navigation, form submission, or any dynamic content change, you must re-snapshot to get fresh refs.

## Snapshot commands

```bash
patchright-cli snapshot                        # Full page snapshot
patchright-cli snapshot e3                     # Subtree rooted at ref e3
patchright-cli snapshot --depth=2              # Limit tree depth (reduces noise)
patchright-cli snapshot --filename=snap.yml    # Save to custom filename
patchright-cli snapshot -i                     # Interactive-only elements
patchright-cli snapshot --interactive          # Same as -i
```

### Options

| Flag | Effect |
|------|--------|
| `[ref]` | Show only the subtree rooted at the given ref |
| `--depth=N` | Limit depth of the tree (useful for complex pages) |
| `-i` / `--interactive` | Show only interactive elements (buttons, links, inputs) |
| `--filename=F` | Save snapshot to a custom file path |

## When to re-snapshot

Re-snapshot whenever the page state has changed and you need to interact with elements:

- After `goto`, `go-back`, `go-forward`, `reload`
- After `click` that triggers navigation or DOM changes
- After form submission
- After `wait` / `wait-for` for dynamic content
- After `eval` / `run-code` that modifies the DOM

Most state-changing commands (click, fill, goto, etc.) **auto-snapshot** and return the new snapshot. You only need to manually run `snapshot` when the page changes asynchronously or you need a subtree/filtered view.

## Reading the snapshot

The snapshot is a YAML tree. Each element shows:
- Its **ref** (e.g., `e5`)
- Its **role** (button, link, textbox, heading, etc.)
- Its **name** or text content
- Child elements nested underneath

Example output:
```yaml
- heading "Welcome" [e1]
  - link "Sign in" [e2]
- navigation [e3]
  - link "Home" [e4]
  - link "About" [e5]
- main [e6]
  - textbox "Search" [e7]
  - button "Go" [e8]
```

## Troubleshooting

### "Could not locate element for ref"
The page has changed since the last snapshot. Run `snapshot` to get fresh refs and retry.

### Element not visible or not interactive
Some elements are hidden or overlapped. Try:
- `scroll-to <ref>` to bring the element into view
- `wait-for <ref>` to wait until the element appears
- `snapshot -i` to see only interactive elements
- `snapshot <ref>` to inspect a subtree for nested elements

### Too many elements in snapshot
Use `--depth=N` to limit tree depth, or snapshot a specific subtree with `snapshot <ref>`. The `-i` flag also helps by filtering to interactive elements only.

### Stale refs after dynamic content
SPAs and pages with AJAX calls update the DOM without navigation. After triggering dynamic content (e.g., clicking a "Load more" button), wait briefly and re-snapshot:
```bash
patchright-cli click e5          # Triggers dynamic load
patchright-cli wait 1000         # Wait for content
patchright-cli snapshot          # Get fresh refs
```
