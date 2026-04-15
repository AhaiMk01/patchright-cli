# CDP Attach Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `attach --cdp=<url>` support so patchright-cli can connect to an already-running Chrome/Edge instance via the Chrome DevTools Protocol.

**Architecture:** Add a new `attach` CLI command and corresponding daemon handler. When a `cdp_endpoint` is provided, the daemon uses `chromium.connect_over_cdp()` instead of `launch_persistent_context()`, then creates a fresh browser context and page from the connected `Browser` object.

**Tech Stack:** Python 3.10+, Patchright (Playwright)

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/patchright_cli/cli.py` | Add `attach` command help and map `--cdp` flag. |
| `src/patchright_cli/daemon.py` | Handle `attach` command, update `get_or_create_session` to support `cdp_endpoint`, `cdp_headers`, `cdp_timeout`. |
| `tests/test_daemon_handlers.py` | Async test for `attach` handler. |

---

### Task 1: CLI command definition

**Files:**
- Modify: `src/patchright_cli/cli.py`

- [ ] **Step 1: Add `attach` to command help**

In `COMMANDS_HELP`, add:

```python
    "attach": "attach --cdp=<url>   Attach to running Chrome via CDP",
```

In `_print_help()`, under Browser lifecycle (near `open`), change the category to include `attach`:

```python
        (
            "Core",
            [
                "open",
                "attach",
                "goto",
                ...
            ],
        ),
```

- [ ] **Step 2: Add `--cdp` global option parsing**

In `main()`, inside the option-parsing loop, add:

```python
        elif arg.startswith("--cdp="):
            extra_opts["cdp"] = arg.split("=", 1)[1]
        elif arg == "--cdp" and i + 1 < len(argv):
            i += 1
            extra_opts["cdp"] = argv[i]
```

And in `_print_help()`:

```python
    click.echo("  --cdp=<url>         Attach to Chrome via CDP endpoint")
```

- [ ] **Step 3: Commit**

```bash
git add src/patchright_cli/cli.py
git commit -m "feat: add attach command and --cdp flag to CLI"
```

---

### Task 2: Daemon handler and session creation

**Files:**
- Modify: `src/patchright_cli/daemon.py`
- Test: `tests/test_daemon_handlers.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_attach_cdp(mock_state, mock_session):
    mock_state.sessions = {}
    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_page = MagicMock()
    mock_page.url = "about:blank"
    mock_page.title = AsyncMock(return_value="Attached")
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_context.new_page = AsyncMock(return_value=mock_page)
    mock_context.pages = [mock_page]
    mock_state.playwright.chromium.connect_over_cdp = AsyncMock(return_value=mock_browser)

    with patch("patchright_cli.daemon.take_snapshot", new_callable=AsyncMock) as mock_snap:
        mock_snap.return_value = ("snapshot-text", MagicMock())
        with patch("patchright_cli.daemon.save_snapshot", return_value="/tmp/snap.yml"):
            response = await handle_command(
                mock_state,
                {"command": "attach", "args": [], "options": {"cdp": "http://localhost:9222"}}
            )
    assert response["success"] is True
    mock_state.playwright.chromium.connect_over_cdp.assert_awaited_once_with(
        "http://localhost:9222", headers=None, timeout=30000
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_daemon_handlers.py::test_attach_cdp -v`
Expected: FAIL — command unknown or `connect_over_cdp` not called.

- [ ] **Step 3: Update `get_or_create_session` for CDP**

In `DaemonState.get_or_create_session` signature, add:

```python
        cdp_endpoint: str | None = None,
        cdp_headers: dict | None = None,
        cdp_timeout: int = 30000,
```

Then replace the `launch_kwargs` and `launch_persistent_context` block with branching logic:

```python
        if cdp_endpoint:
            browser = await self.playwright.chromium.connect_over_cdp(
                cdp_endpoint, headers=cdp_headers, timeout=cdp_timeout
            )
            context = await browser.new_context()
            pages = context.pages or []
            if not pages:
                page = await context.new_page()
                pages = [page]
        else:
            launch_kwargs = {
                "channel": "chrome",
                "headless": use_headless,
                "no_viewport": True,
                "args": ["--disable-blink-features=AutomationControlled"],
            }
            if proxy:
                parsed = urlparse(proxy)
                if parsed.username or parsed.password:
                    import base64
                    creds = base64.b64encode(f"{parsed.username or ''}:{parsed.password or ''}".encode()).decode()
                    launch_kwargs["extra_http_headers"] = {"Proxy-Authorization": f"Basic {creds}"}
                    netloc = parsed.hostname or ""
                    if parsed.port:
                        netloc += f":{parsed.port}"
                    cleaned = parsed._replace(netloc=netloc).geturl()
                    launch_kwargs["proxy"] = {"server": cleaned}
                else:
                    launch_kwargs["proxy"] = {"server": proxy}
            launch_kwargs.update(context_options)
            context = await self.playwright.chromium.launch_persistent_context(
                profile_dir,
                **launch_kwargs,
            )
            pages = context.pages or []
            if not pages:
                page = await context.new_page()
                pages = [page]
```

Wait, we need to make sure `context_options` is still built for the non-CDP path. And for CDP, device/viewport options should also be applied. Actually, when using `browser.new_context()` after CDP connect, we can pass context options directly to `new_context()`:

```python
        context_options = {}
        if device and self.playwright is not None:
            device_descriptor = self.playwright.devices.get(device)
            if device_descriptor:
                context_options.update(device_descriptor)
        if viewport:
            context_options["viewport"] = {"width": int(viewport["width"]), "height": int(viewport["height"])}
        if locale:
            context_options["locale"] = locale
        if timezone:
            context_options["timezone_id"] = timezone
        if geolocation:
            context_options["geolocation"] = {
                "latitude": float(geolocation["lat"]),
                "longitude": float(geolocation["lon"]),
            }
            context_options["permissions"] = context_options.get("permissions", []) + ["geolocation"]
        if user_agent:
            context_options["user_agent"] = user_agent
        if grant_permissions:
            perms = [p.strip() for p in grant_permissions.split(",") if p.strip()]
            context_options["permissions"] = list(set(context_options.get("permissions", []) + perms))

        if cdp_endpoint:
            browser = await self.playwright.chromium.connect_over_cdp(
                cdp_endpoint, headers=cdp_headers, timeout=cdp_timeout
            )
            context = await browser.new_context(**context_options)
            pages = context.pages or []
            if not pages:
                page = await context.new_page()
                pages = [page]
        else:
            launch_kwargs = {
                "channel": "chrome",
                "headless": use_headless,
                "no_viewport": True,
                "args": ["--disable-blink-features=AutomationControlled"],
            }
            if proxy:
                parsed = urlparse(proxy)
                if parsed.username or parsed.password:
                    import base64
                    creds = base64.b64encode(f"{parsed.username or ''}:{parsed.password or ''}".encode()).decode()
                    launch_kwargs["extra_http_headers"] = {"Proxy-Authorization": f"Basic {creds}"}
                    netloc = parsed.hostname or ""
                    if parsed.port:
                        netloc += f":{parsed.port}"
                    cleaned = parsed._replace(netloc=netloc).geturl()
                    launch_kwargs["proxy"] = {"server": cleaned}
                else:
                    launch_kwargs["proxy"] = {"server": proxy}
            launch_kwargs.update(context_options)
            context = await self.playwright.chromium.launch_persistent_context(
                profile_dir,
                **launch_kwargs,
            )
            pages = context.pages or []
            if not pages:
                page = await context.new_page()
                pages = [page]
```

- [ ] **Step 4: Add `attach` handler and update `open` handler**

In `handle_command`, add the `attach` branch right after the `open` branch:

```python
        if cmd == "attach":
            cdp = options.get("cdp")
            if not cdp:
                return {"success": False, "output": "Usage: attach --cdp=<url>"}
            headless = options.get("headless", False)
            session = await state.get_or_create_session(
                session_name,
                headless=headless,
                cdp_endpoint=cdp,
                cdp_headers=options.get("cdp-headers"),
                cdp_timeout=int(options.get("cdp-timeout", 30000)),
                device=options.get("device"),
                viewport=options.get("viewport"),
                locale=options.get("locale"),
                timezone=options.get("timezone"),
                geolocation=options.get("geolocation"),
                user_agent=options.get("user-agent") or options.get("userAgent"),
                grant_permissions=options.get("grant-permissions") or options.get("grantPermissions"),
            )
            if session.page:
                session.push_history(session.page.url)
            return await _page_info(session, cwd)
```

- [ ] **Step 5: Run test**

Run: `pytest tests/test_daemon_handlers.py::test_attach_cdp -v`
Expected: PASS

- [ ] **Step 6: Run full handler test suite**

Run: `pytest tests/test_daemon_handlers.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add tests/test_daemon_handlers.py src/patchright_cli/daemon.py
git commit -m "feat: attach to running Chrome via CDP endpoint"
```

---

## Self-Review

**1. Spec coverage:**
- `attach --cdp=<url>` → Task 1 + Task 2
- CDP headers/timeout support → Task 2 via `get_or_create_session`
- All requirements covered.

**2. Placeholder scan:**
- No TBDs or vague steps.
- Exact code provided for every change.

**3. Type consistency:**
- `cdp_endpoint` is consistently `str | None`.
- `cdp_timeout` defaults to `30000` (int) throughout.
