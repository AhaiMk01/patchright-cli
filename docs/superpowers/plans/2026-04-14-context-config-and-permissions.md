# Context Config & Permissions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add config file support, device emulation, and permission management to patchright-cli.

**Architecture:** Load a JSON config file in `cli.py`, merge it with command-line flags (CLI wins), and pass the merged options to the daemon. The daemon applies device descriptors, viewport, locale, timezone, geolocation, and permissions when creating or attaching to browser contexts.

**Tech Stack:** Python 3.10+, Patchright (Playwright), pytest, pytest-asyncio

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/patchright_cli/cli.py` | Parse `--config` and `--grant-permissions`. Load and merge JSON config with CLI args. |
| `src/patchright_cli/daemon.py` | Apply device descriptors, viewport, locale, timezone, geolocation, and permissions in `get_or_create_session` and `cmd_grant_permissions`. |
| `tests/test_daemon_handlers.py` | Async handler tests for `grant-permissions` and config-driven options. |
| `tests/test_cli_config.py` | Unit tests for config loading/merging without a browser. |

---

### Task 1: Config loading and merging in CLI

**Files:**
- Create: `tests/test_cli_config.py`
- Modify: `src/patchright_cli/cli.py`

- [ ] **Step 1: Write the failing test**

```python
import json
import os
import tempfile

from patchright_cli.cli import _load_config


def test_load_config_defaults():
    result = _load_config(None)
    assert result == {}


def test_load_config_from_path():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"headless": True, "proxy": "http://proxy"}, f)
        path = f.name
    try:
        result = _load_config(path)
        assert result["headless"] is True
        assert result["proxy"] == "http://proxy"
    finally:
        os.unlink(path)


def test_load_config_from_default_location():
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, ".patchright-cli", "config.json")
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w") as f:
            json.dump({"persistent": True}, f)
        old_cwd = os.getcwd()
        os.chdir(tmpdir)
        try:
            result = _load_config(None)
            assert result["persistent"] is True
        finally:
            os.chdir(old_cwd)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_config.py -v`
Expected: FAIL with `ImportError: cannot import name '_load_config' from 'patchright_cli.cli'`

- [ ] **Step 3: Implement `_load_config` and `_merge_config_with_options`**

In `src/patchright_cli/cli.py`, add these helper functions near the top (after imports):

```python
def _load_config(config_path: str | None) -> dict:
    """Load JSON config file. If config_path is None, try .patchright-cli/config.json in cwd."""
    if config_path:
        p = Path(config_path)
    else:
        p = Path.cwd() / ".patchright-cli" / "config.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def _merge_config_with_options(config: dict, options: dict) -> dict:
    """Merge config dict with CLI options. CLI options take precedence."""
    merged = dict(config)
    for k, v in options.items():
        if v is not None:
            merged[k] = v
    return merged
```

- [ ] **Step 4: Wire config into main()**

In `src/patchright_cli/cli.py`, inside `main()`:

Find the option-parsing loop and add `--config` parsing before the existing options:

```python
        elif arg.startswith("--config="):
            config_path = arg.split("=", 1)[1]
        elif arg == "--config" and i + 1 < len(argv):
            i += 1
            config_path = argv[i]
```

Initialize `config_path = None` alongside the other defaults near the top of `main()`.

After the option-parsing loop finishes, add:

```python
    config = _load_config(config_path)
    merged_options = _merge_config_with_options(config, options)
```

Change the `options = merged_options` usage when building the command:

Find:
```python
    options = {"session": session_name, **extra_opts}
```

Replace with:
```python
    options = {"session": session_name, **_merge_config_with_options(config, extra_opts)}
```

Also add to `_print_help()`:

```python
    click.echo("  --config=<path>     Load config from JSON file")
    click.echo("  --grant-perms=P     Comma-separated permissions to grant")
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_cli_config.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add tests/test_cli_config.py src/patchright_cli/cli.py
git commit -m "feat: add --config support for JSON configuration files"
```

---

### Task 2: Device emulation, viewport, locale, timezone, geolocation, UA in daemon

**Files:**
- Modify: `src/patchright_cli/daemon.py`

- [ ] **Step 1: Update `get_or_create_session` signature and body**

In `src/patchright_cli/daemon.py`, modify `DaemonState.get_or_create_session`:

Add these kwargs to the signature:

```python
        device: str | None = None,
        viewport: dict | None = None,
        locale: str | None = None,
        timezone: str | None = None,
        geolocation: dict | None = None,
        user_agent: str | None = None,
        grant_permissions: str | None = None,
```

Then, after `launch_kwargs` is built, add `context_options` dict:

```python
        context_options: dict = {}
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
```

Then merge `context_options` into `launch_kwargs`:

```python
        launch_kwargs.update(context_options)
```

Wait — `launch_persistent_context` accepts both launch and context options. In Playwright, `viewport`, `locale`, `timezone_id`, `geolocation`, `user_agent`, `permissions` are all valid context options and `launch_persistent_context` accepts them. So merging is correct.

- [ ] **Step 2: Extract config options in `handle_command` for `open`**

In `src/patchright_cli/daemon.py`, in the `cmd == "open"` branch:

Find:
```python
            session = await state.get_or_create_session(
                session_name,
                headless=headless,
                persistent=options.get("persistent", True),
                profile=options.get("profile"),
                proxy=options.get("proxy"),
                url=url,
            )
```

Replace with:
```python
            session = await state.get_or_create_session(
                session_name,
                headless=headless,
                persistent=options.get("persistent", True),
                profile=options.get("profile"),
                proxy=options.get("proxy"),
                url=url,
                device=options.get("device"),
                viewport=options.get("viewport"),
                locale=options.get("locale"),
                timezone=options.get("timezone"),
                geolocation=options.get("geolocation"),
                user_agent=options.get("user-agent") or options.get("userAgent"),
                grant_permissions=options.get("grant-permissions") or options.get("grantPermissions"),
            )
```

- [ ] **Step 3: Add CLI global options**

In `src/patchright_cli/cli.py`, inside the option-parsing loop, add:

```python
        elif arg.startswith("--device="):
            extra_opts["device"] = arg.split("=", 1)[1]
        elif arg == "--device" and i + 1 < len(argv):
            i += 1
            extra_opts["device"] = argv[i]
        elif arg.startswith("--viewport-size="):
            vw, vh = arg.split("=", 1)[1].split("x", 1)
            extra_opts["viewport"] = {"width": vw, "height": vh}
        elif arg == "--viewport-size" and i + 1 < len(argv):
            i += 1
            vw, vh = argv[i].split("x", 1)
            extra_opts["viewport"] = {"width": vw, "height": vh}
        elif arg.startswith("--locale="):
            extra_opts["locale"] = arg.split("=", 1)[1]
        elif arg == "--locale" and i + 1 < len(argv):
            i += 1
            extra_opts["locale"] = argv[i]
        elif arg.startswith("--timezone="):
            extra_opts["timezone"] = arg.split("=", 1)[1]
        elif arg == "--timezone" and i + 1 < len(argv):
            i += 1
            extra_opts["timezone"] = argv[i]
        elif arg.startswith("--geolocation="):
            lat, lon = arg.split("=", 1)[1].split(",", 1)
            extra_opts["geolocation"] = {"lat": lat, "lon": lon}
        elif arg == "--geolocation" and i + 1 < len(argv):
            i += 1
            lat, lon = argv[i].split(",", 1)
            extra_opts["geolocation"] = {"lat": lat, "lon": lon}
        elif arg.startswith("--user-agent="):
            extra_opts["user-agent"] = arg.split("=", 1)[1]
        elif arg == "--user-agent" and i + 1 < len(argv):
            i += 1
            extra_opts["user-agent"] = argv[i]
        elif arg.startswith("--grant-permissions="):
            extra_opts["grant-permissions"] = arg.split("=", 1)[1]
        elif arg == "--grant-permissions" and i + 1 < len(argv):
            i += 1
            extra_opts["grant-permissions"] = argv[i]
```

And update `_print_help()`:

```python
    click.echo("  --device=<name>     Emulate a device (e.g. 'iPhone 15')")
    click.echo("  --viewport-size=WxH Set viewport size")
    click.echo("  --locale=<code>     Locale (e.g. en-US)")
    click.echo("  --timezone=<id>     Timezone ID")
    click.echo("  --geolocation=lat,lon Geolocation override")
    click.echo("  --user-agent=<ua>   Custom user agent")
    click.echo("  --grant-perms=P     Comma-separated permissions to grant")
```

- [ ] **Step 4: Commit**

```bash
git add src/patchright_cli/cli.py src/patchright_cli/daemon.py
git commit -m "feat: add device emulation, viewport, locale, timezone, geolocation, UA options"
```

---

### Task 3: `grant-permissions` command

**Files:**
- Modify: `src/patchright_cli/cli.py`
- Modify: `src/patchright_cli/daemon.py`
- Test: `tests/test_daemon_handlers.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_grant_permissions(mock_state, mock_session):
    mock_state.sessions = {"default": mock_session}
    mock_session.context.grant_permissions = AsyncMock()
    response = await handle_command(
        mock_state,
        {"command": "grant-permissions", "args": ["geolocation,camera"], "options": {"origin": "https://example.com"}}
    )
    assert response["success"] is True
    mock_session.context.grant_permissions.assert_awaited_once_with(
        ["geolocation", "camera"], origin="https://example.com"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_daemon_handlers.py::test_grant_permissions -v`
Expected: FAIL because command is not registered.

- [ ] **Step 3: Add CLI help**

In `src/patchright_cli/cli.py`:

Add to `COMMANDS_HELP` under Storage (or a new Permissions category):

```python
    "grant-permissions": "grant-perms <perms>  Grant permissions [--origin=url]",
```

Add to `_print_help()` categories. Insert after Storage:

```python
        ("Permissions", ["grant-permissions"]),
```

- [ ] **Step 4: Add daemon handler**

In `src/patchright_cli/daemon.py`, add:

```python
@register("grant-permissions")
async def cmd_grant_permissions(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    perms_str = args[0] if args else ""
    perms = [p.strip() for p in perms_str.split(",") if p.strip()]
    if not perms:
        return {"success": False, "output": "Usage: grant-permissions <perm1,perm2> [--origin=url]"}
    origin = options.get("origin")
    kwargs = {"permissions": perms}
    if origin:
        kwargs["origin"] = origin
    await session.context.grant_permissions(**kwargs)
    return {"success": True, "output": f"Granted permissions: {', '.join(perms)}"}
```

- [ ] **Step 5: Run test**

Run: `pytest tests/test_daemon_handlers.py::test_grant_permissions -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add tests/test_daemon_handlers.py src/patchright_cli/cli.py src/patchright_cli/daemon.py
git commit -m "feat: add grant-permissions command"
```

---

## Self-Review

**1. Spec coverage:**
- Config file support → Task 1
- Device emulation, viewport, locale, timezone, geolocation, UA → Task 2
- `grant-permissions` → Task 3
- All requirements covered.

**2. Placeholder scan:**
- No TBDs or vague instructions.
- Every code block contains exact implementation.

**3. Type consistency:**
- Config keys use hyphenated lowercase matching CLI convention.
- `viewport` is consistently `{"width": ..., "height": ...}`.
- `geolocation` is consistently `{"lat": ..., "lon": ...}`.
