# Snapshots and Element Refs

## How the snapshot system works

patchright-cli takes Playwright's AI-mode accessibility snapshot of the whole
page, iframes included, and saves it as YAML. Elements carry Playwright's own
refs, like `e5` or `f3e5`. You use these refs to target elements in commands
like `click`, `fill`, `hover`, etc.

```
open browser -> snapshot -> read refs -> interact -> snapshot again -> repeat
```

## What a ref is

- A ref names **one element** for as long as that element stays on the page.
  Taking another snapshot, or acting, does not renumber it; new elements get
  new numbers.
- `e5` is an element of the tab's first page. After a navigation (`goto`, a
  link, `reload`) the page's refs gain a frame prefix, `f3e5`, and refs inside
  an iframe always have one. Copy refs exactly as printed.
- A ref whose element was removed or replaced fails at once:
  `Ref e7 no longer matches an element (removed or changed since the snapshot). Run snapshot.`
  A ref from a page the tab has left fails with `is from a page this tab has left`.
  Nothing falls back to "an element with the same role and name", so a stale
  ref never clicks the wrong element.
- Refs are **per tab**. A ref that is not in this tab's last snapshot is refused:
  `e2 is not from a snapshot of this tab; run snapshot (...)`. An element whose role
  or name changes (a button relabelled "Saved") gets a new ref, so the old one is refused too.
  After `tab-select`, use the snapshot it prints. A click that opens a popup makes
  the popup current, and the popup's first-document refs (`e1`, `e2`, ...) can reuse
  numbers from the opener, so use the snapshot printed after the click.
- `e5`, `@e5`, `ref=e5` and `[ref=e5]` all work. Anything else is a usage error (exit 2).

## Snapshot commands

```bash
patchright-cli snapshot                        # Full page snapshot
patchright-cli snapshot e3                     # Subtree rooted at ref e3
patchright-cli snapshot --selector="#main"     # Subtree under a CSS selector (exactly one match)
patchright-cli snapshot --depth=2              # Levels 0..2 only
patchright-cli snapshot -i                     # Interactive elements only
patchright-cli snapshot --boxes                # [box=x,y,w,h] on each element
patchright-cli snapshot --filename=snap.yml    # Save to custom filename
```

| Flag | Effect |
|------|--------|
| `[ref]` | Show only the subtree rooted at the given ref |
| `--selector=<css>` | Show only the subtree under a CSS selector. Must match exactly one element that is in the accessibility tree |
| `--depth=N` | Show levels 0..N. A node whose children were cut ends in `# +N refs` (or `# +more`) |
| `-i` / `--interactive` | Interactive elements, clickable `generic [cursor=pointer]` nodes, a combobox's options, and the named landmarks and iframes around them. `text:` and `/url:` lines are dropped |
| `--boxes` | Append `[box=x,y,w,h]`: viewport coordinates in CSS pixels, converted to page coordinates inside iframes |
| `--filename=F` | Save snapshot to a custom file path |

Every view is cut from one full-page snapshot, so the refs it shows are the
page's refs: a ref from `snapshot e3`, `--depth` or `-i` works with every
command, and refs outside the printed part keep working too.

## When to re-snapshot

Most state-changing commands (click, fill, goto, etc.) **auto-snapshot** and
return the new snapshot. Re-snapshot yourself when the page changes on its own
(timers, AJAX), after `eval` / `run-code` that changes the DOM, or when a ref
reports `no longer matches an element`.

## Reading the snapshot

```yaml
- heading "Welcome" [level=1] [ref=e2]
- navigation "Main" [ref=e3]:
  - link "Home" [ref=e4]:
    - /url: /
- main [ref=e6]:
  - textbox "Search" [ref=e7]
  - button "Go" [ref=e8]
  - generic [ref=e9] [cursor=pointer]: Open card
  - combobox "Size" [ref=e10]:
    - option "Small" [selected]
    - option "Large"
  - iframe [ref=e11]:
    - button "Pay" [ref=f2e4]
```

- `[active]` marks the focused element, `[cursor=pointer]` a node that looks
  clickable though its role does not say so.
- Wrapper `generic` nodes with nothing of their own are not printed; their
  children move up a level.
- `text:` lines and `option`s have no ref. Select an option by its label:
  `select e10 Large`.

## Troubleshooting

### Element not visible or not interactive
- `scroll-to <ref>` to bring the element into view
- `wait-for <ref>` to wait until it is visible (`--state=hidden` to wait until it goes)
- `snapshot <ref>` to inspect a subtree

### Too many elements in snapshot
Use `find`, `--depth=N`, `-i`, or `snapshot <ref>`.

### Elements that appear later
```bash
patchright-cli click e5          # Triggers a dynamic load
patchright-cli wait 1000         # Wait for content
patchright-cli snapshot          # Refs for the new elements
```

## `find` — searching the snapshot

```
find <text>              Substring match on the accessible name, case-insensitive
find --regex <pattern>   Regex match; bare patterns are case-insensitive
find --regex "/p/ims"    Slash form; supported flags are i, m, s
find --all               Search every node, not just interactive roles + heading
find --limit=N           Cap rendered hits (default 20)
find --role=<role>       Elements get_by_role matches (--name, --description, --exact)
```

### What gets searched

By default, every interactive role plus `heading`. This is deliberate: role
filtering is the single biggest lever on output size. Measured across 13
queries on four real pages it cut hits 4.3x (729 to 168); on one Wikipedia
query, 442 hits became 53.

Matching runs against a node's accessible name. For unnamed nodes such as
`- text: Star 95.3k`, the value after the colon is used instead. Property
lines like `- /url: ...` are not nodes and never match.

### Output

Per hit: a `  # ancestor > ancestor > parent` breadcrumb (3 deep, names cut at
45 characters, repeated names collapsed), then the matched node with its ref,
then its subtree. A hit without a ref of its own (an `option`, or a `text:`
line under `--all`) shows the nearest element you can act on as `[in=e12]`.

`find` prints inline and writes no `.yml` file. `snapshot` still writes one.

### Divergence from playwright-cli

playwright-cli's `find` searches all nodes and returns grep-style +/-3 lines of
context. This one defaults to interactive roles and returns the matched
subtree with a breadcrumb. Across 13 queries on four real pages, total output
was 13KB here against 37KB there (a single lookup is a few hundred bytes). `--all` widens the search to every node like
playwright-cli's does, but each hit still renders as a full subtree rather
than +/-3 lines of context — on content-heavy pages a matching container can
print its entire subtree, plus its matching descendants again as their own
hits (Hacker News `find "comments"` returned 45% of the page this way).

### When hits are truncated

`Found 20 of 340 matches` means the query is too broad. Narrow it — a longer
substring, or a regex anchored with `\b`. Raising `--limit` just buys more
tokens for the same ambiguity.
