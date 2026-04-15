# patchright-cli Skills Catalog

Anti-detect browser automation CLI using Patchright (undetected Playwright fork).

## Skill hierarchy

```
skills/patchright-cli/
├── SKILL.md                            # Main skill — quick start, commands, options, patterns
├── CATALOG.md                          # This file — skill index and design overview
└── references/
    ├── snapshot-refs.md                # Snapshots, element refs, ref lifecycle, -i flag
    ├── session-management.md           # Named sessions, profiles, CDP attach, dashboard
    ├── request-mocking.md              # Route mocking, network state, DevTools
    ├── storage-state.md                # Cookies, localStorage, sessionStorage, state save/load
    ├── running-code.md                 # eval, run-code, --file, stdin, quoting pitfalls
    ├── tracing.md                      # Trace recording, output format, debugging
    └── video-recording.md              # Video, codegen, screenshot, PDF, upload, resize
```

## Design

**SKILL.md** is the entry point. It contains everything an agent needs for common tasks: quick start, command reference, global options, common patterns (login, data extraction, proxies, dialogs), and anti-detect guidance. Agents load this file first.

**Reference docs** contain deep task-specific guidance. Agents load these on demand when working on a specific topic. This keeps SKILL.md lean (~300 lines) while providing detailed reference material (~600 lines total across 7 files) without inflating the base token cost.

## Reference index

| File | Topic | When to load |
|------|-------|-------------|
| [snapshot-refs.md](references/snapshot-refs.md) | Snapshot system, ref lifecycle, `--depth`, `-i` flag, troubleshooting | Working with page elements, debugging stale refs |
| [session-management.md](references/session-management.md) | Named sessions, persistent profiles, CDP attach, dashboard, cleanup | Multi-session workflows, connecting to existing browsers |
| [request-mocking.md](references/request-mocking.md) | Route patterns, status/body/header overrides, network state | Mocking APIs, blocking resources, simulating offline |
| [storage-state.md](references/storage-state.md) | Cookies, localStorage, sessionStorage, state save/load | Login persistence, cookie import/export, state management |
| [running-code.md](references/running-code.md) | `eval`, `run-code`, `--file`, stdin, element targeting | JavaScript execution, data extraction, custom waits |
| [tracing.md](references/tracing.md) | Trace recording and output format | Debugging failed actions, performance analysis |
| [video-recording.md](references/video-recording.md) | Video, codegen, screenshot, PDF, upload, resize | Recording sessions, capturing evidence, code generation |

## Install

```bash
npx skills add AhaiMk01/patchright-cli
```
