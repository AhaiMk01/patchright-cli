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

from patchright_cli.snapshot import save_snapshot, take_snapshot

logger = logging.getLogger("patchright-cli.daemon")

DEFAULT_PORT = 9321
DEFAULT_PROFILE_DIR = str(Path.home() / ".patchright-cli" / "profiles" / "default")

# ---------------------------------------------------------------------------
# Session management — multiple named sessions each with their own context
# ---------------------------------------------------------------------------

class Session:
    """A single browser session (one persistent context, multiple pages/tabs)."""

    def __init__(self, name: str, context, pages: list | None = None):
        self.name = name
        self.context = context
        self.pages: list = pages or []
        self.current_tab: int = 0
        self.ref_map: dict[str, dict] = {}
        self.console_messages: list[dict] = []
        self.network_log: list[dict] = []
        self._pending_dialog_action: tuple | None = None
        self._profile_dir: str | None = None
        self._setup_listeners()

    # -- internal helpers ---------------------------------------------------

    def _setup_listeners(self):
        """Attach console / network listeners to all existing pages."""
        for page in self.pages:
            self._attach_page_listeners(page)
        self.context.on("page", self._on_new_page)

    def _on_new_page(self, page):
        self.pages.append(page)
        self.current_tab = len(self.pages) - 1
        self._attach_page_listeners(page)

    def _attach_page_listeners(self, page):
        page.on("console", lambda msg: self.console_messages.append({
            "type": msg.type, "text": msg.text,
            "url": page.url, "ts": time.time(),
        }))
        page.on("request", lambda req: self.network_log.append({
            "method": req.method, "url": req.url,
            "resource": req.resource_type, "ts": time.time(),
        }))
        page.on("dialog", lambda dialog: self._handle_dialog(dialog))

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


class DaemonState:
    """Global daemon state holding all sessions."""

    def __init__(self):
        self.sessions: dict[str, Session] = {}
        self.playwright = None
        self.default_headless: bool = False

    async def get_or_create_session(
        self,
        name: str = "default",
        *,
        headless: bool | None = None,
        persistent: bool = True,
        profile: str | None = None,
        url: str | None = None,
    ) -> Session:
        if name in self.sessions:
            return self.sessions[name]

        if self.playwright is None:
            from patchright.async_api import async_playwright
            self.playwright = await async_playwright().start()

        use_headless = headless if headless is not None else self.default_headless
        profile_dir = profile or str(
            Path.home() / ".patchright-cli" / "profiles" / name
        )
        Path(profile_dir).mkdir(parents=True, exist_ok=True)

        context = await self.playwright.chromium.launch_persistent_context(
            profile_dir,
            channel="chrome",
            headless=use_headless,
            no_viewport=True,
            args=[
                "--disable-blink-features=AutomationControlled",
            ],
        )

        pages = context.pages or []
        if not pages:
            page = await context.new_page()
            pages = [page]

        if url:
            await pages[0].goto(url)

        session = Session(name, context, list(pages))
        self.sessions[name] = session
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
# Command handlers
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

    yaml_text, session.ref_map = await take_snapshot(page)
    snap_path = save_snapshot(yaml_text, cwd)

    output_lines = [
        "### Page",
        f"- Page URL: {url}",
        f"- Page Title: {title}",
        "### Snapshot",
        f"[Snapshot]({snap_path})",
    ]
    return {"success": True, "output": "\n".join(output_lines), "snapshot_path": snap_path}


async def _resolve_ref(session: Session, page, ref: str):
    """Resolve an element ref (e.g. 'e5') to a Playwright locator.

    The snapshot assigns data-patchright-ref attributes to DOM elements,
    so we can locate them directly via CSS selector.
    """
    loc = page.locator(f'[data-patchright-ref="{ref}"]')
    try:
        count = await loc.count()
        if count >= 1:
            return loc.first
    except Exception:
        pass

    raise ValueError(
        f"Could not locate element for ref '{ref}'. "
        "The page may have changed — run 'snapshot' to refresh."
    )


async def handle_command(state: DaemonState, msg: dict) -> dict:
    """Dispatch a single command and return a JSON-serialisable response."""
    cmd = msg.get("command", "")
    args = msg.get("args", [])
    opts = msg.get("options", {})
    cwd = msg.get("cwd")

    session_name = opts.pop("session", "default") or "default"

    try:
        # -- Session / lifecycle commands -----------------------------------
        if cmd == "open":
            url = args[0] if args else None
            session = await state.get_or_create_session(
                session_name,
                headless=opts.get("headless", False),
                persistent=opts.get("persistent", True),
                profile=opts.get("profile"),
                url=url,
            )
            return await _page_info(session, cwd)

        # All other commands require an existing session
        session = state.sessions.get(session_name)
        if session is None:
            return {
                "success": False,
                "output": f"Session '{session_name}' is not open. Run 'open' first.",
            }

        page = session.page

        # -- Navigation -----------------------------------------------------
        if cmd == "goto":
            await page.goto(args[0])
            return await _page_info(session, cwd)

        if cmd == "go-back":
            await page.go_back()
            return await _page_info(session, cwd)

        if cmd == "go-forward":
            await page.go_forward()
            return await _page_info(session, cwd)

        if cmd == "reload":
            await page.reload()
            return await _page_info(session, cwd)

        # -- Core interactions ----------------------------------------------
        if cmd == "click":
            elem = await _resolve_ref(session, page, args[0])
            await elem.click()
            return await _page_info(session, cwd)

        if cmd == "dblclick":
            elem = await _resolve_ref(session, page, args[0])
            await elem.dblclick()
            return await _page_info(session, cwd)

        if cmd == "fill":
            elem = await _resolve_ref(session, page, args[0])
            await elem.fill(args[1])
            return await _page_info(session, cwd)

        if cmd == "type":
            text = args[0] if args else ""
            await page.keyboard.type(text)
            return await _page_info(session, cwd)

        if cmd == "hover":
            elem = await _resolve_ref(session, page, args[0])
            await elem.hover()
            return await _page_info(session, cwd)

        if cmd == "select":
            elem = await _resolve_ref(session, page, args[0])
            await elem.select_option(args[1])
            return await _page_info(session, cwd)

        if cmd == "check":
            elem = await _resolve_ref(session, page, args[0])
            await elem.check()
            return await _page_info(session, cwd)

        if cmd == "uncheck":
            elem = await _resolve_ref(session, page, args[0])
            await elem.uncheck()
            return await _page_info(session, cwd)

        if cmd == "drag":
            src = await _resolve_ref(session, page, args[0])
            dst = await _resolve_ref(session, page, args[1])
            await src.drag_to(dst)
            return await _page_info(session, cwd)

        # -- Snapshot -------------------------------------------------------
        if cmd == "snapshot":
            yaml_text, session.ref_map = await take_snapshot(page)
            snap_path = save_snapshot(yaml_text, cwd)
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

        # -- Eval -----------------------------------------------------------
        if cmd == "eval":
            expr = args[0] if args else ""
            result = await page.evaluate(expr)
            return {"success": True, "output": json.dumps(result, indent=2, default=str)}

        # -- Screenshot -----------------------------------------------------
        if cmd == "screenshot":
            base = Path(cwd) if cwd else Path.cwd()
            snap_dir = base / ".patchright-cli"
            snap_dir.mkdir(parents=True, exist_ok=True)
            ts = int(time.time() * 1000)
            fn = options.get("filename")
            if args and args[0].startswith("e"):
                # Element screenshot
                elem = await _resolve_ref(session, page, args[0])
                filepath = snap_dir / (fn or f"element-{ts}.png")
                await elem.screenshot(path=str(filepath))
            else:
                filepath = snap_dir / (fn or f"page-{ts}.png")
                await page.screenshot(path=str(filepath))
            return {"success": True, "output": f"Screenshot saved to {filepath}"}

        # -- Close ----------------------------------------------------------
        if cmd == "close":
            closed = await state.close_session(session_name)
            return {"success": True, "output": f"Session '{session_name}' closed." if closed else "Session not found."}

        # -- Keyboard -------------------------------------------------------
        if cmd == "press":
            await page.keyboard.press(args[0])
            return await _page_info(session, cwd)

        if cmd == "keydown":
            await page.keyboard.down(args[0])
            return {"success": True, "output": f"Key down: {args[0]}"}

        if cmd == "keyup":
            await page.keyboard.up(args[0])
            return {"success": True, "output": f"Key up: {args[0]}"}

        # -- Mouse ----------------------------------------------------------
        if cmd == "mousemove":
            await page.mouse.move(float(args[0]), float(args[1]))
            return {"success": True, "output": f"Mouse moved to ({args[0]}, {args[1]})"}

        if cmd == "mousedown":
            button = args[0] if args else "left"
            await page.mouse.down(button=button)
            return {"success": True, "output": f"Mouse down: {button}"}

        if cmd == "mouseup":
            button = args[0] if args else "left"
            await page.mouse.up(button=button)
            return {"success": True, "output": f"Mouse up: {button}"}

        if cmd == "mousewheel":
            await page.mouse.wheel(float(args[0]), float(args[1]))
            return {"success": True, "output": f"Mouse wheel: dx={args[0]}, dy={args[1]}"}

        # -- Tabs -----------------------------------------------------------
        if cmd == "tab-list":
            lines = ["### Tabs"]
            for i, p in enumerate(session.pages):
                marker = " *" if i == session.current_tab else ""
                try:
                    t = await p.title()
                except Exception:
                    t = ""
                lines.append(f"  [{i}]{marker} {p.url} — {t}")
            return {"success": True, "output": "\n".join(lines)}

        if cmd == "tab-new":
            url = args[0] if args else "about:blank"
            new_page = await session.context.new_page()
            if url and url != "about:blank":
                await new_page.goto(url)
            # _on_new_page callback already appended it
            return await _page_info(session, cwd)

        if cmd == "tab-close":
            idx = int(args[0]) if args else session.current_tab
            if 0 <= idx < len(session.pages):
                p = session.pages.pop(idx)
                await p.close()
                session.current_tab = max(0, min(session.current_tab, len(session.pages) - 1))
            return {"success": True, "output": f"Tab {idx} closed."}

        if cmd == "tab-select":
            idx = int(args[0])
            if 0 <= idx < len(session.pages):
                session.current_tab = idx
                await session.pages[idx].bring_to_front()
                return await _page_info(session, cwd)
            return {"success": False, "output": f"Invalid tab index: {idx}"}

        # -- Cookies --------------------------------------------------------
        if cmd == "cookie-list":
            cookies = await session.context.cookies()
            return {"success": True, "output": json.dumps(cookies, indent=2, default=str)}

        if cmd == "cookie-get":
            name = args[0]
            cookies = await session.context.cookies()
            found = [c for c in cookies if c.get("name") == name]
            return {"success": True, "output": json.dumps(found, indent=2, default=str)}

        if cmd == "cookie-set":
            cookie_name = args[0]
            cookie_value = args[1]
            url = page.url
            await session.context.add_cookies([{
                "name": cookie_name,
                "value": cookie_value,
                "url": url,
            }])
            return {"success": True, "output": f"Cookie '{cookie_name}' set."}

        if cmd == "cookie-delete":
            cookie_name = args[0]
            # Playwright doesn't have a direct delete — clear and re-add all except target
            cookies = await session.context.cookies()
            await session.context.clear_cookies()
            remaining = [c for c in cookies if c.get("name") != cookie_name]
            if remaining:
                await session.context.add_cookies(remaining)
            return {"success": True, "output": f"Cookie '{cookie_name}' deleted."}

        if cmd == "cookie-clear":
            await session.context.clear_cookies()
            return {"success": True, "output": "All cookies cleared."}

        # -- LocalStorage ---------------------------------------------------
        if cmd == "localstorage-list":
            result = await page.evaluate("() => JSON.stringify(localStorage)")
            return {"success": True, "output": result}

        if cmd == "localstorage-get":
            key = args[0]
            result = await page.evaluate(f"() => localStorage.getItem({json.dumps(key)})")
            return {"success": True, "output": json.dumps(result, default=str)}

        if cmd == "localstorage-set":
            key, value = args[0], args[1]
            await page.evaluate(
                f"() => localStorage.setItem({json.dumps(key)}, {json.dumps(value)})"
            )
            return {"success": True, "output": f"localStorage['{key}'] set."}

        if cmd == "localstorage-delete":
            key = args[0]
            await page.evaluate(f"() => localStorage.removeItem({json.dumps(key)})")
            return {"success": True, "output": f"localStorage['{key}'] deleted."}

        if cmd == "localstorage-clear":
            await page.evaluate("() => localStorage.clear()")
            return {"success": True, "output": "localStorage cleared."}

        # -- DevTools -------------------------------------------------------
        if cmd == "console":
            lines = []
            for m in session.console_messages[-50:]:
                lines.append(f"[{m['type']}] {m['text']}")
            return {"success": True, "output": "\n".join(lines) or "(no console messages)"}

        if cmd == "network":
            lines = []
            for r in session.network_log[-50:]:
                lines.append(f"{r['method']} {r['url']} [{r['resource']}]")
            return {"success": True, "output": "\n".join(lines) or "(no network requests)"}

        # -- Session management ---------------------------------------------
        if cmd == "list":
            lines = ["### Sessions"]
            for sname, s in state.sessions.items():
                marker = " *" if sname == session_name else ""
                tab_count = len(s.pages)
                lines.append(f"  - {sname}{marker}: {tab_count} tab(s)")
            return {"success": True, "output": "\n".join(lines)}

        if cmd == "close-all":
            names = list(state.sessions.keys())
            for n in names:
                await state.close_session(n)
            return {"success": True, "output": f"Closed {len(names)} session(s)."}

        if cmd == "kill-all":
            names = list(state.sessions.keys())
            for n in names:
                await state.close_session(n)
            return {"success": True, "output": f"Killed {len(names)} session(s)."}

        # -- Dialog handling ------------------------------------------------
        if cmd == "dialog-accept":
            text = args[0] if args else None
            session._pending_dialog_action = ("accept", text)
            return {"success": True, "output": "Will accept next dialog" + (f" with '{text}'" if text else "")}

        if cmd == "dialog-dismiss":
            session._pending_dialog_action = ("dismiss", None)
            return {"success": True, "output": "Will dismiss next dialog"}

        # -- Upload ---------------------------------------------------------
        if cmd == "upload":
            filepath = args[0] if args else ""
            elem = await _resolve_ref(session, page, args[1]) if len(args) > 1 else page.locator('input[type="file"]').first
            await elem.set_input_files(filepath)
            return await _page_info(session, cwd)

        # -- Resize ---------------------------------------------------------
        if cmd == "resize":
            w, h = int(args[0]), int(args[1])
            await page.set_viewport_size({"width": w, "height": h})
            return {"success": True, "output": f"Viewport resized to {w}x{h}"}

        # -- State save/load ------------------------------------------------
        if cmd == "state-save":
            base = Path(cwd) if cwd else Path.cwd()
            filepath = args[0] if args else str(base / ".patchright-cli" / "state.json")
            Path(filepath).parent.mkdir(parents=True, exist_ok=True)
            state_data = await session.context.storage_state()
            Path(filepath).write_text(json.dumps(state_data, indent=2), encoding="utf-8")
            return {"success": True, "output": f"State saved to {filepath}"}

        if cmd == "state-load":
            filepath = args[0] if args else ""
            if not filepath or not Path(filepath).exists():
                return {"success": False, "output": f"File not found: {filepath}"}
            state_data = json.loads(Path(filepath).read_text(encoding="utf-8"))
            # Apply cookies
            if state_data.get("cookies"):
                await session.context.add_cookies(state_data["cookies"])
            # Apply localStorage via JS
            for origin_data in state_data.get("origins", []):
                origin = origin_data.get("origin", "")
                ls = origin_data.get("localStorage", [])
                if ls and page.url.startswith(origin):
                    for item in ls:
                        await page.evaluate(
                            f"() => localStorage.setItem({json.dumps(item['name'])}, {json.dumps(item['value'])})"
                        )
            return {"success": True, "output": f"State loaded from {filepath}"}

        # -- Session storage ------------------------------------------------
        if cmd == "sessionstorage-list":
            result = await page.evaluate("() => JSON.stringify(sessionStorage)")
            return {"success": True, "output": result}

        if cmd == "sessionstorage-get":
            key = args[0]
            result = await page.evaluate(f"() => sessionStorage.getItem({json.dumps(key)})")
            return {"success": True, "output": json.dumps(result, default=str)}

        if cmd == "sessionstorage-set":
            key, value = args[0], args[1]
            await page.evaluate(f"() => sessionStorage.setItem({json.dumps(key)}, {json.dumps(value)})")
            return {"success": True, "output": f"sessionStorage['{key}'] set."}

        if cmd == "sessionstorage-delete":
            key = args[0]
            await page.evaluate(f"() => sessionStorage.removeItem({json.dumps(key)})")
            return {"success": True, "output": f"sessionStorage['{key}'] deleted."}

        if cmd == "sessionstorage-clear":
            await page.evaluate("() => sessionStorage.clear()")
            return {"success": True, "output": "sessionStorage cleared."}

        # -- Delete data ----------------------------------------------------
        if cmd == "delete-data":
            session_obj = state.sessions.get(session_name)
            if session_obj and hasattr(session_obj, '_profile_dir') and session_obj._profile_dir:
                import shutil
                await state.close_session(session_name)
                try:
                    shutil.rmtree(session_obj._profile_dir, ignore_errors=True)
                except Exception:
                    pass
                return {"success": True, "output": f"Profile data deleted for '{session_name}'."}
            return {"success": False, "output": "No persistent profile to delete."}

        return {"success": False, "output": f"Unknown command: {cmd}"}

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


async def run_daemon(port: int = DEFAULT_PORT, headless: bool = False):
    """Start the daemon TCP server."""
    state = DaemonState()
    state.default_headless = headless

    async def client_handler(reader, writer):
        await _handle_client(reader, writer, state)

    server = await asyncio.start_server(client_handler, "127.0.0.1", port)
    addr = server.sockets[0].getsockname()
    logger.info("Daemon listening on %s:%s", addr[0], addr[1])
    print(f"patchright-cli daemon listening on {addr[0]}:{addr[1]}", flush=True)

    # Handle graceful shutdown
    shutdown_event = asyncio.Event()

    def _signal_handler():
        logger.info("Shutdown signal received")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)

    try:
        if sys.platform == "win32":
            # On Windows, asyncio signal handlers don't work; just serve forever
            async with server:
                await server.serve_forever()
        else:
            async with server:
                await shutdown_event.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
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

    # Start daemon as a background subprocess
    cmd = [sys.executable, "-m", "patchright_cli.daemon"]
    if headless:
        cmd.append("--headless")
    cmd.extend(["--port", str(port)])

    if sys.platform == "win32":
        # CREATE_NEW_PROCESS_GROUP + DETACHED_PROCESS
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

    # Wait for daemon to be ready
    import time as _time
    for _ in range(30):  # up to 3 seconds
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
