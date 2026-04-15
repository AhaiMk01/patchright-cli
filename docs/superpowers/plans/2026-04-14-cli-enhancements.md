# CLI Enhancements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add five quality-of-life CLI features: `snapshot --depth`, `eval [ref]`, `PATCHRIGHT_CLI_SESSION` env var, configurable timeouts, and `video-chapter`.

**Architecture:** Extend existing CLI parsing in `cli.py`, daemon handlers in `daemon.py`, and `RefRegistry` in `ref_registry.py`. All features reuse the current TCP command protocol with no breaking changes.

**Tech Stack:** Python 3.10+, Patchright (Playwright), pytest, pytest-asyncio

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/patchright_cli/cli.py` | Parse new global options (`--timeout-action`, `--timeout-navigation`, `--depth`) and `video-chapter` command. Read `PATCHRIGHT_CLI_SESSION` env var. |
| `src/patchright_cli/ref_registry.py` | Add `max_depth` support to `parse()`; skip ref injection beyond the depth limit. |
| `src/patchright_cli/snapshot.py` | Pass `max_depth` through `take_snapshot()` to `RefRegistry.parse()`. |
| `src/patchright_cli/daemon.py` | Handle `video-chapter`, apply timeouts to pages, update `cmd_eval` for element refs, update `cmd_snapshot` for `--depth`. |
| `tests/test_ref_registry.py` | Unit tests for `--depth` parsing. |
| `tests/test_daemon_handlers.py` | Async handler tests for eval-with-ref, timeouts, and video-chapter. |

---

### Task 1: `snapshot --depth=N` in RefRegistry

**Files:**
- Modify: `src/patchright_cli/ref_registry.py`
- Test: `tests/test_ref_registry.py`

- [ ] **Step 1: Write the failing test**

```python
def test_parse_with_max_depth():
    registry = RefRegistry()
    raw = '- heading "Login"\n  - textbox "Username"\n  - button "Submit"\n    - link "Forgot"'
    result = registry.parse(raw, max_depth=1)
    lines = result.splitlines()
    assert "[ref=e1]" in lines[0]  # depth 0
    assert "[ref=e2]" in lines[1]  # depth 1
    assert "[ref=e3]" in lines[2]  # depth 1
    assert "[ref=" not in lines[3]  # depth 2 skipped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ref_registry.py::test_parse_with_max_depth -v`
Expected: FAIL with `TypeError: parse() got an unexpected keyword argument 'max_depth'`

- [ ] **Step 3: Write minimal implementation**

In `src/patchright_cli/ref_registry.py`, modify `parse`:

```python
    def parse(self, aria_text: str, max_depth: int | None = None) -> str:
        """Return annotated snapshot text with [ref=eN] tags inserted."""
        self.entries.clear()
        self._counter = 0
        seen: dict[tuple[str, str], int] = {}
        result_lines: list[str] = []

        for line in aria_text.splitlines():
            m = _NODE_LINE_RE.match(line)
            if not m:
                result_lines.append(line)
                continue

            indent = len(line) - len(line.lstrip())
            depth = indent // 2
            if max_depth is not None and depth > max_depth:
                result_lines.append(line)
                continue

            role = m.group(1)
            name = m.group(2) or ""

            self._counter += 1
            ref = f"e{self._counter}"

            key = (role, name)
            nth = seen.get(key, 0)
            seen[key] = nth + 1

            self.entries[ref] = AriaRefEntry(ref=ref, role=role, name=name, nth=nth)
            result_lines.append(f"{line.rstrip()} [ref={ref}]")

        return "\n".join(result_lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ref_registry.py::test_parse_with_max_depth -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_ref_registry.py src/patchright_cli/ref_registry.py
git commit -m "feat: support max_depth in RefRegistry.parse for snapshot --depth"
```

---

### Task 2: Wire `--depth` through snapshot.py and daemon.py

**Files:**
- Modify: `src/patchright_cli/snapshot.py`
- Modify: `src/patchright_cli/daemon.py`

- [ ] **Step 1: Update `take_snapshot` signature**

In `src/patchright_cli/snapshot.py`:

```python
async def take_snapshot(page, root_element=None, max_depth: int | None = None) -> tuple[str, RefRegistry]:
    """Take a DOM snapshot. Returns (annotated_text, registry).

    If root_element is provided, only snapshot the subtree under that element.
    """
    try:
        if root_element:
            aria_text = await root_element.aria_snapshot()
        else:
            aria_text = await page.locator("body").aria_snapshot()
    except Exception:
        aria_text = ""

    if not aria_text or not aria_text.strip():
        return "# Empty page - no accessible elements found\n", RefRegistry()

    registry = RefRegistry()
    annotated = registry.parse(aria_text, max_depth=max_depth)
    return annotated + "\n", registry
```

- [ ] **Step 2: Update `cmd_snapshot` to read `--depth`**

In `src/patchright_cli/daemon.py`, in `cmd_snapshot`:

```python
@register("snapshot")
async def cmd_snapshot(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    element_ref = args[0] if args else None
    max_depth = int(options["depth"]) if options.get("depth") is not None else None
    if element_ref:
        elem = await _resolve_ref(session, page, element_ref)
        snapshot_text, session.ref_registry = await take_snapshot(page, root_element=elem, max_depth=max_depth)
    else:
        snapshot_text, session.ref_registry = await take_snapshot(page, max_depth=max_depth)
    # ... rest unchanged
```

- [ ] **Step 3: Add `--depth` to CLI help**

In `src/patchright_cli/cli.py`, in `COMMANDS_HELP`:

```python
    "snapshot": "snapshot [ref]        Take accessibility snapshot [--filename=F] [--depth=N]",
```

- [ ] **Step 4: Run existing snapshot-related tests**

Run: `pytest tests/test_ref_registry.py tests/test_daemon_handlers.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/patchright_cli/snapshot.py src/patchright_cli/daemon.py src/patchright_cli/cli.py
git commit -m "feat: wire --depth option through snapshot command"
```

---

### Task 3: `eval` with optional element ref

**Files:**
- Modify: `src/patchright_cli/daemon.py`
- Modify: `src/patchright_cli/cli.py`
- Test: `tests/test_daemon_handlers.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_daemon_handlers.py`:

```python
@pytest.mark.asyncio
async def test_eval_with_ref(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    registry = MagicMock()
    locator = MagicMock()
    locator.evaluate = AsyncMock(return_value="el-result")
    registry.resolve.return_value = locator
    mock_session.ref_registry = registry

    response = await handle_command(mock_state, {"command": "eval", "args": ["el => el.textContent", "e1"]})
    assert response["success"] is True
    assert '"el-result"' in response["output"]
    registry.resolve.assert_called_once_with(mock_session.page, "e1")
    locator.evaluate.assert_awaited_once_with("el => el.textContent")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_daemon_handlers.py::test_eval_with_ref -v`
Expected: FAIL because `cmd_eval` treats both args as a single expression.

- [ ] **Step 3: Update `cmd_eval` handler**

In `src/patchright_cli/daemon.py`:

```python
@register("eval")
async def cmd_eval(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    import re

    if len(args) >= 2 and re.fullmatch(r"e\d+", args[-1]) and session.ref_registry is not None:
        expr = " ".join(args[:-1])
        ref = args[-1]
        elem = await _resolve_ref(session, page, ref)
        result = await elem.evaluate(expr)
    else:
        expr = args[0] if args else ""
        result = await page.evaluate(expr)
    return {"success": True, "output": json.dumps(result, indent=2, default=str)}
```

- [ ] **Step 4: Update CLI help for eval**

In `src/patchright_cli/cli.py`:

```python
    "eval": "eval <expr> [ref]     Evaluate JavaScript [--file=F or stdin]",
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_daemon_handlers.py::test_eval_with_ref -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add tests/test_daemon_handlers.py src/patchright_cli/daemon.py src/patchright_cli/cli.py
git commit -m "feat: eval can target a specific element ref"
```

---

### Task 4: `PATCHRIGHT_CLI_SESSION` env var fallback

**Files:**
- Modify: `src/patchright_cli/cli.py`
- Test: `tests/test_daemon_handlers.py` (indirectly covered by session tests)

- [ ] **Step 1: Update CLI session default**

In `src/patchright_cli/cli.py`, in `main()`:

Find:
```python
    session_name = "default"
```

Replace with:
```python
    session_name = os.environ.get("PATCHRIGHT_CLI_SESSION", "default")
```

- [ ] **Step 2: Verify with a quick Python one-liner**

Run: `PATCHRIGHT_CLI_SESSION=mytest python -c "from patchright_cli.cli import main; import sys; sys.argv=['patchright-cli']; main()"`
(It should print help and exit 0; no errors.)

Also run without the env var:
Run: `python -c "import os; from patchright_cli.cli import main; print(os.environ.get('PATCHRIGHT_CLI_SESSION', 'default'))"`
Expected: prints `default`

- [ ] **Step 3: Commit**

```bash
git add src/patchright_cli/cli.py
git commit -m "feat: support PATCHRIGHT_CLI_SESSION env var"
```

---

### Task 5: Configurable timeouts

**Files:**
- Modify: `src/patchright_cli/cli.py`
- Modify: `src/patchright_cli/daemon.py`
- Test: `tests/test_daemon_handlers.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_daemon_handlers.py`:

```python
@pytest.mark.asyncio
async def test_timeouts_applied(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session.page.set_default_timeout = MagicMock()
    mock_session.page.set_default_navigation_timeout = MagicMock()
    response = await handle_command(
        mock_state,
        {"command": "goto", "args": ["https://example.com"], "options": {"timeout-action": "10000", "timeout-navigation": "30000"}}
    )
    mock_session.page.set_default_timeout.assert_called_once_with(10000.0)
    mock_session.page.set_default_navigation_timeout.assert_called_once_with(30000.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_daemon_handlers.py::test_timeouts_applied -v`
Expected: FAIL because timeouts are not yet applied.

- [ ] **Step 3: Update CLI parsing for timeout options**

In `src/patchright_cli/cli.py`, in `main()`:

Add inside the option-parsing loop, after the `--port` blocks:

```python
        elif arg.startswith("--timeout-action="):
            extra_opts["timeout-action"] = arg.split("=", 1)[1]
        elif arg == "--timeout-action" and i + 1 < len(argv):
            i += 1
            extra_opts["timeout-action"] = argv[i]
        elif arg.startswith("--timeout-navigation="):
            extra_opts["timeout-navigation"] = arg.split("=", 1)[1]
        elif arg == "--timeout-navigation" and i + 1 < len(argv):
            i += 1
            extra_opts["timeout-navigation"] = argv[i]
```

Also add to `_print_help()`:

```python
    click.echo("  --timeout-action=ms   Default action timeout")
    click.echo("  --timeout-navigation=ms Default navigation timeout")
```

- [ ] **Step 4: Apply timeouts in the daemon**

In `src/patchright_cli/daemon.py`, add a helper after `_resolve_ref`:

```python
async def _apply_timeouts(page, options: dict) -> None:
    if options.get("timeout-action"):
        try:
            page.set_default_timeout(float(options["timeout-action"]))
        except Exception:
            pass
    if options.get("timeout-navigation"):
        try:
            page.set_default_navigation_timeout(float(options["timeout-navigation"]))
        except Exception:
            pass
```

Then in `handle_command`, after `page = session.page` and before dispatching to handlers (but after validating page exists), add:

```python
        if page is not None:
            await _apply_timeouts(page, options)
```

Wait — `handle_command` is sync-async. The `page is None` check is after the lifecycle commands. Find this block:

```python
        if page is None:
            return {
                "success": False,
                "output": "No page open. Run 'tab-new' to create one, or 'close' and 'open' again.",
            }
```

Add immediately after it:

```python
        await _apply_timeouts(page, options)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_daemon_handlers.py::test_timeouts_applied -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add tests/test_daemon_handlers.py src/patchright_cli/cli.py src/patchright_cli/daemon.py
git commit -m "feat: add --timeout-action and --timeout-navigation options"
```

---

### Task 6: `video-chapter` command

**Files:**
- Modify: `src/patchright_cli/cli.py`
- Modify: `src/patchright_cli/daemon.py`
- Test: `tests/test_daemon_handlers.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_daemon_handlers.py`:

```python
@pytest.mark.asyncio
async def test_video_chapter(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session._video_recording = True
    mock_session._video_frames = [b"frame1"]
    response = await handle_command(mock_state, {"command": "video-chapter", "args": ["Login page"]})
    assert response["success"] is True
    assert len(mock_session._video_chapters) == 1
    assert mock_session._video_chapters[0] == (1, "Login page")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_daemon_handlers.py::test_video_chapter -v`
Expected: FAIL with `AttributeError: '_video_chapters' not found` or command unknown.

- [ ] **Step 3: Add CLI command and help**

In `src/patchright_cli/cli.py`:

Add to `COMMANDS_HELP` under Video:

```python
    "video-chapter": "video-chapter <title>  Add chapter marker to video",
```

Add to the Video category in `_print_help()`:

```python
        ("Video", ["video-start", "video-stop", "video-chapter"]),
```

- [ ] **Step 4: Add Session field and handlers**

In `src/patchright_cli/daemon.py`, in `Session.__init__`:

Find:
```python
        self._video_recording: bool = False
```

Add below:
```python
        self._video_chapters: list[tuple[int, str]] = []
```

Add handler:

```python
@register("video-chapter")
async def cmd_video_chapter(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    if not session._video_recording:
        return {"success": False, "output": "No video recording in progress."}
    title = args[0] if args else "Chapter"
    frame_index = len(session._video_frames)
    session._video_chapters.append((frame_index, title))
    return {"success": True, "output": f"Chapter '{title}' added at frame {frame_index}."}
```

In `cmd_video_stop`, before clearing frames, write chapters metadata if any exist:

Find the `frames = session._video_frames` line and add before it:

```python
    if session._video_chapters:
        base = Path(cwd) if cwd else Path.cwd()
        snap_dir = base / ".patchright-cli"
        snap_dir.mkdir(parents=True, exist_ok=True)
        chapters_path = snap_dir / f"video-{ts}-chapters.json"
        chapters_path.write_text(
            json.dumps([{"frame": f, "title": t} for f, t in session._video_chapters], indent=2),
            encoding="utf-8",
        )
        session._video_chapters.clear()
```

And reset chapters after clearing:
```python
    session._video_chapters = []
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_daemon_handlers.py::test_video_chapter -v`
Expected: PASS

- [ ] **Step 6: Run full test suite**

Run: `pytest tests/test_ref_registry.py tests/test_daemon_handlers.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add tests/test_daemon_handlers.py src/patchright_cli/cli.py src/patchright_cli/daemon.py
git commit -m "feat: add video-chapter command for screencast chapter markers"
```

---

## Self-Review

**1. Spec coverage:**
- `snapshot --depth=N` → Task 1 + Task 2
- `eval [ref]` → Task 3
- `PATCHRIGHT_CLI_SESSION` env var → Task 4
- Configurable timeouts → Task 5
- `video-chapter` → Task 6
- All requirements have dedicated tasks.

**2. Placeholder scan:**
- No TBDs, no vague "add error handling" steps, no "similar to Task X" references.
- Every code block contains the exact implementation.

**3. Type consistency:**
- `RefRegistry.parse` signature changes from `parse(self, aria_text: str)` to `parse(self, aria_text: str, max_depth: int | None = None)` — used consistently in Task 2.
- `_video_chapters` is `list[tuple[int, str]]` throughout.
- Timeout option keys are `"timeout-action"` and `"timeout-navigation"` in both CLI and daemon.
