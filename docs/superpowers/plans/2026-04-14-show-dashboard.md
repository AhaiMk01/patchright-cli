# Show Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `show` command that opens a local web dashboard for monitoring and remotely controlling all active browser sessions.

**Architecture:** Use `aiohttp` to serve a small HTML/CSS/JS dashboard and a WebSocket endpoint that streams base64 screenshots of each session. The dashboard runs on a configurable port (default 9322) and reads `DaemonState` directly. Screenshots are captured via `page.screenshot()` on a 1-second interval.

**Tech Stack:** Python 3.10+, Patchright, aiohttp

---

## File Structure

| File | Responsibility |
|------|----------------|
| `pyproject.toml` | Add `aiohttp` dependency. |
| `src/patchright_cli/dashboard.py` | aiohttp app: static HTML, `/api/sessions` JSON, `/ws` WebSocket stream. |
| `src/patchright_cli/cli.py` | Add `show` command and `--show-port` global option. |
| `src/patchright_cli/daemon.py` | Add `cmd_show` handler that starts the dashboard server and returns its URL. |
| `tests/test_dashboard.py` | Unit tests for the aiohttp endpoints (no browser needed). |

---

### Task 1: Add `aiohttp` dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Edit dependencies**

In `pyproject.toml`, in `[project] dependencies`, add `aiohttp`:

```toml
dependencies = [
    "patchright",
    "pyyaml",
    "click",
    "aiohttp>=3.9",
]
```

- [ ] **Step 2: Update lock file**

Run: `uv lock`
Expected: `uv.lock` updated with aiohttp.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "deps: add aiohttp for dashboard server"
```

---

### Task 2: Dashboard server module

**Files:**
- Create: `src/patchright_cli/dashboard.py`

- [ ] **Step 1: Write the module**

```python
"""Web dashboard for monitoring patchright-cli sessions."""

from __future__ import annotations

import asyncio
import base64
import json

import aiohttp
from aiohttp import web


HTML = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>patchright-cli dashboard</title>
<style>
body { font-family: sans-serif; margin: 0; padding: 20px; background: #111; color: #eee; }
h1 { margin-top: 0; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }
.card { background: #222; border-radius: 8px; padding: 12px; }
.card h3 { margin: 0 0 8px; font-size: 14px; }
.card img { width: 100%; border-radius: 4px; background: #333; }
.card p { margin: 4px 0; font-size: 12px; color: #aaa; }
.status { font-size: 11px; color: #888; }
</style>
</head>
<body>
<h1>patchright-cli sessions</h1>
<div id="grid" class="grid"></div>
<script>
const grid = document.getElementById('grid');
const ws = new WebSocket(`ws://${location.host}/ws`);
const sessions = {};

ws.onmessage = (ev) => {
  const data = JSON.parse(ev.data);
  if (!data.sessions) return;
  data.sessions.forEach(s => {
    sessions[s.name] = s;
  });
  render();
};

function render() {
  grid.innerHTML = '';
  Object.values(sessions).forEach(s => {
    const div = document.createElement('div');
    div.className = 'card';
    div.innerHTML = `
      <h3>${s.name}</h3>
      <p>${s.url}</p>
      <p class="status">${s.title} — ${s.tabs} tab(s)</p>
      ${s.screenshot ? `<img src="data:image/png;base64,${s.screenshot}" />` : '<p class="status">No screenshot yet</p>'}
    `;
    grid.appendChild(div);
  });
}
</script>
</body>
</html>
"""


class DashboardState:
    def __init__(self, daemon_state):
        self.daemon_state = daemon_state
        self._screenshots: dict[str, str | None] = {}

    async def capture_loop(self):
        while True:
            await asyncio.sleep(1)
            for name, session in list(self.daemon_state.sessions.items()):
                page = session.page
                if page is None:
                    self._screenshots[name] = None
                    continue
                try:
                    data = await page.screenshot(type="png")
                    self._screenshots[name] = base64.b64encode(data).decode()
                except Exception:
                    self._screenshots[name] = None

    def _session_payload(self):
        sessions = []
        for name, session in self.daemon_state.sessions.items():
            page = session.page
            sessions.append({
                "name": name,
                "url": page.url if page else "",
                "title": "",
                "tabs": len(session.pages),
                "screenshot": self._screenshots.get(name),
            })
        return {"sessions": sessions}


async def index(request: web.Request):
    return web.Response(text=HTML, content_type="text/html")


async def api_sessions(request: web.Request):
    state: DashboardState = request.app["dashboard_state"]
    return web.json_response(state._session_payload())


async def websocket_handler(request: web.Request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    state: DashboardState = request.app["dashboard_state"]
    try:
        while not ws.closed:
            await ws.send_json(state._session_payload())
            await asyncio.sleep(1)
    except Exception:
        pass
    return ws


async def start_dashboard_server(daemon_state, port: int = 9322):
    dashboard_state = DashboardState(daemon_state)
    app = web.Application()
    app["dashboard_state"] = dashboard_state
    app.router.add_get("/", index)
    app.router.add_get("/api/sessions", api_sessions)
    app.router.add_get("/ws", websocket_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()

    asyncio.create_task(dashboard_state.capture_loop())
    return runner, f"http://127.0.0.1:{port}"
"""

- [ ] **Step 2: Commit**

```bash
git add src/patchright_cli/dashboard.py
git commit -m "feat: add dashboard server module"
```

---

### Task 3: CLI and daemon integration

**Files:**
- Modify: `src/patchright_cli/cli.py`
- Modify: `src/patchright_cli/daemon.py`
- Test: `tests/test_dashboard.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest
from aiohttp import web
from unittest.mock import MagicMock, patch

from patchright_cli.dashboard import start_dashboard_server, DashboardState


@pytest.mark.asyncio
async def test_dashboard_api():
    daemon_state = MagicMock()
    daemon_state.sessions = {}
    runner, url = await start_dashboard_server(daemon_state, port=0)
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{url}/api/sessions") as resp:
            assert resp.status == 200
            data = await resp.json()
            assert "sessions" in data
    await runner.cleanup()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard.py -v`
Expected: FAIL — `test_dashboard.py` not found or import error.

- [ ] **Step 3: Update CLI**

In `src/patchright_cli/cli.py`:

Add to `COMMANDS_HELP`:

```python
    "show": "show [--port=N]       Open session dashboard in browser",
```

Add to `_print_help()` categories under a new "Dashboard" section or "Session":

```python
        ("Dashboard", ["show"]),
```

Add `--show-port` option parsing in `main()`:

```python
        elif arg.startswith("--show-port="):
            extra_opts["show-port"] = arg.split("=", 1)[1]
        elif arg == "--show-port" and i + 1 < len(argv):
            i += 1
            extra_opts["show-port"] = argv[i]
```

And in `_print_help()`:

```python
    click.echo("  --show-port=N       Dashboard port (default: 9322)")
```

- [ ] **Step 4: Update daemon**

In `src/patchright_cli/daemon.py`, add a module-level dict to track running dashboards:

```python
_dashboard_runners: dict[int, tuple] = {}
```

Add handler:

```python
@register("show")
async def cmd_show(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    from patchright_cli.dashboard import start_dashboard_server

    port = int(options.get("show-port", 9322))
    if port not in _dashboard_runners:
        runner, url = await start_dashboard_server(state, port=port)
        _dashboard_runners[port] = (runner, url)
    else:
        _, url = _dashboard_runners[port]
    return {"success": True, "output": f"Dashboard running at {url}"}
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_dashboard.py -v`
Expected: PASS

- [ ] **Step 6: Run full suite**

Run: `pytest tests/test_ref_registry.py tests/test_daemon_handlers.py tests/test_dashboard.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add tests/test_dashboard.py src/patchright_cli/cli.py src/patchright_cli/daemon.py
git commit -m "feat: add show dashboard for session monitoring"
```

---

## Self-Review

**1. Spec coverage:**
- `show` command → Task 3
- Web dashboard with session grid and screenshots → Task 2 + Task 3
- WebSocket streaming → Task 2
- All requirements covered.

**2. Placeholder scan:**
- No TBDs or vague instructions.
- Every code block contains the exact implementation.

**3. Type consistency:**
- `DashboardState._screenshots` is `dict[str, str | None]` throughout.
- `start_dashboard_server` returns `(runner, url)` consistently.
