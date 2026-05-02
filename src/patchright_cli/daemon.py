"""Long-running browser server process using Patchright.

Launches a persistent Chrome context with anti-detect settings and listens
on TCP port 9321 for JSON commands from the CLI client.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import struct
import subprocess
import sys
import time
import traceback
from pathlib import Path
from urllib.parse import urlparse

from patchright_cli.ref_registry import RefRegistry
from patchright_cli.snapshot import save_snapshot, take_snapshot

logger = logging.getLogger("patchright-cli.daemon")

DEFAULT_PORT = 9321
DEFAULT_PROFILE_DIR = str(Path.home() / ".patchright-cli" / "profiles" / "default")

_dashboard_runners: dict[int, tuple] = {}

# ---------------------------------------------------------------------------
# Command handler registry
# ---------------------------------------------------------------------------

COMMAND_HANDLERS: dict[str, ...] = {}


def register(name: str):
    """Decorator that registers a command handler."""

    def decorator(fn):
        COMMAND_HANDLERS[name] = fn
        return fn

    return decorator


# ---------------------------------------------------------------------------
# Session management — multiple named sessions each with their own context
# ---------------------------------------------------------------------------


class Session:
    """A single browser session (one persistent context, multiple pages/tabs)."""

    def __init__(self, name: str, context, pages: list | None = None, browser=None, is_attached: bool = False):
        self.name = name
        self.context = context
        self.browser = browser  # Browser handle for CDP-connected sessions (None for launch_persistent_context)
        self.is_attached = is_attached  # True if created via `attach --cdp=...`
        self.pages: list = pages or []
        self.current_tab: int = 0
        self.ref_registry: RefRegistry | None = None
        self.console_messages: list[dict] = []
        self.network_log: list[dict] = []
        self._pending_dialog_action: tuple | None = None
        self._profile_dir: str | None = None
        self._cdp_sessions: dict[int, object] = {}
        self._video_cdp = None
        self._video_frames: list[bytes] = []
        self._video_recording: bool = False
        self._video_chapters: list[tuple[int, str]] = []
        self._history: list[str] = []
        self._history_index: int = -1
        self._codegen: list[str] | None = None

    # -- internal helpers ---------------------------------------------------

    async def setup_listeners(self):
        """Attach console / network listeners to all existing pages."""
        for page in self.pages:
            await self._attach_page_listeners(page)
        self.context.on("page", lambda page: asyncio.ensure_future(self._on_new_page(page)))

    async def _on_new_page(self, page):
        if page not in self.pages:
            self.pages.append(page)
            self.current_tab = len(self.pages) - 1
        await self._attach_page_listeners(page)

    async def _attach_page_listeners(self, page):
        # Use CDP for console messages (Patchright suppresses page.on('console'))
        try:
            cdp = await page.context.new_cdp_session(page)
            await cdp.send("Runtime.enable")
            cdp.on(
                "Runtime.consoleAPICalled",
                lambda event: self.console_messages.append(
                    {
                        "type": event["type"] if event["type"] != "warning" else "warning",
                        "text": " ".join(str(a.get("value", a.get("description", ""))) for a in event.get("args", [])),
                        "url": page.url,
                        "ts": time.time(),
                    }
                ),
            )
            self._cdp_sessions[id(page)] = cdp
        except Exception:
            pass
        page.on("request", lambda req: self._on_request(req))
        page.on("response", lambda resp: asyncio.ensure_future(self._on_response(resp)))
        page.on("dialog", lambda dialog: self._handle_dialog(dialog))

    def _on_request(self, req):
        try:
            entry = {
                "id": len(self.network_log),
                "method": req.method,
                "url": req.url,
                "resource": req.resource_type,
                "ts": time.time(),
                "request_headers": dict(req.headers or {}),
                "post_data": req.post_data,
                "_request": req,
            }
        except Exception:
            entry = {
                "id": len(self.network_log),
                "method": getattr(req, "method", "?"),
                "url": getattr(req, "url", "?"),
                "resource": getattr(req, "resource_type", "?"),
                "ts": time.time(),
                "request_headers": {},
                "post_data": None,
                "_request": req,
            }
        self.network_log.append(entry)

    async def _on_response(self, resp):
        try:
            req = resp.request
        except Exception:
            return
        for entry in reversed(self.network_log):
            if entry.get("_request") is req:
                try:
                    entry["status"] = resp.status
                    entry["status_text"] = resp.status_text
                    entry["response_headers"] = dict(resp.headers or {})
                except Exception:
                    pass
                entry["_response"] = resp
                return

    def _handle_dialog(self, dialog):
        """Auto-handle dialogs based on pending action or auto-dismiss."""
        import asyncio

        action = self._pending_dialog_action
        self._pending_dialog_action = None

        async def _do():
            if action and action[0] == "accept":
                await dialog.accept(action[1] or "")
            elif action and action[0] == "dismiss":
                await dialog.dismiss()
            else:
                await dialog.dismiss()  # default: dismiss

        asyncio.ensure_future(_do())

    # -- page access --------------------------------------------------------

    @property
    def page(self):
        if not self.pages:
            return None
        idx = max(0, min(self.current_tab, len(self.pages) - 1))
        return self.pages[idx]

    def push_history(self, url: str) -> None:
        """Record a URL in our navigation history."""
        self._history = self._history[: self._history_index + 1]
        self._history.append(url)
        self._history_index = len(self._history) - 1

    async def go_back(self) -> str | None:
        if self._history_index <= 0:
            return None
        self._history_index -= 1
        url = self._history[self._history_index]
        await self.page.goto(url)
        return url

    async def go_forward(self) -> str | None:
        if self._history_index >= len(self._history) - 1:
            return None
        self._history_index += 1
        url = self._history[self._history_index]
        await self.page.goto(url)
        return url


class DaemonState:
    """Global daemon state holding all sessions."""

    def __init__(self):
        self.sessions: dict[str, Session] = {}
        self.profile_dirs: dict[str, str] = {}
        self.playwright = None
        self.default_headless: bool = False
        self.idle_timeout: float = 300.0
        self.last_activity: float = time.monotonic()
        self.shutdown_event: asyncio.Event | None = None

    async def get_or_create_session(
        self,
        name: str = "default",
        *,
        headless: bool | None = None,
        persistent: bool = True,
        profile: str | None = None,
        proxy: str | None = None,
        url: str | None = None,
        device: str | None = None,
        viewport: dict | None = None,
        locale: str | None = None,
        timezone: str | None = None,
        geolocation: dict | None = None,
        user_agent: str | None = None,
        grant_permissions: str | None = None,
        cdp_endpoint: str | None = None,
        cdp_headers: dict | None = None,
        cdp_timeout: int = 30000,
    ) -> Session:
        if name in self.sessions:
            return self.sessions[name]

        if self.playwright is None:
            from patchright.async_api import async_playwright

            self.playwright = await async_playwright().start()

        use_headless = headless if headless is not None else self.default_headless
        profile_dir = profile or str(Path.home() / ".patchright-cli" / "profiles" / name)
        Path(profile_dir).mkdir(parents=True, exist_ok=True)

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

        attached_browser = None
        if cdp_endpoint:
            attached_browser = await self.playwright.chromium.connect_over_cdp(
                cdp_endpoint, headers=cdp_headers, timeout=cdp_timeout
            )
            context = await attached_browser.new_context(**context_options)
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
                    # Rebuild proxy URL without credentials
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

        if url:
            await pages[0].goto(url)

        session = Session(
            name,
            context,
            list(pages),
            browser=attached_browser,
            is_attached=cdp_endpoint is not None,
        )
        await session.setup_listeners()
        self.sessions[name] = session
        self.profile_dirs[name] = profile_dir
        return session

    async def close_session(self, name: str) -> bool:
        session = self.sessions.pop(name, None)
        if session is None:
            return False
        try:
            await session.context.close()
        except Exception:
            pass
        return True

    async def shutdown(self):
        for name in list(self.sessions):
            await self.close_session(name)
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _page_info(session: Session, cwd: str | None = None) -> dict:
    """Return standard page info + snapshot after a state-changing command."""
    page = session.page
    if page is None:
        return {"success": True, "output": "No page open."}

    url = page.url
    try:
        title = await page.title()
    except Exception:
        title = ""

    snapshot_text, session.ref_registry = await take_snapshot(page)
    snap_path = save_snapshot(snapshot_text, cwd)

    output_lines = [
        "### Page",
        f"- Page URL: {url}",
        f"- Page Title: {title}",
        "### Snapshot",
        f"[Snapshot]({snap_path})",
    ]
    return {"success": True, "output": "\n".join(output_lines), "snapshot_path": snap_path}


async def _resolve_ref(session: Session, page, ref: str):
    """Resolve an element ref (e.g. 'e5') to a Playwright locator."""
    if session.ref_registry is None:
        raise ValueError("No snapshot available. Run 'snapshot' first.")
    return session.ref_registry.resolve(page, ref)


async def _apply_timeouts(page, options: dict) -> None:
    """Apply per-command timeout overrides from CLI options."""
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


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

# -- Navigation --------------------------------------------------------------


@register("goto")
async def cmd_goto(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    await page.goto(args[0])
    session.push_history(page.url)
    return await _page_info(session, cwd)


@register("go-back")
async def cmd_go_back(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    url = await session.go_back()
    if url is None:
        return {"success": False, "output": "No previous page in history"}
    return await _page_info(session, cwd)


@register("go-forward")
async def cmd_go_forward(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    url = await session.go_forward()
    if url is None:
        return {"success": False, "output": "No next page in history"}
    return await _page_info(session, cwd)


@register("reload")
async def cmd_reload(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    await page.reload()
    return await _page_info(session, cwd)


@register("url")
async def cmd_url(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    return {"success": True, "output": page.url}


@register("title")
async def cmd_title(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    title = await page.title()
    return {"success": True, "output": title}


# -- Core interactions -------------------------------------------------------


@register("click")
async def cmd_click(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    elem = await _resolve_ref(session, page, args[0])
    button = args[1] if len(args) > 1 else "left"
    modifiers = options.get("modifiers", "").split(",") if options.get("modifiers") else []

    # Walk up DOM to find <a> ancestor; if found, navigate directly.
    link_href = await elem.evaluate(
        "el => { while (el) { if (el.tagName === 'A') return el.href; el = el.parentElement; } return null; }"
    )
    if link_href:
        await page.goto(link_href)
        session.push_history(page.url)
    else:
        await elem.click(button=button, modifiers=[m.strip() for m in modifiers if m.strip()] or None)
    return await _page_info(session, cwd)


@register("dblclick")
async def cmd_dblclick(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    elem = await _resolve_ref(session, page, args[0])
    button = args[1] if len(args) > 1 else "left"
    modifiers = options.get("modifiers", "").split(",") if options.get("modifiers") else []
    await elem.dblclick(button=button, modifiers=[m.strip() for m in modifiers if m.strip()] or None)
    return await _page_info(session, cwd)


@register("fill")
async def cmd_fill(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    elem = await _resolve_ref(session, page, args[0])
    await elem.fill(args[1])
    if options.get("submit"):
        await page.keyboard.press("Enter")
    return await _page_info(session, cwd)


@register("type")
async def cmd_type(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    text = args[0] if args else ""
    await page.keyboard.type(text)
    if options.get("submit"):
        await page.keyboard.press("Enter")
    return await _page_info(session, cwd)


@register("hover")
async def cmd_hover(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    elem = await _resolve_ref(session, page, args[0])
    await elem.hover()
    return await _page_info(session, cwd)


@register("select")
async def cmd_select(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    elem = await _resolve_ref(session, page, args[0])
    await elem.select_option(args[1])
    return await _page_info(session, cwd)


@register("check")
async def cmd_check(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    elem = await _resolve_ref(session, page, args[0])
    await elem.check()
    return await _page_info(session, cwd)


@register("uncheck")
async def cmd_uncheck(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    elem = await _resolve_ref(session, page, args[0])
    await elem.uncheck()
    return await _page_info(session, cwd)


@register("drag")
async def cmd_drag(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    src = await _resolve_ref(session, page, args[0])
    dst = await _resolve_ref(session, page, args[1])
    await src.drag_to(dst)
    return await _page_info(session, cwd)


@register("drop")
async def cmd_drop(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    """Drop files or data onto an element (simulates HTML5 drop from outside)."""
    if not args:
        return {"success": False, "output": "Usage: drop <ref> --path=F | --data=mime=value"}
    elem = await _resolve_ref(session, page, args[0])

    path_opt = options.get("path")
    data_opt = options.get("data")
    if not path_opt and not data_opt:
        return {"success": False, "output": "drop requires --path=<file> or --data=<mime=value>"}

    files: list[dict] = []
    if path_opt:
        import base64
        import mimetypes

        paths = [path_opt] if isinstance(path_opt, str) else list(path_opt)
        for p in paths:
            fp = Path(p)
            if not fp.exists():
                return {"success": False, "output": f"File not found: {p}"}
            mime = mimetypes.guess_type(fp.name)[0] or "application/octet-stream"
            files.append(
                {
                    "name": fp.name,
                    "type": mime,
                    "b64": base64.b64encode(fp.read_bytes()).decode("ascii"),
                }
            )

    data_entries: list[tuple[str, str]] = []
    if data_opt:
        raw_list = [data_opt] if isinstance(data_opt, str) else list(data_opt)
        for raw in raw_list:
            if "=" not in raw:
                return {"success": False, "output": f"Invalid --data (need mime=value): {raw}"}
            mime, value = raw.split("=", 1)
            data_entries.append((mime, value))

    js = """
    async (target, { files, dataEntries }) => {
      const dt = new DataTransfer();
      for (const f of files) {
        const bytes = Uint8Array.from(atob(f.b64), c => c.charCodeAt(0));
        dt.items.add(new File([bytes], f.name, { type: f.type }));
      }
      for (const [mime, value] of dataEntries) {
        dt.setData(mime, value);
      }
      const evt = (type) => new DragEvent(type, {
        bubbles: true, cancelable: true, dataTransfer: dt,
      });
      target.dispatchEvent(evt('dragenter'));
      target.dispatchEvent(evt('dragover'));
      target.dispatchEvent(evt('drop'));
    }
    """
    await elem.evaluate(js, {"files": files, "dataEntries": data_entries})
    return await _page_info(session, cwd)


# -- Snapshot ----------------------------------------------------------------


@register("snapshot")
async def cmd_snapshot(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    element_ref = args[0] if args else None
    max_depth = int(options["depth"]) if options.get("depth") is not None else None
    interactive_only = bool(options.get("interactive", False))
    if element_ref:
        elem = await _resolve_ref(session, page, element_ref)
        snapshot_text, session.ref_registry = await take_snapshot(
            page, root_element=elem, max_depth=max_depth, interactive_only=interactive_only
        )
    else:
        snapshot_text, session.ref_registry = await take_snapshot(
            page, max_depth=max_depth, interactive_only=interactive_only
        )
    if options.get("boxes"):
        snapshot_text = await _annotate_with_boxes(page, snapshot_text, session.ref_registry)
    fn = options.get("filename")
    if fn:
        Path(fn).parent.mkdir(parents=True, exist_ok=True)
        Path(fn).write_text(snapshot_text, encoding="utf-8")
        snap_path = fn
    else:
        snap_path = save_snapshot(snapshot_text, cwd)
    url = page.url
    try:
        title = await page.title()
    except Exception:
        title = ""
    output_lines = [
        "### Page",
        f"- Page URL: {url}",
        f"- Page Title: {title}",
        "### Snapshot",
        f"[Snapshot]({snap_path})",
    ]
    return {"success": True, "output": "\n".join(output_lines), "snapshot_path": snap_path}


async def _annotate_with_boxes(page, snapshot_text: str, registry) -> str:
    """Append `[box=x,y,w,h]` to each `[ref=eN]` line in snapshot_text.

    Refs whose locator can't be measured are left unchanged.
    """
    if registry is None:
        return snapshot_text
    import re

    ref_re = re.compile(r"\[ref=(e\d+)\]")
    boxes: dict[str, str] = {}
    for ref in registry.entries:
        try:
            loc = registry.resolve(page, ref)
            box = await loc.bounding_box()
            if box:
                boxes[ref] = f"[box={int(box['x'])},{int(box['y'])},{int(box['width'])},{int(box['height'])}]"
        except Exception:
            continue

    out = []
    for line in snapshot_text.splitlines():
        m = ref_re.search(line)
        if m and m.group(1) in boxes:
            line = line.rstrip() + " " + boxes[m.group(1)]
        out.append(line)
    return "\n".join(out)


@register("generate-locator")
async def cmd_generate_locator(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    """Emit a Playwright locator expression for a ref."""
    if not args:
        return {"success": False, "output": "Usage: generate-locator <ref>"}
    if session.ref_registry is None:
        return {"success": False, "output": "No snapshot taken yet. Run `snapshot` first."}
    ref = args[0].lstrip("@")
    entry = session.ref_registry.entries.get(ref)
    if entry is None:
        return {"success": False, "output": f"Ref @{ref} not found. Re-run `snapshot`."}

    name_part = ""
    if entry.name:
        escaped = entry.name.replace("\\", "\\\\").replace("'", "\\'")
        name_part = f", {{ name: '{escaped}', exact: true }}"
    expr = f"getByRole('{entry.role}'{name_part})"
    if entry.nth > 0:
        expr += f".nth({entry.nth})"
    return {"success": True, "output": expr}


@register("highlight")
async def cmd_highlight(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    """Draw or remove a persistent overlay over an element.

    `highlight <ref>` shows it; `highlight <ref> --hide` hides it; `highlight --hide`
    clears all highlights on the page.
    """
    hide = bool(options.get("hide"))
    style = options.get("style") or "outline: 2px solid #ff3366; outline-offset: 2px;"

    if hide and not args:
        await page.evaluate("() => document.querySelectorAll('[data-patchright-highlight]').forEach(el => el.remove())")
        return {"success": True, "output": "All highlights cleared."}

    if not args:
        return {"success": False, "output": "Usage: highlight <ref> [--style=...] [--hide]"}
    elem = await _resolve_ref(session, page, args[0])

    if hide:
        await elem.evaluate(
            "el => { const id = el.getAttribute('data-patchright-hl-id');"
            " if (id) {"
            '   const o = document.querySelector(`[data-patchright-highlight="${id}"]`);'
            "   if (o) o.remove();"
            "   el.removeAttribute('data-patchright-hl-id');"
            " } }"
        )
        return {"success": True, "output": f"Hid highlight on {args[0]}."}

    js = """
    (el, { style }) => {
      const rect = el.getBoundingClientRect();
      const id = el.getAttribute('data-patchright-hl-id') || ('h' + Math.random().toString(36).slice(2));
      el.setAttribute('data-patchright-hl-id', id);
      let overlay = document.querySelector(`[data-patchright-highlight="${id}"]`);
      if (!overlay) {
        overlay = document.createElement('div');
        overlay.setAttribute('data-patchright-highlight', id);
        overlay.style.cssText = 'position: fixed; pointer-events: none; z-index: 2147483647;';
        document.documentElement.appendChild(overlay);
      }
      overlay.style.left = rect.left + 'px';
      overlay.style.top = rect.top + 'px';
      overlay.style.width = rect.width + 'px';
      overlay.style.height = rect.height + 'px';
      overlay.style.cssText += '; ' + style;
    }
    """
    await elem.evaluate(js, {"style": style})
    return {"success": True, "output": f"Highlighted {args[0]}."}


# -- Eval / Screenshot -------------------------------------------------------


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


@register("text")
async def cmd_text(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    target = args[0] if args else ""
    if not target:
        return {"success": False, "output": "Usage: text <ref|selector>"}
    import re

    if re.fullmatch(r"e\d+", target) and session.ref_registry is not None:
        elem = await _resolve_ref(session, page, target)
    else:
        elem = page.locator(target)
    text = (await elem.text_content()) or ""
    return {"success": True, "output": text}


@register("screenshot")
async def cmd_screenshot(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    base = Path(cwd) if cwd else Path.cwd()
    snap_dir = base / ".patchright-cli"
    snap_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time() * 1000)
    fn = options.get("filename")
    if args and args[0].startswith("e"):
        elem = await _resolve_ref(session, page, args[0])
        filepath = snap_dir / (fn or f"element-{ts}.png")
        await elem.screenshot(path=str(filepath))
    else:
        filepath = snap_dir / (fn or f"page-{ts}.png")
        full_page = bool(options.get("full-page"))
        await page.screenshot(path=str(filepath), full_page=full_page)
    return {"success": True, "output": f"Screenshot saved to {filepath}"}


# -- Keyboard ----------------------------------------------------------------


@register("press")
async def cmd_press(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    await page.keyboard.press(args[0])
    return await _page_info(session, cwd)


@register("keydown")
async def cmd_keydown(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    await page.keyboard.down(args[0])
    return {"success": True, "output": f"Key down: {args[0]}"}


@register("keyup")
async def cmd_keyup(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    await page.keyboard.up(args[0])
    return {"success": True, "output": f"Key up: {args[0]}"}


# -- Mouse -------------------------------------------------------------------


@register("mousemove")
async def cmd_mousemove(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    await page.mouse.move(float(args[0]), float(args[1]))
    return {"success": True, "output": f"Mouse moved to ({args[0]}, {args[1]})"}


@register("mousedown")
async def cmd_mousedown(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    button = args[0] if args else "left"
    await page.mouse.down(button=button)
    return {"success": True, "output": f"Mouse down: {button}"}


@register("mouseup")
async def cmd_mouseup(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    button = args[0] if args else "left"
    await page.mouse.up(button=button)
    return {"success": True, "output": f"Mouse up: {button}"}


@register("mousewheel")
async def cmd_mousewheel(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    await page.mouse.wheel(float(args[0]), float(args[1]))
    return {"success": True, "output": f"Mouse wheel: dx={args[0]}, dy={args[1]}"}


# -- Tabs --------------------------------------------------------------------


@register("tab-list")
async def cmd_tab_list(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    lines = ["### Tabs"]
    for i, p in enumerate(session.pages):
        marker = " *" if i == session.current_tab else ""
        try:
            t = await p.title()
        except Exception:
            t = ""
        lines.append(f"  [{i}]{marker} {p.url} — {t}")
    return {"success": True, "output": "\n".join(lines)}


@register("tab-new")
async def cmd_tab_new(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    url = args[0] if args else "about:blank"
    new_page = await session.context.new_page()
    if url and url != "about:blank":
        await new_page.goto(url)
    return await _page_info(session, cwd)


@register("tab-close")
async def cmd_tab_close(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    idx = int(args[0]) if args else session.current_tab
    if 0 <= idx < len(session.pages):
        p = session.pages.pop(idx)
        session._cdp_sessions.pop(id(p), None)
        await p.close()
        session.current_tab = max(0, min(session.current_tab, len(session.pages) - 1))
    return {"success": True, "output": f"Tab {idx} closed."}


@register("tab-select")
async def cmd_tab_select(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    idx = int(args[0])
    if 0 <= idx < len(session.pages):
        session.current_tab = idx
        await session.pages[idx].bring_to_front()
        return await _page_info(session, cwd)
    return {"success": False, "output": f"Invalid tab index: {idx}"}


# -- Cookies -----------------------------------------------------------------


@register("cookie-list")
async def cmd_cookie_list(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    cookies = await session.context.cookies()
    domain = options.get("domain")
    if domain:
        cookies = [c for c in cookies if domain in c.get("domain", "")]
    path_filter = options.get("path")
    if path_filter:
        cookies = [c for c in cookies if path_filter in c.get("path", "")]
    return {"success": True, "output": json.dumps(cookies, indent=2, default=str)}


@register("cookie-get")
async def cmd_cookie_get(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    name = args[0]
    cookies = await session.context.cookies()
    found = [c for c in cookies if c.get("name") == name]
    return {"success": True, "output": json.dumps(found, indent=2, default=str)}


@register("cookie-set")
async def cmd_cookie_set(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    cookie_name = args[0]
    cookie_value = args[1]
    cookie = {"name": cookie_name, "value": cookie_value}
    if options.get("domain"):
        cookie["domain"] = options["domain"]
        cookie["path"] = options.get("path", "/")
    else:
        cookie["url"] = page.url
    if options.get("httpOnly"):
        cookie["httpOnly"] = True
    if options.get("secure"):
        cookie["secure"] = True
    if options.get("sameSite"):
        cookie["sameSite"] = options["sameSite"]
    if options.get("expires"):
        cookie["expires"] = int(options["expires"])
    if options.get("path"):
        cookie["path"] = options["path"]
    await session.context.add_cookies([cookie])
    return {"success": True, "output": f"Cookie '{cookie_name}' set."}


@register("cookie-delete")
async def cmd_cookie_delete(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    cookie_name = args[0]
    cookies = await session.context.cookies()
    await session.context.clear_cookies()
    remaining = [c for c in cookies if c.get("name") != cookie_name]
    if remaining:
        await session.context.add_cookies(remaining)
    return {"success": True, "output": f"Cookie '{cookie_name}' deleted."}


@register("cookie-clear")
async def cmd_cookie_clear(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    await session.context.clear_cookies()
    return {"success": True, "output": "All cookies cleared."}


@register("cookie-import")
async def cmd_cookie_import(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    filepath = args[0] if args else ""
    if not filepath or not Path(filepath).exists():
        return {"success": False, "output": f"File not found: {filepath}"}
    data = json.loads(Path(filepath).read_text(encoding="utf-8"))
    cookies = data.get("cookies") if isinstance(data, dict) else data
    if not isinstance(cookies, list):
        return {"success": False, "output": "Invalid cookie file format: expected a list or {'cookies': [...]}"}
    if cookies:
        await session.context.add_cookies(cookies)
    return {"success": True, "output": f"Imported {len(cookies)} cookie(s) from {filepath}"}


@register("cookie-export")
async def cmd_cookie_export(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    base = Path(cwd) if cwd else Path.cwd()
    snap_dir = base / ".patchright-cli"
    snap_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time() * 1000)
    filepath = args[0] if args else str(snap_dir / f"cookies-{ts}.json")
    cookies = await session.context.cookies()
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    Path(filepath).write_text(json.dumps(cookies, indent=2, default=str), encoding="utf-8")
    return {"success": True, "output": f"Exported {len(cookies)} cookie(s) to {filepath}"}


# -- LocalStorage ------------------------------------------------------------


@register("localstorage-list")
async def cmd_localstorage_list(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    result = await page.evaluate("() => JSON.stringify(localStorage)")
    return {"success": True, "output": result}


@register("localstorage-get")
async def cmd_localstorage_get(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    key = args[0]
    result = await page.evaluate(f"() => localStorage.getItem({json.dumps(key)})")
    return {"success": True, "output": json.dumps(result, default=str)}


@register("localstorage-set")
async def cmd_localstorage_set(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    key, value = args[0], args[1]
    await page.evaluate(f"() => localStorage.setItem({json.dumps(key)}, {json.dumps(value)})")
    return {"success": True, "output": f"localStorage['{key}'] set."}


@register("localstorage-delete")
async def cmd_localstorage_delete(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    key = args[0]
    await page.evaluate(f"() => localStorage.removeItem({json.dumps(key)})")
    return {"success": True, "output": f"localStorage['{key}'] deleted."}


@register("localstorage-clear")
async def cmd_localstorage_clear(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    await page.evaluate("() => localStorage.clear()")
    return {"success": True, "output": "localStorage cleared."}


# -- DevTools / Network ------------------------------------------------------


@register("console")
async def cmd_console(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    level_filter = args[0] if args else None
    filter_aliases = {"warn": "warning"}
    if level_filter:
        level_filter = filter_aliases.get(level_filter, level_filter)
    lines = []
    for m in session.console_messages[-50:]:
        if level_filter and m["type"] != level_filter:
            continue
        lines.append(f"[{m['type']}] {m['text']}")
    if options.get("clear"):
        session.console_messages.clear()
    return {"success": True, "output": "\n".join(lines) or "(no console messages)"}


@register("network")
async def cmd_network(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    include_static = bool(options.get("static"))
    static_types = {"image", "font", "stylesheet", "script", "media"}
    lines = []
    for r in session.network_log[-50:]:
        if not include_static and r["resource"] in static_types:
            continue
        status = r.get("status")
        status_part = f" {status}" if status is not None else ""
        lines.append(f"#{r['id']} {r['method']}{status_part} {r['url']} [{r['resource']}]")
    if options.get("clear"):
        session.network_log.clear()
    return {"success": True, "output": "\n".join(lines) or "(no network requests)"}


@register("request")
async def cmd_request(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    if not args:
        return {"success": False, "output": "Usage: request <id>"}
    try:
        req_id = int(args[0])
    except ValueError:
        return {"success": False, "output": f"Invalid request id: {args[0]}"}

    entry = next((e for e in session.network_log if e["id"] == req_id), None)
    if entry is None:
        return {"success": False, "output": f"No request with id {req_id}. Use `network` to list."}

    lines = [
        f"### Request #{entry['id']}",
        f"- {entry['method']} {entry['url']}",
        f"- Resource: {entry['resource']}",
    ]
    status = entry.get("status")
    if status is not None:
        status_text = entry.get("status_text") or ""
        lines.append(f"- Status: {status} {status_text}".rstrip())

    req_headers = entry.get("request_headers") or {}
    if req_headers:
        lines.append("### Request Headers")
        for k, v in req_headers.items():
            lines.append(f"- {k}: {v}")

    post_data = entry.get("post_data")
    if post_data:
        lines.append("### Request Body")
        lines.append(post_data if len(post_data) < 4000 else post_data[:4000] + "\n... (truncated)")

    resp_headers = entry.get("response_headers") or {}
    if resp_headers:
        lines.append("### Response Headers")
        for k, v in resp_headers.items():
            lines.append(f"- {k}: {v}")

    if options.get("body"):
        resp = entry.get("_response")
        if resp is not None:
            try:
                body = await resp.text()
                lines.append("### Response Body")
                lines.append(body if len(body) < 8000 else body[:8000] + "\n... (truncated)")
            except Exception as e:
                lines.append(f"### Response Body\n(could not read: {e})")

    return {"success": True, "output": "\n".join(lines)}


@register("network-state-set")
async def cmd_network_state_set(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    state_val = args[0] if args else ""
    if state_val not in ("online", "offline"):
        return {"success": False, "output": "Usage: network-state-set <online|offline>"}
    cdp = await page.context.new_cdp_session(page)
    if state_val == "offline":
        await cdp.send(
            "Network.emulateNetworkConditions",
            {"offline": True, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1},
        )
    else:
        await cdp.send(
            "Network.emulateNetworkConditions",
            {"offline": False, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1},
        )
    return {"success": True, "output": f"Network state set to {state_val}."}


# -- Dialog handling ---------------------------------------------------------


@register("dialog-accept")
async def cmd_dialog_accept(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    text = args[0] if args else None
    session._pending_dialog_action = ("accept", text)
    return {"success": True, "output": "Will accept next dialog" + (f" with '{text}'" if text else "")}


@register("dialog-dismiss")
async def cmd_dialog_dismiss(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    session._pending_dialog_action = ("dismiss", None)
    return {"success": True, "output": "Will dismiss next dialog"}


# -- Upload / Resize ---------------------------------------------------------


@register("upload")
async def cmd_upload(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    filepath = args[0] if args else ""
    elem = await _resolve_ref(session, page, args[1]) if len(args) > 1 else page.locator('input[type="file"]').first
    await elem.set_input_files(filepath)
    return await _page_info(session, cwd)


@register("resize")
async def cmd_resize(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    w, h = int(args[0]), int(args[1])
    await page.set_viewport_size({"width": w, "height": h})
    return {"success": True, "output": f"Viewport resized to {w}x{h}"}


# -- Permissions -------------------------------------------------------------


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


# -- State save/load ---------------------------------------------------------


@register("state-save")
async def cmd_state_save(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    base = Path(cwd) if cwd else Path.cwd()
    filepath = args[0] if args else str(base / ".patchright-cli" / "state.json")
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    state_data = await session.context.storage_state()
    Path(filepath).write_text(json.dumps(state_data, indent=2), encoding="utf-8")
    return {"success": True, "output": f"State saved to {filepath}"}


@register("state-load")
async def cmd_state_load(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    filepath = args[0] if args else ""
    if not filepath or not Path(filepath).exists():
        return {"success": False, "output": f"File not found: {filepath}"}
    state_data = json.loads(Path(filepath).read_text(encoding="utf-8"))
    if state_data.get("cookies"):
        await session.context.add_cookies(state_data["cookies"])
    ls_applied = 0
    ls_skipped = 0
    for origin_data in state_data.get("origins", []):
        origin = origin_data.get("origin", "")
        ls = origin_data.get("localStorage", [])
        if ls and page.url.startswith(origin):
            for item in ls:
                await page.evaluate(
                    f"() => localStorage.setItem({json.dumps(item['name'])}, {json.dumps(item['value'])})"
                )
            ls_applied += len(ls)
        elif ls:
            ls_skipped += len(ls)
    msg = f"State loaded from {filepath}"
    if ls_skipped:
        msg += f" (note: {ls_skipped} localStorage item(s) skipped — navigate to the matching origin first)"
    return {"success": True, "output": msg}


# -- Session storage ---------------------------------------------------------


@register("sessionstorage-list")
async def cmd_sessionstorage_list(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    result = await page.evaluate("() => JSON.stringify(sessionStorage)")
    return {"success": True, "output": result}


@register("sessionstorage-get")
async def cmd_sessionstorage_get(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    key = args[0]
    result = await page.evaluate(f"() => sessionStorage.getItem({json.dumps(key)})")
    return {"success": True, "output": json.dumps(result, default=str)}


@register("sessionstorage-set")
async def cmd_sessionstorage_set(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    key, value = args[0], args[1]
    await page.evaluate(f"() => sessionStorage.setItem({json.dumps(key)}, {json.dumps(value)})")
    return {"success": True, "output": f"sessionStorage['{key}'] set."}


@register("sessionstorage-delete")
async def cmd_sessionstorage_delete(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    key = args[0]
    await page.evaluate(f"() => sessionStorage.removeItem({json.dumps(key)})")
    return {"success": True, "output": f"sessionStorage['{key}'] deleted."}


@register("sessionstorage-clear")
async def cmd_sessionstorage_clear(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    await page.evaluate("() => sessionStorage.clear()")
    return {"success": True, "output": "sessionStorage cleared."}


# -- Route (request interception) --------------------------------------------


@register("route")
async def cmd_route(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    pattern = args[0] if args else "**/*"
    status = int(options.get("status", 200))
    body = options.get("body", "")
    content_type = options.get("content-type", "text/plain")
    headers = {}
    if options.get("header"):
        for h in options["header"] if isinstance(options["header"], list) else [options["header"]]:
            k, _, v = h.partition(":")
            headers[k.strip()] = v.strip()
    remove_headers = [h.strip() for h in options.get("remove-header", "").split(",") if h.strip()]

    async def _route_handler(route):
        if headers or remove_headers:
            resp_headers = dict(headers)
            for rh in remove_headers:
                resp_headers.pop(rh, None)
            await route.fulfill(status=status, body=body, content_type=content_type, headers=resp_headers)
        else:
            await route.fulfill(status=status, body=body, content_type=content_type)

    if not hasattr(session, "_routes"):
        session._routes = {}
    await page.route(pattern, _route_handler)
    session._routes[pattern] = _route_handler
    return {"success": True, "output": f"Route added: {pattern} → status={status}"}


@register("route-list")
async def cmd_route_list(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    routes = getattr(session, "_routes", {})
    if not routes:
        return {"success": True, "output": "(no active routes)"}
    lines = ["### Active Routes"]
    for pat in routes:
        lines.append(f"  - {pat}")
    return {"success": True, "output": "\n".join(lines)}


@register("unroute")
async def cmd_unroute(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    pattern = args[0] if args else None
    routes = getattr(session, "_routes", {})
    if pattern:
        handler = routes.pop(pattern, None)
        if handler:
            await page.unroute(pattern, handler)
        return {"success": True, "output": f"Route removed: {pattern}"}
    else:
        for pat, handler in routes.items():
            await page.unroute(pat, handler)
        routes.clear()
        return {"success": True, "output": "All routes removed."}


# -- Run code ----------------------------------------------------------------


@register("run-code")
async def cmd_run_code(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    code = args[0] if args else ""
    result = await page.evaluate(f"async () => {{ {code} }}")
    return {
        "success": True,
        "output": json.dumps(result, indent=2, default=str) if result is not None else "Code executed.",
    }


# -- Tracing -----------------------------------------------------------------


@register("tracing-start")
async def cmd_tracing_start(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    await session.context.tracing.start(screenshots=True, snapshots=True, sources=True)
    return {"success": True, "output": "Tracing started."}


@register("tracing-stop")
async def cmd_tracing_stop(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    base = Path(cwd) if cwd else Path.cwd()
    snap_dir = base / ".patchright-cli"
    snap_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time() * 1000)
    filepath = snap_dir / f"trace-{ts}.zip"
    await session.context.tracing.stop(path=str(filepath))
    return {"success": True, "output": f"Tracing saved to {filepath}"}


# -- Video recording ---------------------------------------------------------


@register("video-start")
async def cmd_video_start(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    if session._video_recording:
        return {"success": False, "output": "Video recording is already in progress."}
    cdp = await page.context.new_cdp_session(page)
    session._video_cdp = cdp
    session._video_frames = []
    session._video_recording = True

    def _on_frame(event):
        import base64

        session._video_frames.append(base64.b64decode(event["data"]))
        asyncio.ensure_future(cdp.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]}))

    cdp.on("Page.screencastFrame", _on_frame)
    await cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 80, "maxWidth": 1280, "maxHeight": 720})
    return {"success": True, "output": "Video recording started."}


@register("video-stop")
async def cmd_video_stop(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    if not session._video_recording or not session._video_cdp:
        return {"success": False, "output": "No video recording in progress."}
    try:
        await session._video_cdp.send("Page.stopScreencast")
    except Exception:
        pass
    session._video_recording = False
    frames = session._video_frames
    session._video_frames = []
    session._video_cdp = None
    chapters = list(session._video_chapters)
    session._video_chapters = []

    if not frames:
        return {"success": False, "output": "No video frames were captured."}

    base = Path(cwd) if cwd else Path.cwd()
    snap_dir = base / ".patchright-cli"
    snap_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time() * 1000)

    if chapters:
        chapters_path = snap_dir / f"video-{ts}-chapters.json"
        chapters_path.write_text(
            json.dumps([{"frame": f, "title": t} for f, t in chapters], indent=2),
            encoding="utf-8",
        )

    fn = options.get("filename")
    video_path = snap_dir / (fn or f"video-{ts}.webm")
    try:
        import shutil
        import tempfile

        if not shutil.which("ffmpeg"):
            raise FileNotFoundError("ffmpeg not found")

        with tempfile.TemporaryDirectory() as tmpdir:
            for i, frame_data in enumerate(frames):
                Path(tmpdir, f"frame-{i:06d}.jpg").write_bytes(frame_data)
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-y",
                "-framerate",
                "5",
                "-i",
                str(Path(tmpdir, "frame-%06d.jpg")),
                "-c:v",
                "libvpx",
                "-b:v",
                "1M",
                str(video_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            if proc.returncode != 0:
                raise RuntimeError("ffmpeg failed")
        return {"success": True, "output": f"Video saved to {video_path} ({len(frames)} frames)"}
    except (FileNotFoundError, RuntimeError):
        frames_dir = snap_dir / f"video-{ts}-frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        for i, frame_data in enumerate(frames):
            (frames_dir / f"frame-{i:04d}.jpg").write_bytes(frame_data)
        return {
            "success": True,
            "output": f"Saved {len(frames)} frames to {frames_dir}/ (install ffmpeg for video output)",
        }


@register("video-chapter")
async def cmd_video_chapter(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    if not session._video_recording:
        return {"success": False, "output": "No video recording in progress."}
    title = args[0] if args else "Chapter"
    frame_index = len(session._video_frames)
    session._video_chapters.append((frame_index, title))
    return {"success": True, "output": f"Chapter '{title}' added at frame {frame_index}."}


# -- PDF ---------------------------------------------------------------------


@register("pdf")
async def cmd_pdf(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    base = Path(cwd) if cwd else Path.cwd()
    snap_dir = base / ".patchright-cli"
    snap_dir.mkdir(parents=True, exist_ok=True)
    fn = options.get("filename")
    ts = int(time.time() * 1000)
    filepath = snap_dir / (fn or f"page-{ts}.pdf")
    await page.pdf(path=str(filepath))
    return {"success": True, "output": f"PDF saved to {filepath}"}


# -- Scroll & Wait (new) -----------------------------------------------------


@register("scroll")
async def cmd_scroll(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    dx = float(args[0]) if len(args) > 0 else 0
    dy = float(args[1]) if len(args) > 1 else 0
    await page.mouse.wheel(dx, dy)
    return {"success": True, "output": f"Scrolled by ({dx}, {dy})"}


@register("scroll-to")
async def cmd_scroll_to(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    elem = await _resolve_ref(session, page, args[0])
    await elem.scroll_into_view_if_needed()
    return {"success": True, "output": "Scrolled element into view"}


@register("wait")
async def cmd_wait(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    ms = int(args[0]) if args else 0
    await asyncio.sleep(ms / 1000)
    return {"success": True, "output": f"Waited {ms}ms"}


@register("wait-for")
async def cmd_wait_for(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    elem = await _resolve_ref(session, page, args[0])
    state_arg = options.get("state", "visible")
    await elem.wait_for(state=state_arg)
    return {"success": True, "output": f"Element is {state_arg}"}


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


_CODEGEN_RECORDABLE = {
    "goto",
    "go-back",
    "go-forward",
    "reload",
    "click",
    "dblclick",
    "fill",
    "type",
    "hover",
    "select",
    "check",
    "uncheck",
    "drag",
    "press",
    "keydown",
    "keyup",
    "mousemove",
    "mousedown",
    "mouseup",
    "mousewheel",
    "scroll",
    "scroll-to",
    "wait",
    "wait-for",
    "tab-new",
    "tab-close",
    "tab-select",
    "upload",
    "resize",
    "cookie-set",
    "cookie-delete",
    "cookie-clear",
    "localstorage-set",
    "localstorage-delete",
    "localstorage-clear",
    "sessionstorage-set",
    "sessionstorage-delete",
    "sessionstorage-clear",
    "state-load",
    "route",
    "unroute",
    "network-state-set",
    "dialog-accept",
    "dialog-dismiss",
    "screenshot",
    "pdf",
}


@register("codegen")
async def cmd_codegen(session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState) -> dict:
    session._codegen = []
    if page:
        url = page.url
        session._codegen.append(f'patchright-cli open "{url}"')
    return {"success": True, "output": "Recording started. Run 'codegen-stop' to save the script."}


@register("codegen-stop")
async def cmd_codegen_stop(
    session: Session, page, args: list, options: dict, cwd: str | None, state: DaemonState
) -> dict:
    if session._codegen is None:
        return {"success": False, "output": "No recording in progress. Run 'codegen' first."}
    lines = ["#!/usr/bin/env bash", "set -e", ""] + session._codegen
    script = "\n".join(lines) + "\n"
    session._codegen = None

    base = Path(cwd) if cwd else Path.cwd()
    snap_dir = base / ".patchright-cli"
    snap_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time() * 1000)
    filepath = args[0] if args else str(snap_dir / f"script-{ts}.sh")
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    Path(filepath).write_text(script, encoding="utf-8")
    return {"success": True, "output": f"Saved {len(lines) - 3} command(s) to {filepath}"}


# ---------------------------------------------------------------------------
# Command dispatch
# ---------------------------------------------------------------------------


async def handle_command(state: DaemonState, msg: dict) -> dict:
    """Dispatch a single command and return a JSON-serialisable response."""
    cmd = msg.get("command", "")
    args = msg.get("args", [])
    options = dict(msg.get("options", {}))
    cwd = msg.get("cwd")

    # `requests` is an alias for `network` (matches playwright-cli naming).
    if cmd == "requests":
        cmd = "network"

    session_name = options.pop("session", "default") or "default"

    try:
        # -- Session / lifecycle commands -----------------------------------
        if cmd == "open":
            url = args[0] if args else None
            headless = options.get("headless", False)
            if options.get("headed"):
                headless = False
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
            if session.page:
                session.push_history(session.page.url)
            return await _page_info(session, cwd)

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

        if cmd == "delete-data":
            import shutil

            if session_name in state.sessions:
                await state.close_session(session_name)
            profile_dir = state.profile_dirs.pop(session_name, None)
            if profile_dir and Path(profile_dir).exists():
                shutil.rmtree(profile_dir, ignore_errors=True)
                return {"success": True, "output": f"Profile data deleted for '{session_name}'."}
            return {"success": False, "output": "No persistent profile to delete."}

        # Dashboard command — no session required
        if cmd == "show":
            handler = COMMAND_HANDLERS.get("show")
            return await handler(None, None, args, options, cwd, state)

        # All other commands require an existing session
        session = state.sessions.get(session_name)
        if session is None:
            return {
                "success": False,
                "output": f"Session '{session_name}' is not open. Run 'open' first.",
            }

        page = session.page

        # Commands that don't need a page
        if cmd == "list":
            lines = ["### Sessions"]
            for sname, s in state.sessions.items():
                marker = " *" if sname == session_name else ""
                tab_count = len(s.pages)
                lines.append(f"  - {sname}{marker}: {tab_count} tab(s)")
            return {"success": True, "output": "\n".join(lines)}

        if cmd == "close":
            closed = await state.close_session(session_name)
            return {"success": True, "output": f"Session '{session_name}' closed." if closed else "Session not found."}

        if cmd == "detach":
            target = state.sessions.get(session_name)
            if target is None:
                return {"success": False, "output": f"Session '{session_name}' not found."}
            if not target.is_attached:
                return {
                    "success": False,
                    "output": f"Session '{session_name}' was not attached. Use `close` for sessions created via `open`.",
                }
            state.sessions.pop(session_name, None)
            # Disconnect from external Chrome without killing it. For CDP-attached
            # sessions, browser.close() only severs the connection.
            try:
                if target.browser is not None:
                    await target.browser.close()
            except Exception:
                pass
            return {"success": True, "output": f"Detached from '{session_name}' (external browser kept running)."}

        if cmd == "close-all":
            names = list(state.sessions.keys())
            for n in names:
                await state.close_session(n)
            return {"success": True, "output": f"Closed {len(names)} session(s)."}

        if cmd == "kill-all":
            names = list(state.sessions.keys())
            for n in names:
                s = state.sessions.get(n)
                if s:
                    try:
                        for p in s.pages:
                            try:
                                await p.close()
                            except Exception:
                                pass
                        await s.context.close()
                    except Exception:
                        pass
                    state.sessions.pop(n, None)
            if state.shutdown_event:
                state.shutdown_event.set()
            return {"success": True, "output": f"Killed {len(names)} session(s) and stopping daemon."}

        if page is None:
            return {
                "success": False,
                "output": "No page open. Run 'tab-new' to create one, or 'close' and 'open' again.",
            }

        await _apply_timeouts(page, options)

        handler = COMMAND_HANDLERS.get(cmd)
        if handler is None:
            return {"success": False, "output": f"Unknown command: {cmd}"}

        result = await handler(session, page, args, options, cwd, state)
        if result.get("success") and session._codegen is not None and cmd in _CODEGEN_RECORDABLE:
            args_quoted = [f'"{a}"' if " " in a else a for a in args]
            opt_parts = []
            for k, v in options.items():
                if k in ("session", "headless", "persistent", "profile", "proxy"):
                    continue
                if v is True:
                    opt_parts.append(f"--{k}")
                elif isinstance(v, str):
                    opt_parts.append(f"--{k}={v}")
            cmd_line = " ".join(["patchright-cli"] + opt_parts + [cmd] + args_quoted)
            session._codegen.append(cmd_line)
        return result

    except Exception as e:
        logger.error("Command %s failed: %s", cmd, traceback.format_exc())
        return {"success": False, "output": f"Error: {e}"}


# ---------------------------------------------------------------------------
# TCP server
# ---------------------------------------------------------------------------


async def _read_message(reader: asyncio.StreamReader) -> dict | None:
    """Read a length-prefixed JSON message from the stream."""
    header = await reader.readexactly(4)
    length = struct.unpack("!I", header)[0]
    data = await reader.readexactly(length)
    return json.loads(data.decode("utf-8"))


async def _write_message(writer: asyncio.StreamWriter, obj: dict):
    """Write a length-prefixed JSON message to the stream."""
    data = json.dumps(obj, default=str).encode("utf-8")
    writer.write(struct.pack("!I", len(data)) + data)
    await writer.drain()


async def _handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    state: DaemonState,
):
    """Handle a single client connection: read one command, respond, close."""
    state.last_activity = time.monotonic()
    addr = writer.get_extra_info("peername")
    logger.debug("Client connected: %s", addr)
    try:
        msg = await _read_message(reader)
        if msg is None:
            return
        response = await handle_command(state, msg)
        await _write_message(writer, response)
    except asyncio.IncompleteReadError:
        logger.debug("Client disconnected prematurely")
    except Exception as e:
        logger.error("Error handling client: %s", e)
        try:
            await _write_message(writer, {"success": False, "output": f"Daemon error: {e}"})
        except Exception:
            pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def idle_watchdog(state: DaemonState):
    """Shut down the daemon after idle_timeout seconds of inactivity."""
    while True:
        await asyncio.sleep(30)
        if time.monotonic() - state.last_activity > state.idle_timeout:
            logger.info("Idle timeout reached; shutting down daemon.")
            if state.shutdown_event:
                state.shutdown_event.set()
            break


async def run_daemon(port: int = DEFAULT_PORT, headless: bool = False):
    """Start the daemon TCP server."""
    state = DaemonState()
    state.default_headless = headless
    state.shutdown_event = asyncio.Event()

    async def client_handler(reader, writer):
        await _handle_client(reader, writer, state)

    server = await asyncio.start_server(client_handler, "127.0.0.1", port)
    addr = server.sockets[0].getsockname()
    logger.info("Daemon listening on %s:%s", addr[0], addr[1])
    print(f"patchright-cli daemon listening on {addr[0]}:{addr[1]}", flush=True)

    shutdown_event = state.shutdown_event

    def _signal_handler():
        logger.info("Shutdown signal received")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)

    watchdog_task = asyncio.create_task(idle_watchdog(state))

    try:
        async with server:
            await shutdown_event.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        watchdog_task.cancel()
        try:
            await watchdog_task
        except asyncio.CancelledError:
            pass
        logger.info("Shutting down daemon...")
        await state.shutdown()
        server.close()
        await server.wait_closed()
        logger.info("Daemon stopped.")


def start_daemon(port: int = DEFAULT_PORT, headless: bool = False):
    """Entry point to start the daemon (blocking)."""
    logging.basicConfig(
        level=logging.DEBUG if os.environ.get("PATCHRIGHT_DEBUG") else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    asyncio.run(run_daemon(port, headless))


def ensure_daemon_running(port: int = DEFAULT_PORT, headless: bool = False) -> bool:
    """Check if daemon is running; if not, start it in background. Returns True if started."""
    import socket as _socket

    try:
        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        sock.settimeout(1)
        sock.connect(("127.0.0.1", port))
        sock.close()
        return False  # Already running
    except (ConnectionRefusedError, OSError, TimeoutError):
        pass

    cmd = [sys.executable, "-m", "patchright_cli.daemon"]
    if headless:
        cmd.append("--headless")
    cmd.extend(["--port", str(port)])

    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
        )
    else:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

    import time as _time

    for _ in range(30):
        _time.sleep(0.1)
        try:
            sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            sock.settimeout(1)
            sock.connect(("127.0.0.1", port))
            sock.close()
            return True
        except (ConnectionRefusedError, OSError, TimeoutError):
            continue

    raise RuntimeError(f"Failed to start daemon on port {port}")


# Allow running daemon directly: python -m patchright_cli.daemon
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="patchright-cli daemon")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--headless", action="store_true")
    parsed = parser.parse_args()
    start_daemon(parsed.port, parsed.headless)
