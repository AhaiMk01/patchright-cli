"""Web dashboard for monitoring patchright-cli sessions."""

from __future__ import annotations

import asyncio
import base64

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
