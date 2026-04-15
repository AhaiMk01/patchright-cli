# Tracing

## Commands

```bash
patchright-cli tracing-start               # Start recording a trace
patchright-cli tracing-stop                # Stop and save trace
```

## Output format

Traces are saved as `.zip` files in the `.patchright-cli/` directory. These are Playwright trace files that can be opened in the [Playwright Trace Viewer](https://trace.playwright.dev/).

## What traces capture

A trace records:
- **Screenshots** at each action (before and after)
- **DOM snapshots** at each step
- **Network requests** and responses
- **Console messages** (log, warn, error)
- **Action log** with timing (click, fill, goto, etc.)
- **Source locations** for each action

## Use cases

### Debugging automation failures
Start a trace before a complex interaction sequence. If something goes wrong, the trace shows exactly what the page looked like at each step.

```bash
patchright-cli tracing-start
patchright-cli goto https://example.com/checkout
patchright-cli fill e3 "4111111111111111"
patchright-cli click e8
patchright-cli tracing-stop
# Open the .zip in https://trace.playwright.dev/ to inspect
```

### Performance investigation
Traces include network timing, so you can identify slow requests or rendering bottlenecks.

### Evidence and audit trail
Traces provide a complete record of what happened during an automation run -- useful for compliance, QA sign-off, or reproducing issues.

## Trace vs Video vs Screenshot comparison

| Feature | Trace | Video | Screenshot |
|---------|-------|-------|------------|
| Format | .zip (Playwright) | .webm (or frames) | .png |
| Captures | DOM + network + actions | Visual recording | Single frame |
| Interactive replay | Yes (Trace Viewer) | No (play only) | No |
| File size | Medium | Large | Small |
| Best for | Debugging, step-by-step inspection | Visual demo, recording flows | Quick state check |
| Requires | Nothing extra | ffmpeg (for .webm) | Nothing extra |
