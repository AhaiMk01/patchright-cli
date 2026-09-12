"""Web dashboard for monitoring patchright-cli sessions."""

from __future__ import annotations

import asyncio
import base64
import binascii
import secrets

from aiohttp import web

PNG_MAGIC = bytes((0x89,)) + b"PNG"

# Upper bound on a submitted annotation body (image + notes, base64-inflated).
ANNOTATION_MAX_BYTES = 48 * 1024 * 1024


def decode_annotation_image(value: str | None) -> bytes | None:
    """Decode a submitted PNG. None when the reviewer sent no image.

    The payload arrives from a browser page, so it is checked rather than
    trusted: a bad base64 blob or a non-PNG is a ValueError, not something
    written to disk under a .png name.
    """
    if value is None:
        return None
    # Type before emptiness: an empty list is falsy, and treating it as "nothing
    # submitted" would let a malformed body pass as a valid no-image review.
    if not isinstance(value, str):
        raise ValueError(f"Annotation image must be a base64 string, got {type(value).__name__}.")
    if not value:
        return None
    if value.startswith("data:"):
        _, _, value = value.partition(",")
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"Annotation image is not valid base64: {exc}") from None
    if not raw.startswith(PNG_MAGIC):
        raise ValueError("Annotation image is not a PNG.")
    return raw


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
        # token -> (future, target). One token per `show --annotate`, so two
        # reviewers can be pointed at two pages without crossing wires.
        self.annotations: dict[str, tuple[asyncio.Future, dict]] = {}

    # -- annotation round-trip ---------------------------------------------

    def open_annotation(self, session_name: str, tab_name: str, page=None) -> tuple[str, asyncio.Future]:
        """Register a pending review. Returns its token and a waiter.

        The page is pinned here rather than looked up per request. Resolving it
        later through the session would follow whatever page the session moved
        to in the meantime -- including the review page itself, which made the
        reviewer annotate a screenshot of their own screen.
        """
        token = secrets.token_urlsafe(12)
        waiter: asyncio.Future = asyncio.get_running_loop().create_future()
        self.annotations[token] = (waiter, {"session": session_name, "tab": tab_name, "page": page})
        return token, waiter

    def resolve_annotation(self, token: str, payload: dict) -> bool:
        """Hand a submitted review to whoever is waiting. False if nobody is."""
        entry = self.annotations.pop(token, None)
        if entry is None:
            return False
        waiter, _ = entry
        if waiter.done():
            return False
        waiter.set_result(payload)
        return True

    def cancel_annotation(self, token: str) -> None:
        entry = self.annotations.pop(token, None)
        if entry is None:
            return
        waiter, _ = entry
        if not waiter.done():
            waiter.cancel()

    def annotation_target(self, token: str) -> dict | None:
        entry = self.annotations.get(token)
        if entry is None:
            return None
        return {k: v for k, v in entry[1].items() if k != "page"}

    def annotation_page(self, token: str):
        """The exact page this token was opened against, or None."""
        entry = self.annotations.get(token)
        return entry[1].get("page") if entry else None

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
            sessions.append(
                {
                    "name": name,
                    "url": page.url if page else "",
                    "title": "",
                    "tabs": len(session.pages),
                    "screenshot": self._screenshots.get(name),
                }
            )
        return {"sessions": sessions}


ANNOTATE_HTML = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Annotate — patchright-cli</title>
<style>
  :root { color-scheme: dark; }
  body { font-family: system-ui, sans-serif; margin: 0; background: #111; color: #eee; }
  header { display: flex; gap: 12px; align-items: center; padding: 10px 16px; background: #1b1b1b;
           position: sticky; top: 0; border-bottom: 1px solid #2c2c2c; flex-wrap: wrap; }
  header h1 { font-size: 14px; margin: 0 auto 0 0; font-weight: 600; }
  button { font: inherit; padding: 6px 12px; border-radius: 6px; border: 1px solid #3a3a3a;
           background: #262626; color: #eee; cursor: pointer; }
  button.on { background: #ff3366; border-color: #ff3366; color: #fff; }
  button.primary { background: #2f7d32; border-color: #2f7d32; }
  button:disabled { opacity: .5; cursor: default; }
  main { padding: 16px; display: grid; grid-template-columns: 1fr 320px; gap: 16px; align-items: start; }
  #stage { position: relative; line-height: 0; background: #000; border-radius: 6px; overflow: hidden; }
  #stage img, #stage canvas { width: 100%; display: block; }
  #stage canvas { position: absolute; inset: 0; cursor: crosshair; }
  aside { display: flex; flex-direction: column; gap: 10px; }
  textarea { width: 100%; min-height: 220px; font: inherit; padding: 10px; border-radius: 6px;
             border: 1px solid #3a3a3a; background: #1b1b1b; color: #eee; resize: vertical; }
  p.hint { font-size: 12px; color: #999; margin: 0; }
  #done { display: none; padding: 40px 16px; text-align: center; font-size: 15px; }
  @media (max-width: 860px) { main { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<header>
  <h1>Annotate this page</h1>
  <button id="box" class="on">Box</button>
  <button id="pen">Pen</button>
  <button id="undo">Undo</button>
  <button id="reload">Recapture</button>
  <button id="submit" class="primary">Send to agent</button>
</header>
<main id="ui">
  <div id="stage">
    <img id="shot" alt="page screenshot">
    <canvas id="pad"></canvas>
  </div>
  <aside>
    <p class="hint">Draw on the screenshot, write what you want changed, then send.
       The agent is waiting on this page.</p>
    <textarea id="notes" placeholder="What should change?"></textarea>
    <p class="hint" id="status"></p>
  </aside>
</main>
<div id="done">Sent. You can close this tab.</div>
<script>
const token = new URLSearchParams(location.search).get('token');
const shot = document.getElementById('shot');
const pad = document.getElementById('pad');
const ctx = pad.getContext('2d');
const status = document.getElementById('status');
let strokes = [], current = null, tool = 'box';

function setTool(name) {
  tool = name;
  document.getElementById('box').classList.toggle('on', name === 'box');
  document.getElementById('pen').classList.toggle('on', name === 'pen');
}
document.getElementById('box').onclick = () => setTool('box');
document.getElementById('pen').onclick = () => setTool('pen');
document.getElementById('undo').onclick = () => { strokes.pop(); redraw(); };
document.getElementById('reload').onclick = () => capture();

function fit() {
  pad.width = shot.naturalWidth || pad.clientWidth;
  pad.height = shot.naturalHeight || pad.clientHeight;
  redraw();
}
shot.onload = fit;
addEventListener('resize', redraw);

function pos(ev) {
  const r = pad.getBoundingClientRect();
  return { x: (ev.clientX - r.left) * pad.width / r.width,
           y: (ev.clientY - r.top) * pad.height / r.height };
}
pad.onpointerdown = (ev) => {
  pad.setPointerCapture(ev.pointerId);
  const p = pos(ev);
  current = tool === 'box' ? { tool, x: p.x, y: p.y, w: 0, h: 0 } : { tool, points: [p] };
};
pad.onpointermove = (ev) => {
  if (!current) return;
  const p = pos(ev);
  if (current.tool === 'box') { current.w = p.x - current.x; current.h = p.y - current.y; }
  else current.points.push(p);
  redraw(current);
};
pad.onpointerup = () => { if (current) { strokes.push(current); current = null; redraw(); } };

function redraw(preview) {
  ctx.clearRect(0, 0, pad.width, pad.height);
  const scale = Math.max(1, pad.width / 900);
  ctx.lineWidth = 3 * scale;
  ctx.strokeStyle = '#ff3366';
  ctx.lineJoin = ctx.lineCap = 'round';
  for (const s of strokes.concat(preview ? [preview] : [])) {
    ctx.beginPath();
    if (s.tool === 'box') ctx.strokeRect(s.x, s.y, s.w, s.h);
    else {
      s.points.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y));
      ctx.stroke();
    }
  }
}

async function capture() {
  status.textContent = 'Capturing...';
  const res = await fetch(`/api/annotate/${token}/shot`, { cache: 'no-store' });
  if (!res.ok) { status.textContent = 'Could not capture the page.'; return; }
  const blob = await res.blob();
  shot.src = URL.createObjectURL(blob);
  status.textContent = '';
}

document.getElementById('submit').onclick = async () => {
  const button = document.getElementById('submit');
  button.disabled = true;
  status.textContent = 'Sending...';
  const flat = document.createElement('canvas');
  flat.width = pad.width; flat.height = pad.height;
  const fctx = flat.getContext('2d');
  fctx.drawImage(shot, 0, 0, flat.width, flat.height);
  fctx.drawImage(pad, 0, 0);
  const res = await fetch('/api/annotate', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      token,
      notes: document.getElementById('notes').value,
      image: flat.toDataURL('image/png'),
      shapes: strokes.length,
    }),
  });
  if (res.ok) {
    document.getElementById('ui').style.display = 'none';
    document.getElementById('done').style.display = 'block';
  } else {
    button.disabled = false;
    status.textContent = 'Nobody is waiting on this link any more.';
  }
};

capture();
</script>
</body>
</html>
"""


def _target_page(state: DashboardState, token: str):
    """The page a given annotation token points at, or None.

    The pinned page wins. Resolving through the session each request followed
    wherever the session had moved since -- including onto the review page
    itself, which had the reviewer annotating a picture of their own screen.
    """
    pinned = state.annotation_page(token)
    if pinned is not None:
        return pinned
    target = state.annotation_target(token)
    if target is None:
        return None
    session = state.daemon_state.sessions.get(target["session"])
    if session is None:
        return None
    tab = session.tabs.get(target["tab"])
    return tab.page if tab is not None and tab.page is not None else session.page


async def index(request: web.Request):
    return web.Response(text=HTML, content_type="text/html")


async def annotate_page(request: web.Request):
    return web.Response(text=ANNOTATE_HTML, content_type="text/html")


async def annotate_shot(request: web.Request):
    """Fresh screenshot of the page this token points at."""
    state: DashboardState = request.app["dashboard_state"]
    page = _target_page(state, request.match_info["token"])
    if page is None:
        raise web.HTTPNotFound(text="No page is waiting for this annotation.")
    try:
        # scale="css" keeps the capture at one image pixel per CSS pixel. Under
        # a device profile the default is the device pixel ratio, which on a
        # long page produced an image the browser then posted back well over the
        # server's body limit -- the submit failed and the reviewer's work was lost.
        data = await page.screenshot(type="png", full_page=True, scale="css")
    except Exception as exc:
        raise web.HTTPServiceUnavailable(text=f"Could not capture the page: {exc}") from None
    return web.Response(body=data, content_type="image/png")


async def annotate_submit(request: web.Request):
    state: DashboardState = request.app["dashboard_state"]
    try:
        payload = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text="Expected a JSON body.") from None

    # The body comes from a browser page; a bare list or string would otherwise
    # reach .get() and surface as a 500 instead of a refusal.
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(text="Expected a JSON object.")

    token = payload.get("token") or ""
    try:
        decode_annotation_image(payload.get("image"))
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from None

    if not state.resolve_annotation(token, payload):
        raise web.HTTPGone(text="Nobody is waiting on this annotation.")
    return web.json_response({"ok": True})


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
    # A full-page annotated PNG re-encoded as a base64 data URL comfortably
    # exceeds aiohttp's 1 MiB default, which rejected the submit with a 413 the
    # reviewer never saw. Bounded, not unbounded: the body is still untrusted.
    app = web.Application(client_max_size=ANNOTATION_MAX_BYTES)
    app["dashboard_state"] = dashboard_state
    app.router.add_get("/", index)
    app.router.add_get("/annotate", annotate_page)
    app.router.add_get("/api/annotate/{token}/shot", annotate_shot)
    app.router.add_post("/api/annotate", annotate_submit)
    app.router.add_get("/api/sessions", api_sessions)
    app.router.add_get("/ws", websocket_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()

    asyncio.create_task(dashboard_state.capture_loop())
    return runner, f"http://127.0.0.1:{port}", dashboard_state
