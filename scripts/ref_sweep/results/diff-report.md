# Ref sweep: before (RefRegistry) vs after (native aria refs)

212 steps; 106 identical; 106 differ; 0 unexplained.

## Intended: snapshot format (39)

Native AI-mode YAML: Playwright's own refs printed verbatim (`f1e5` after a navigation), bare `generic` wrappers hoisted, `[active]`/`[cursor=pointer]` tags, refs after a node's key instead of after its colon, iframe content included, options and `text:` lines without refs. `--depth` now cuts lines (with `# +N refs`) where it used to only hide tags; `-i` drops non-interactive lines where it used to only hide tags; scoped snapshots show page refs instead of renumbering from e1.

- controls:snapshot (exit 0 -> 0)
- controls:snapshot-i (exit 0 -> 0)
- controls:snapshot-depth2 (exit 0 -> 0)
- controls:snapshot-selector (exit 0 -> 0)
- controls:snapshot-selector-multi (exit 1 -> 1)
- controls:snapshot-selector-none (exit 1 -> 1)
- controls:snapshot-full (exit 0 -> 0)
- controls:snapshot-ref-list (exit 0 -> 0)
- controls:snapshot-ref-list-depth1 (exit 0 -> 0)
- controls:snapshot-again (exit 0 -> 0)
- controls:snapshot-selector-scope (exit 0 -> 0)
- controls:snapshot-ref-scope (exit 0 -> 0)
- controls:snapshot-depth-markers (exit 0 -> 0)
- controls:snapshot-i-again (exit 0 -> 0)
- nested:snapshot (exit 0 -> 0)
- nested:snapshot-depth1 (exit 0 -> 0)
- nested:snapshot-depth3-i (exit 0 -> 0)
- nested:snapshot-sub (exit 0 -> 0)
- iframe:snapshot (exit 0 -> 0)
- iframe:snapshot-i (exit 0 -> 0)
- iframe:snapshot-boxes (exit 0 -> 0)
- iframe:snapshot-i#2 (exit 0 -> 0)
- shadow:snapshot (exit 0 -> 0)
- dialog:snapshot-closed (exit 0 -> 0)
- dialog:snapshot-open (exit 0 -> 0)
- dialog:snapshot-after (exit 0 -> 0)
- delayed:snapshot-early (exit 0 -> 0)
- delayed:snapshot-late (exit 0 -> 0)
- delayed:snapshot-gone (exit 0 -> 0)
- offscreen:snapshot (exit 0 -> 0)
- offscreen:snapshot-boxes (exit 0 -> 0)
- offscreen:snapshot-boxes-2 (exit 0 -> 0)
- large:snapshot (exit 0 -> 0)
- large:snapshot-i (exit 0 -> 0)
- large:snapshot-boxes (exit 0 -> 0)
- nav:snapshot (exit 0 -> 0)
- nav:snapshot-2 (exit 0 -> 0)
- tabs:snapshot-a (exit 0 -> 0)
- tabs:snapshot-b (exit 0 -> 0)

## Intended: find output (18)

Same search over the new YAML: refs are the page's refs (`f1e28`), ref-less hits show `[in=<ref>]`, and iframe content is searchable. find --role maps matches through a depth-0 probe (same matches, native refs).

- controls:find-save (exit 0 -> 0)
- controls:find-limit (exit 0 -> 0)
- controls:find-regex (exit 0 -> 0)
- controls:find-regex-flag (exit 0 -> 0)
- controls:find-all (exit 0 -> 0)
- controls:find-role-save (exit 0 -> 0)
- controls:find-role-regex (exit 0 -> 0)
- controls:find-role-link (exit 0 -> 0)
- controls:find-role-exact (exit 0 -> 0)
- nested:find-green (exit 0 -> 0)
- iframe:find-submit (exit 0 -> 0)
- iframe:find-role-submit (exit 0 -> 0)
- iframe:find-inner-check (exit 0 -> 0)
- shadow:find-deep (exit 0 -> 0)
- shadow:find-role (exit 0 -> 0)
- large:find-buy1 (exit 0 -> 0)
- large:find-buy1-limit5 (exit 0 -> 0)
- large:find-regex (exit 0 -> 0)

## Intended: --boxes no longer times out (1)

Old --boxes resolved every ref with a 30 s bounding_box and timed out (controls page > 90 s); native boxes=True takes ~0.2 s, iframe boxes shifted to page coordinates (iframe:snapshot-boxes f4e4 [box=332,91,58,21] equals the page box measured in iframe:inner-submit-page-box).

- controls:snapshot-boxes (exit -1 -> 0)

## Intended: iframe elements are actionable (10)

The old snapshot had no iframe content, so no ref existed (`missing_targets`); native refs (`f4e3`) reach into the same-origin iframe.

- iframe:click-inner (exit 1 -> 0)
- iframe:fill-inner (exit 1 -> 0)
- iframe:check-inner (exit 1 -> 0)
- iframe:eval-inner (exit 1 -> 0)
- iframe:text-inner (exit 1 -> 0)
- iframe:highlight-inner (exit 1 -> 0)
- iframe:screenshot-inner (exit 1 -> 0)
- iframe:generate-locator-inner (exit 1 -> 0)
- iframe:html-inner (exit 1 -> 0)
- iframe:snapshot-iframe-ref (exit 1 -> 0)

## Intended: refs outside a scoped view keep working (2)

A scoped snapshot used to replace the registry with subtree-local refs, so a page ref then failed; every view is now cut from one full snapshot.

- controls:outside-ref-after-selector (exit 1 -> 0)
- controls:outside-ref-after-ref-scope (exit 1 -> 0)

## Intended: stale ref refused instead of acting on another element (7)

Old role/name/nth lookup silently hit a different element after a DOM change or navigation (state log shows item-a2 / stay2 / rerender clicked); native refs fail fast with `no longer matches an element`, `is from a page this tab has left`, or the tab guard.

- nav:stale-after-rerender-click (exit 0 -> 1)
- nav:stale-after-cli-click (exit 0 -> 1)
- nav:stale-after-js-nav-click (exit 0 -> 1)
- nav:stale-after-js-nav-gen (exit 0 -> 1)
- nav:stale-after-js-nav-html (exit 0 -> 1)
- nav:stale-after-js-nav-link (exit 1 -> 1)
- nav:stale-after-goto (exit 0 -> 1)

## Intended: new ref error wording (7)

Unknown/stale ref errors name the cause: tab guard `is not from a snapshot of this tab; run snapshot (...)`, `is from a page this tab has left`. Same exit code.

- controls:click-after-scoped (exit 1 -> 1)
- controls:html-bad-ref (exit 1 -> 1)
- controls:bad-ref-num (exit 1 -> 1)
- controls:generate-locator-bad (exit 1 -> 1)
- controls:highlight-bad (exit 1 -> 1)
- nav:click-after-back (exit 1 -> 1)
- tabs:a-ref-in-b-unknown-number (exit 1 -> 1)

## Intended: malformed ref is a usage error (1)

`click nonsense` exits 2 with `"nonsense" is not a ref (refs look like e5 or f3e5, from snapshot)`.

- controls:bad-ref-word (exit 1 -> 2)

## Intended: ref from the previous document refused (1)

`@e1` named the first document's root; after the navigations the page's refs are `f1e*`, and the old code resolved `e1` by role/name on the new page. The tab guard refuses it.

- controls:bad-ref-at (exit 0 -> 1)

## Intended: select names the mistake (2)

A ref given as the option fails at once with the label hint instead of a 30 s select_option timeout; a missing option's timeout lists the combobox's options.

- controls:select-ref-as-option (exit 1 -> 1)
- controls:select-missing-option (exit 1 -> 1)

## Intended: ref names in output (7)

Same result, with the native ref in the message (`Highlighted f1e18.`, pick-locator `### Ref - f1e5`) or the locator in Playwright's call log (`locator("aria-ref=f8e4")` instead of `get_by_role(...)`).

- controls:highlight (exit 0 -> 0)
- controls:highlight-style (exit 0 -> 0)
- controls:highlight-hide-ref (exit 0 -> 0)
- dialog:click-behind (exit 1 -> 1)
- offscreen:click-none (exit 1 -> 1)
- offscreen:hover-none (exit 1 -> 1)
- offscreen:check-none (exit 1 -> 1)

## Intended: pick-locator now finds the ref (6)

Old matching only tried role-based picks (an id-based `#save2` pick got no ref) and capped candidates; the depth-0 probe maps any picked element, including inside the iframe.

- controls:pick-save2 (exit 0 -> 0)
- controls:pick-name (exit 0 -> 0)
- controls:pick-dup-1 (exit 0 -> 0)
- controls:pick-dup-0 (exit 0 -> 0)
- iframe:pick-outer (exit 0 -> 0)
- iframe:pick-inner (exit 0 -> 0)

## Intended: cascade: page state from earlier steps (5)

Same step result; the fixture's click log / field values differ only because earlier steps now succeed (iframe fills, refs outside a scoped view) or no longer act on a wrong element.

- controls:deep-ref-after-depth (exit 0 -> 0)
- controls:ref-after-i (exit 0 -> 0)
- controls:ref-after-reattach (exit 0 -> 0)
- controls:find-then-click (exit 0 -> 0)
- nav:click-rerender (exit 0 -> 0)

## Notes

- Known residual, identical before and after: `tabs:a-ref-in-b-same-number`. Two tabs that are both on
  their first document can print the same ref (`e3`); the tab guard cannot tell them apart, so tab b's
  `e3` is clicked. Both versions do this; SKILL.md says refs are per tab and to use the snapshot that
  `tab-select` prints.
- Ref numbers are Playwright's: the page is on a later document by the time most steps run, so refs read
  `f1e18` where the old registry printed `e19`. The sweep's argv names targets by role and name, so the
  steps still line up.
- Timing (ms, CLI round trip): controls `snapshot --boxes` 90016 (timeout) -> ~210; offscreen `--boxes`
  ~4250 -> ~215; large `--boxes` ~2700 -> ~250; plain large `snapshot` unchanged (~220).

