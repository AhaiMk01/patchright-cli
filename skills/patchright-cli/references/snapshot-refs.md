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

## `find` — searching the snapshot

```
find <text>              Substring match on the accessible name, case-insensitive
find --regex <pattern>   Regex match; bare patterns are case-insensitive
find --regex "/p/ims"    Slash form; supported flags are i, m, s
find --all               Search every node, not just interactive roles + heading
find --limit=N           Cap rendered hits (default 20)
```

### What gets searched

By default, roles in `SEARCHABLE_ROLES` — every interactive role plus
`heading`. This is deliberate: role filtering is the single biggest lever on
output size. Measured across 13 queries on four real pages it cut hits 4.3x
(729 to 168); on one Wikipedia query, 442 hits became 53.

Matching runs against a node's accessible name. For unnamed nodes such as
`- text: Star 95.3k`, the value after the colon is used instead. Property
lines like `- /url: ...` are not nodes and never match.

### Output

Per hit: a `  # ancestor > ancestor > parent` breadcrumb (3 deep, names cut at
45 characters, repeated names collapsed), then the matched node with its ref,
then its subtree.

`find` prints inline and writes no `.yml` file. `snapshot` still writes one.

### Divergence from playwright-cli

playwright-cli's `find` searches all nodes and returns grep-style +/-3 lines of
context. This one defaults to interactive roles and returns the matched
subtree with a breadcrumb. On identical queries the measured output was 13KB
here against 37KB there. `--all` recovers playwright-cli's broader search.

### When hits are truncated

`Found 20 of 340 matches` means the query is too broad. Narrow it — a longer
substring, or a regex anchored with `\b`. Raising `--limit` just buys more
tokens for the same ambiguity.
