import asyncio
import base64
import inspect
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.tools import ToolResult
from mcp.types import ImageContent
from pydantic import Field

CLI = (sys.executable, "-m", "patchright_cli")
DAEMON_PORT = int(os.environ.get("PATCHRIGHT_CLI_MCP_DAEMON_PORT", "9321"))
MCP_ROOT = Path.cwd().resolve()
RUNTIME = (MCP_ROOT / ".patchright-cli" / "mcp-runtime").resolve()
OUTPUT_ROOT = (MCP_ROOT / "output").resolve()
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")

server = FastMCP("Patchright")

TOOL_SPECS = {
    "patchright_open": {
        "description": "CLI command: open Open Chrome and optionally navigate to a URL. Creates or reuses "
        "the named session.",
        "params": [
            ("url", "string", False, "", "Initial URL."),
            ("headless", "boolean", False, True, "Run headless."),
            ("persistent", "boolean", False, False, "Use persistent profile."),
            ("profile", "string", False, "", "Profile path under /patchright."),
            ("proxy", "string", False, "", "HTTP, HTTPS or SOCKS proxy URL."),
            ("device", "string", False, "", "Playwright device name."),
            ("viewport", "string", False, "", "Viewport as WIDTHxHEIGHT."),
            ("locale", "string", False, "", "Browser locale, for example en-GB."),
            ("timezone", "string", False, "", "IANA timezone."),
            ("geolocation", "string", False, "", "Latitude and longitude as LAT,LON."),
            ("user_agent", "string", False, "", "Custom user agent."),
            ("permissions", "string", False, "", "Comma-separated browser permissions."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_attach": {
        "description": "CLI command: attach Attach the named session to an existing Chrome CDP endpoint.",
        "params": [
            ("cdp_url", "string", True, None, "Chrome DevTools Protocol endpoint URL."),
            ("headless", "boolean", False, True, "Treat attached browser as headless."),
            (
                "context",
                "string",
                False,
                "new",
                "Browser context mode. Use 'new' for a fresh isolated context or 'host' to reuse the host browser context.",
            ),
            ("cdp_timeout", "number", False, 30000, "CDP connection timeout in milliseconds."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_goto": {
        "description": "CLI command: goto Navigate the current page to a URL.",
        "params": [
            ("url", "string", True, None, "Destination URL."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_click": {
        "description": "CLI command: click Click an element reference.",
        "params": [
            ("ref", "string", True, None, "Snapshot element reference."),
            ("button", "string", False, "left", "Mouse button: left, right or middle."),
            ("modifiers", "string", False, "", "Comma-separated modifiers such as Alt,Shift."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_dblclick": {
        "description": "CLI command: dblclick Double-click an element reference.",
        "params": [
            ("ref", "string", True, None, "Snapshot element reference."),
            ("button", "string", False, "left", "Mouse button: left, right or middle."),
            ("modifiers", "string", False, "", "Comma-separated modifiers."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_fill": {
        "description": "CLI command: fill Replace the value of an input element.",
        "params": [
            ("ref", "string", True, None, "Snapshot element reference."),
            ("value", "string", True, None, "Value to fill."),
            ("submit", "boolean", False, False, "Press Enter after filling."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_type": {
        "description": "CLI command: type Type text through the keyboard.",
        "params": [
            ("text", "string", True, None, "Text to type."),
            ("submit", "boolean", False, False, "Press Enter afterward."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_hover": {
        "description": "CLI command: hover Hover an element.",
        "params": [
            ("ref", "string", True, None, "Snapshot element reference."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_check": {
        "description": "CLI command: check Check a checkbox or radio element.",
        "params": [
            ("ref", "string", True, None, "Snapshot element reference."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_uncheck": {
        "description": "CLI command: uncheck Uncheck a checkbox or radio element.",
        "params": [
            ("ref", "string", True, None, "Snapshot element reference."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_scroll_to": {
        "description": "CLI command: scroll-to Scroll an element into view.",
        "params": [
            ("ref", "string", True, None, "Snapshot element reference."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_generate_locator": {
        "description": "CLI command: generate-locator Generate a Playwright locator for an element reference.",
        "params": [
            ("ref", "string", True, None, "Snapshot element reference."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_select": {
        "description": "CLI command: select Select an option from a dropdown.",
        "params": [
            ("ref", "string", True, None, "Snapshot element reference."),
            ("value", "string", True, None, "Option value."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_snapshot": {
        "description": "CLI command: snapshot Capture an accessibility snapshot and refresh element references.",
        "params": [
            ("ref", "string", False, "", "Optional element reference for a subtree."),
            ("filename", "string", False, "", "Optional output file under /patchright."),
            ("depth", "number", False, 0, "Maximum snapshot depth; zero means unlimited."),
            ("interactive", "boolean", False, False, "Return interactive elements only."),
            ("boxes", "boolean", False, False, "Include bounding boxes."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_eval": {
        "description": "CLI command: eval Evaluate JavaScript in the page.",
        "params": [
            ("code", "string", True, None, "JavaScript source."),
            ("ref", "string", False, "", "Optional element reference."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_run_code": {
        "description": "CLI command: run-code Run JavaScript in the page and return its value. To get a value returned from the code, a return statement must be added",
        "params": [
            ("code", "string", True, None, "JavaScript source."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_text": {
        "description": "CLI command: text Get text content from an element reference or selector.",
        "params": [
            ("target", "string", True, None, "Element reference or CSS selector."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_screenshot": {
        "description": "CLI command: screenshot Save a page or element screenshot. MCPShell returns "
        "the resulting path as text.",
        "params": [
            ("ref", "string", False, "", "Optional element reference."),
            ("full_page", "boolean", False, False, "Capture the full scrollable page."),
            ("filename", "string", False, "", "Absolute output PNG path under /patchright/cli/."),
            ("hires", "boolean", False, False, "Capture at device pixel ratio."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_drag": {
        "description": "CLI command: drag Drag one element reference onto another.",
        "params": [
            ("from_ref", "string", True, None, "Source reference."),
            ("to_ref", "string", True, None, "Target reference."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_drop": {
        "description": "CLI command: drop Drop a file or data payload onto an element.",
        "params": [
            ("ref", "string", True, None, "Target element reference."),
            ("path", "string", False, "", "File path under /patchright."),
            ("data", "string", False, "", "Data payload formatted as MIME=value."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_close": {
        "description": "CLI command: close Close the named browser session.",
        "params": [("session", "string", False, "default", "Named Patchright browser session.")],
    },
    "patchright_detach": {
        "description": "CLI command: detach Detach an externally attached browser without closing it.",
        "params": [("session", "string", False, "default", "Named Patchright browser session.")],
    },
    "patchright_go_back": {
        "description": "CLI command: go-back Navigate backward.",
        "params": [("session", "string", False, "default", "Named Patchright browser session.")],
    },
    "patchright_go_forward": {
        "description": "CLI command: go-forward Navigate forward.",
        "params": [("session", "string", False, "default", "Named Patchright browser session.")],
    },
    "patchright_reload": {
        "description": "CLI command: reload Reload the page.",
        "params": [("session", "string", False, "default", "Named Patchright browser session.")],
    },
    "patchright_url": {
        "description": "CLI command: url Return the current page URL.",
        "params": [("session", "string", False, "default", "Named Patchright browser session.")],
    },
    "patchright_title": {
        "description": "CLI command: title Return the current page title.",
        "params": [("session", "string", False, "default", "Named Patchright browser session.")],
    },
    "patchright_tab_list": {
        "description": "CLI command: tab-list List browser tabs.",
        "params": [("session", "string", False, "default", "Named Patchright browser session.")],
    },
    "patchright_dialog_dismiss": {
        "description": "CLI command: dialog-dismiss Dismiss the next browser dialog.",
        "params": [("session", "string", False, "default", "Named Patchright browser session.")],
    },
    "patchright_cookie_clear": {
        "description": "CLI command: cookie-clear Clear all cookies.",
        "params": [("session", "string", False, "default", "Named Patchright browser session.")],
    },
    "patchright_localstorage_list": {
        "description": "CLI command: localstorage-list List localStorage.",
        "params": [("session", "string", False, "default", "Named Patchright browser session.")],
    },
    "patchright_localstorage_clear": {
        "description": "CLI command: localstorage-clear Clear localStorage.",
        "params": [("session", "string", False, "default", "Named Patchright browser session.")],
    },
    "patchright_sessionstorage_list": {
        "description": "CLI command: sessionstorage-list List sessionStorage.",
        "params": [("session", "string", False, "default", "Named Patchright browser session.")],
    },
    "patchright_sessionstorage_clear": {
        "description": "CLI command: sessionstorage-clear Clear sessionStorage.",
        "params": [("session", "string", False, "default", "Named Patchright browser session.")],
    },
    "patchright_route_list": {
        "description": "CLI command: route-list List active request routes.",
        "params": [("session", "string", False, "default", "Named Patchright browser session.")],
    },
    "patchright_tracing_start": {
        "description": "CLI command: tracing-start Start Playwright tracing.",
        "params": [("session", "string", False, "default", "Named Patchright browser session.")],
    },
    "patchright_video_start": {
        "description": "CLI command: video-start Start video recording.",
        "params": [("session", "string", False, "default", "Named Patchright browser session.")],
    },
    "patchright_list_sessions": {
        "description": "CLI command: list List Patchright sessions.",
        "params": [("session", "string", False, "default", "Named Patchright browser session.")],
    },
    "patchright_close_all": {
        "description": "CLI command: close-all Close every Patchright browser session. The session "
        "parameter must name any currently open session and is used only as the "
        "command anchor; all sessions are closed.",
        "params": [("session", "string", False, "default", "Named Patchright browser session.")],
    },
    "patchright_kill_all": {
        "description": "CLI command: kill-all Force-close every Patchright browser session and stop "
        "the Patchright daemon. The session parameter must name any currently open "
        "session and is used only as the command anchor; all sessions are terminated.",
        "params": [("session", "string", False, "default", "Named Patchright browser session.")],
    },
    "patchright_delete_data": {
        "description": "CLI command: delete-data Delete the named session's persistent browser profile.",
        "params": [("session", "string", False, "default", "Named Patchright browser session.")],
    },
    "patchright_press": {
        "description": "CLI command: press Press a keyboard key.",
        "params": [
            ("key", "string", True, None, "Playwright key name or character."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_keydown": {
        "description": "CLI command: keydown Hold a keyboard key down.",
        "params": [
            ("key", "string", True, None, "Playwright key name or character."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_keyup": {
        "description": "CLI command: keyup Release a keyboard key.",
        "params": [
            ("key", "string", True, None, "Playwright key name or character."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_mousemove": {
        "description": "CLI command: mousemove Move the mouse to coordinates.",
        "params": [
            ("x", "number", True, None, "X coordinate."),
            ("y", "number", True, None, "Y coordinate."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_mousewheel": {
        "description": "CLI command: mousewheel Scroll the mouse wheel by deltas.",
        "params": [
            ("x", "number", True, None, "Horizontal delta."),
            ("y", "number", True, None, "Vertical delta."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_scroll": {
        "description": "CLI command: scroll Scroll the page by pixel deltas.",
        "params": [
            ("x", "number", True, None, "Horizontal delta."),
            ("y", "number", True, None, "Vertical delta."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_mousedown": {
        "description": "CLI command: mousedown Press a mouse button.",
        "params": [
            ("button", "string", False, "left", "Mouse button: left, right or middle."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_mouseup": {
        "description": "CLI command: mouseup Release a mouse button.",
        "params": [
            ("button", "string", False, "left", "Mouse button: left, right or middle."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_wait": {
        "description": "CLI command: wait Wait for a duration or until the URL matches a glob.",
        "params": [
            ("milliseconds", "number", False, 0, "Duration in milliseconds; use zero when waiting for a URL."),
            ("url_pattern", "string", False, "", "Optional URL glob such as **/dashboard."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_wait_for": {
        "description": "CLI command: wait-for Wait for an element to reach a state.",
        "params": [
            ("ref", "string", True, None, "Element reference."),
            ("state", "string", False, "visible", "attached, detached, visible or hidden."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_tab_new": {
        "description": "CLI command: tab-new Open a new browser tab.",
        "params": [
            ("url", "string", False, "", "Optional initial URL."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_tab_close": {
        "description": "CLI command: tab-close Close a tab by index; omit index for the current tab.",
        "params": [
            ("index", "number", False, -1, "Zero-based tab index; -1 means current tab where supported."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_tab_select": {
        "description": "CLI command: tab-select Select a browser tab by index.",
        "params": [
            ("index", "number", False, -1, "Zero-based tab index; -1 means current tab where supported."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_dialog_accept": {
        "description": "CLI command: dialog-accept Accept the next dialog, optionally supplying prompt text.",
        "params": [
            ("text", "string", False, "", "Optional prompt response."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_upload": {
        "description": "CLI command: upload Upload a file to a file input.",
        "params": [
            ("path", "string", True, None, "File path under /patchright."),
            ("ref", "string", False, "", "Optional target element reference."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_resize": {
        "description": "CLI command: resize Resize the browser viewport.",
        "params": [
            ("width", "number", True, None, "Viewport width."),
            ("height", "number", True, None, "Viewport height."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_state_save": {
        "description": "CLI command: state-save Save cookies and storage state.",
        "params": [
            ("path", "string", False, "", "Output JSON path under /patchright."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_state_load": {
        "description": "CLI command: state-load Load cookies and storage state.",
        "params": [
            ("path", "string", True, None, "Input JSON path under /patchright."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_cookie_list": {
        "description": "CLI command: cookie-list List cookies, optionally filtering by domain and path.",
        "params": [
            ("domain", "string", False, "", "Cookie domain filter."),
            ("path", "string", False, "", "Cookie path filter."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_cookie_get": {
        "description": "CLI command: cookie-get Get cookies by name.",
        "params": [
            ("name", "string", True, None, "Cookie name."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_cookie_delete": {
        "description": "CLI command: cookie-delete Delete a cookie by name.",
        "params": [
            ("name", "string", True, None, "Cookie name."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_cookie_set": {
        "description": "CLI command: cookie-set Create or replace a cookie.",
        "params": [
            ("name", "string", True, None, "Cookie name."),
            ("value", "string", True, None, "Cookie value."),
            ("domain", "string", False, "", "Cookie domain."),
            ("path", "string", False, "/", "Cookie path."),
            ("expires", "number", False, 0, "Unix expiry timestamp; zero means session cookie."),
            ("http_only", "boolean", False, False, "Mark HttpOnly."),
            ("secure", "boolean", False, False, "Mark Secure."),
            ("same_site", "string", False, "Lax", "SameSite policy: Strict, Lax or None."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_cookie_import": {
        "description": "CLI command: cookie-import Import cookies from a JSON file.",
        "params": [
            ("path", "string", True, None, "Input JSON path under /patchright."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_cookie_export": {
        "description": "CLI command: cookie-export Export cookies to a JSON file.",
        "params": [
            ("path", "string", False, "", "Output JSON path under /patchright."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_localstorage_get": {
        "description": "CLI command: localstorage-get Get a localStorage item.",
        "params": [
            ("key", "string", True, None, "Storage key."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_localstorage_set": {
        "description": "CLI command: localstorage-set Set a localStorage item.",
        "params": [
            ("key", "string", True, None, "Storage key."),
            ("value", "string", True, None, "Storage value."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_localstorage_delete": {
        "description": "CLI command: localstorage-delete Delete a localStorage item.",
        "params": [
            ("key", "string", True, None, "Storage key."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_sessionstorage_get": {
        "description": "CLI command: sessionstorage-get Get a sessionStorage item.",
        "params": [
            ("key", "string", True, None, "Storage key."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_sessionstorage_set": {
        "description": "CLI command: sessionstorage-set Set a sessionStorage item.",
        "params": [
            ("key", "string", True, None, "Storage key."),
            ("value", "string", True, None, "Storage value."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_sessionstorage_delete": {
        "description": "CLI command: sessionstorage-delete Delete a sessionStorage item.",
        "params": [
            ("key", "string", True, None, "Storage key."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_grant_permissions": {
        "description": "CLI command: grant-permissions Grant browser permissions.",
        "params": [
            ("permissions", "string", True, None, "Comma-separated permissions."),
            ("origin", "string", False, "", "Optional origin URL."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_route": {
        "description": "CLI command: route Mock requests matching a URL glob.",
        "params": [
            ("pattern", "string", True, None, "URL glob pattern."),
            ("status", "number", False, 200, "Response status."),
            ("body_b64", "string", False, "", "Optional base64 response body."),
            ("content_type", "string", False, "", "Optional Content-Type."),
            ("header", "string", False, "", "Optional response header as Name:Value."),
            ("remove_header", "string", False, "", "Optional response header name to remove."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_unroute": {
        "description": "CLI command: unroute Remove one matching route, or all routes when no pattern is supplied.",
        "params": [
            ("pattern", "string", False, "", "Optional URL glob."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_network_state_set": {
        "description": "CLI command: network-state-set Set browser networking online or offline.",
        "params": [
            ("state", "string", True, None, "online or offline."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_tracing_stop": {
        "description": "CLI command: tracing-stop Stop tracing and save the trace archive.",
        "params": [("session", "string", False, "default", "Named Patchright browser session.")],
    },
    "patchright_video_stop": {
        "description": "CLI command: video-stop Stop recording and save video or frames.",
        "params": [
            ("filename", "string", False, "", "Output path under /patchright."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_video_chapter": {
        "description": "CLI command: video-chapter Add a chapter marker to the active video.",
        "params": [
            ("title", "string", True, None, "Chapter title."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_pdf": {
        "description": "CLI command: pdf Save the current page as PDF.",
        "params": [
            ("filename", "string", False, "", "Output PDF path under /patchright."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_console": {
        "description": "CLI command: console Return captured browser console messages.",
        "params": [
            ("level", "string", False, "", "Optional level: debug, info, log, warning or error."),
            ("clear", "boolean", False, False, "Clear messages after reading."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_network": {
        "description": "CLI command: network Return captured network requests.",
        "params": [
            ("include_static", "boolean", False, False, "Include static resources."),
            ("clear", "boolean", False, False, "Clear entries after reading."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_requests": {
        "description": "CLI command: requests Alias for returning captured network requests.",
        "params": [
            ("include_static", "boolean", False, False, "Include static resources."),
            ("clear", "boolean", False, False, "Clear entries after reading."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_request": {
        "description": "CLI command: request Return full details for one captured network request.",
        "params": [
            ("id", "number", True, None, "Request ID."),
            ("include_body", "boolean", False, False, "Include response body."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_highlight": {
        "description": "CLI command: highlight Show, style or hide a page highlight overlay.",
        "params": [
            ("ref", "string", True, None, "Element reference."),
            ("style", "string", False, "", "Optional CSS style."),
            ("hide", "boolean", False, False, "Hide the highlight."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_show": {
        "description": "CLI command: show Start or report the Patchright session dashboard.",
        "params": [
            ("port", "number", False, 9322, "Dashboard port."),
            ("annotate", "boolean", False, False, "Enable human page annotation."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_codegen": {
        "description": "CLI command: codegen Start recording interactions for script generation.",
        "params": [("session", "string", False, "default", "Named Patchright browser session.")],
    },
    "patchright_codegen_stop": {
        "description": "CLI command: codegen-stop Stop code generation and save the script.",
        "params": [
            ("filename", "string", False, "", "Optional output script path under /patchright."),
            ("session", "string", False, "default", "Named Patchright browser session."),
        ],
    },
    "patchright_install_skills": {
        "description": "CLI command: install Install Patchright CLI skill files into detected agent directories.",
        "params": [],
    },
}

LONG_TIMEOUT_TOOLS = {
    "patchright_open",
    "patchright_attach",
    "patchright_goto",
    "patchright_wait_for",
    "patchright_screenshot",
    "patchright_pdf",
}


def _validate_session(session: str) -> None:
    if not SAFE_NAME.fullmatch(session):
        raise ValueError("Invalid session name")


def _validate_cli_path(path: str, *, required: bool = False) -> None:
    if not path:
        if required:
            raise ValueError("Path is required")
        return
    p = Path(path)
    if not p.is_absolute():
        raise ValueError("Path must be absolute")
    try:
        resolved = p.resolve(strict=False)
    except OSError as exc:
        raise ValueError(f"Invalid path: {exc}") from exc
    if resolved == MCP_ROOT or MCP_ROOT not in resolved.parents:
        raise ValueError(f"Path must be beneath the MCP server working directory: {MCP_ROOT}")


def _decode_b64(value: str, name: str) -> str:
    if not value:
        return ""
    try:
        return base64.b64decode("".join(value.split()), validate=True).decode()
    except Exception as exc:
        raise ValueError(f"Invalid base64 in {name}: {exc}") from exc


async def _run_patchright(
    args: list[str],
    *,
    session: str | None = "default",
    timeout: float = 180,
) -> str:
    if session is not None:
        _validate_session(session)

    RUNTIME.mkdir(parents=True, exist_ok=True)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    command = list(CLI)
    if session is not None:
        command.extend([f"-s={session}", f"--port={DAEMON_PORT}"])
    command.extend(args)

    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=RUNTIME,
        env=os.environ.copy(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        message = f"Patchright timed out after {timeout:g}s: {' '.join(command)}"
        print(message, file=sys.stderr, flush=True)
        raise RuntimeError(message) from None

    out = stdout.decode(errors="replace")
    err = stderr.decode(errors="replace")

    if process.returncode != 0:
        message = (
            f"Patchright failed with exit status {process.returncode}\n"
            f"command: {' '.join(command)}\n"
            f"stdout:\n{out}\n"
            f"stderr:\n{err}"
        )
        # This goes to the MCP server/container logs while the raised exception is
        # also returned to the MCP caller.
        print(message, file=sys.stderr, flush=True)
        raise RuntimeError(message)

    if err.strip():
        print(err, file=sys.stderr, end="" if err.endswith("\n") else "\n", flush=True)

    return out


def _build_args(name: str, p: dict[str, Any]) -> list[str]:
    # Session-management / startup
    if name == "patchright_open":
        args: list[str] = []
        if p["headless"]:
            args.append("--headless")
        if p["persistent"]:
            args.append("--persistent")
        if p["profile"]:
            _validate_cli_path(p["profile"])
            args.append(f"--profile={p['profile']}")
        for key, flag in [
            ("proxy", "proxy"),
            ("device", "device"),
            ("viewport", "viewport-size"),
            ("locale", "locale"),
            ("timezone", "timezone"),
            ("geolocation", "geolocation"),
            ("user_agent", "user-agent"),
            ("permissions", "grant-permissions"),
        ]:
            if p[key]:
                args.append(f"--{flag}={p[key]}")
        args.append("open")
        if p["url"]:
            args.append(p["url"])
        return args

    if name == "patchright_attach":
        if p["context"] not in {"new", "host"}:
            raise ValueError("context must be 'new' or 'host'")
        args = ["attach"]
        if p["headless"]:
            args.append("--headless")
        args += [f"--cdp={p['cdp_url']}", f"--cdp-timeout={p['cdp_timeout']}", f"--context={p['context']}"]
        return args

    # Straightforward commands
    simple = {
        "patchright_goto": ("goto", ["url"]),
        "patchright_hover": ("hover", ["ref"]),
        "patchright_check": ("check", ["ref"]),
        "patchright_uncheck": ("uncheck", ["ref"]),
        "patchright_scroll_to": ("scroll-to", ["ref"]),
        "patchright_generate_locator": ("generate-locator", ["ref"]),
        "patchright_select": ("select", ["ref", "value"]),
        "patchright_text": ("text", ["target"]),
        "patchright_drag": ("drag", ["from_ref", "to_ref"]),
        "patchright_close": ("close", []),
        "patchright_detach": ("detach", []),
        "patchright_go_back": ("go-back", []),
        "patchright_go_forward": ("go-forward", []),
        "patchright_reload": ("reload", []),
        "patchright_url": ("url", []),
        "patchright_title": ("title", []),
        "patchright_tab_list": ("tab-list", []),
        "patchright_dialog_dismiss": ("dialog-dismiss", []),
        "patchright_cookie_clear": ("cookie-clear", []),
        "patchright_localstorage_list": ("localstorage-list", []),
        "patchright_localstorage_clear": ("localstorage-clear", []),
        "patchright_sessionstorage_list": ("sessionstorage-list", []),
        "patchright_sessionstorage_clear": ("sessionstorage-clear", []),
        "patchright_route_list": ("route-list", []),
        "patchright_tracing_start": ("tracing-start", []),
        "patchright_video_start": ("video-start", []),
        "patchright_list_sessions": ("list", []),
        "patchright_close_all": ("close-all", []),
        "patchright_kill_all": ("kill-all", []),
        "patchright_delete_data": ("delete-data", []),
        "patchright_press": ("press", ["key"]),
        "patchright_keydown": ("keydown", ["key"]),
        "patchright_keyup": ("keyup", ["key"]),
        "patchright_mousedown": ("mousedown", ["button"]),
        "patchright_mouseup": ("mouseup", ["button"]),
        "patchright_wait_for": ("wait-for", ["ref"]),
        "patchright_state_load": ("state-load", ["path"]),
        "patchright_cookie_get": ("cookie-get", ["name"]),
        "patchright_cookie_delete": ("cookie-delete", ["name"]),
        "patchright_cookie_import": ("cookie-import", ["path"]),
        "patchright_localstorage_get": ("localstorage-get", ["key"]),
        "patchright_localstorage_set": ("localstorage-set", ["key", "value"]),
        "patchright_localstorage_delete": ("localstorage-delete", ["key"]),
        "patchright_sessionstorage_get": ("sessionstorage-get", ["key"]),
        "patchright_sessionstorage_set": ("sessionstorage-set", ["key", "value"]),
        "patchright_sessionstorage_delete": ("sessionstorage-delete", ["key"]),
        "patchright_network_state_set": ("network-state-set", ["state"]),
        "patchright_tracing_stop": ("tracing-stop", []),
        "patchright_video_chapter": ("video-chapter", ["title"]),
        "patchright_codegen": ("codegen", []),
    }
    if name in simple:
        cmd, keys = simple[name]
        if name in {"patchright_state_load", "patchright_cookie_import"}:
            _validate_cli_path(p["path"], required=True)
        args = [cmd, *[str(p[k]) for k in keys]]
        if name == "patchright_wait_for":
            if p["state"] not in {"attached", "detached", "visible", "hidden"}:
                raise ValueError("state must be attached, detached, visible or hidden")
            args.append(f"--state={p['state']}")
        return args

    if name in {"patchright_click", "patchright_dblclick"}:
        if p["button"] not in {"left", "right", "middle"}:
            raise ValueError("button must be left, right or middle")
        args = [
            "click" if name.endswith("click") and not name.endswith("dblclick") else "dblclick",
            p["ref"],
            p["button"],
        ]
        if p["modifiers"]:
            args.append(f"--modifiers={p['modifiers']}")
        return args

    if name == "patchright_fill":
        args = ["fill", p["ref"], p["value"]]
        if p["submit"]:
            args.append("--submit")
        return args

    if name == "patchright_type":
        args = ["type", p["text"]]
        if p["submit"]:
            args.append("--submit")
        return args

    if name == "patchright_eval":
        args = ["eval", p["code"]]
        if p["ref"]:
            args.append(p["ref"])
        return args

    if name == "patchright_run_code":
        return ["run-code", p["code"]]

    if name == "patchright_screenshot":
        if p["filename"]:
            _validate_cli_path(p["filename"])
        args = ["screenshot"]
        if p["ref"]:
            args.append(p["ref"])
        if p["full_page"]:
            args.append("--full-page")
        if p["filename"]:
            args.append(f"--filename={p['filename']}")
        if p["hires"]:
            args.append("--hires")
        return args

    if name == "patchright_drop":
        if not p["path"] and not p["data"]:
            raise ValueError("Either path or data is required")
        args = ["drop", p["ref"]]
        if p["path"]:
            _validate_cli_path(p["path"])
            args.append(f"--path={p['path']}")
        if p["data"]:
            args.append(f"--data={p['data']}")
        return args

    if name in {"patchright_mousemove", "patchright_mousewheel", "patchright_scroll"}:
        return [name.removeprefix("patchright_").replace("_", "-"), str(p["x"]), str(p["y"])]

    if name == "patchright_wait":
        args = ["wait"]
        if p["url_pattern"]:
            args.append(f"--url={p['url_pattern']}")
        else:
            args.append(str(p["milliseconds"]))
        return args

    if name == "patchright_tab_new":
        args = ["tab-new"]
        if p["url"]:
            args.append(p["url"])
        return args

    if name in {"patchright_tab_close", "patchright_tab_select"}:
        args = ["tab-close" if name.endswith("close") else "tab-select"]
        if p["index"] >= 0:
            args.append(str(p["index"]))
        return args

    if name == "patchright_dialog_accept":
        args = ["dialog-accept"]
        if p["text"]:
            args.append(p["text"])
        return args

    if name == "patchright_upload":
        _validate_cli_path(p["path"], required=True)
        args = ["upload", p["path"]]
        if p["ref"]:
            args.append(p["ref"])
        return args

    if name == "patchright_resize":
        return ["resize", str(p["width"]), str(p["height"])]

    if name == "patchright_state_save":
        args = ["state-save"]
        if p["path"]:
            _validate_cli_path(p["path"])
            args.append(p["path"])
        return args

    if name == "patchright_cookie_list":
        args = ["cookie-list"]
        if p["domain"]:
            args.append(f"--domain={p['domain']}")
        if p["path"]:
            args.append(f"--path={p['path']}")
        return args

    if name == "patchright_cookie_set":
        if p["same_site"] not in {"Strict", "Lax", "None"}:
            raise ValueError("same_site must be Strict, Lax or None")
        args = ["cookie-set", p["name"], p["value"]]
        if p["domain"]:
            args.append(f"--domain={p['domain']}")
        if p["path"]:
            args.append(f"--path={p['path']}")
        if p["expires"] != 0:
            args.append(f"--expires={p['expires']}")
        if p["http_only"]:
            args.append("--httpOnly")
        if p["secure"]:
            args.append("--secure")
        args.append(f"--sameSite={p['same_site']}")
        return args

    if name == "patchright_cookie_export":
        args = ["cookie-export"]
        if p["path"]:
            _validate_cli_path(p["path"])
            args.append(p["path"])
        return args

    if name == "patchright_grant_permissions":
        args = ["grant-permissions", p["permissions"]]
        if p["origin"]:
            args.append(f"--origin={p['origin']}")
        return args

    if name == "patchright_route":
        if not 100 <= p["status"] <= 599:
            raise ValueError("status must be between 100 and 599")
        args = ["route", p["pattern"], f"--status={p['status']}"]
        if p["body_b64"]:
            args.append(f"--body={_decode_b64(p['body_b64'], 'body_b64')}")
        if p["content_type"]:
            args.append(f"--content-type={p['content_type']}")
        if p["header"]:
            args.append(f"--header={p['header']}")
        if p["remove_header"]:
            args.append(f"--remove-header={p['remove_header']}")
        return args

    if name == "patchright_unroute":
        args = ["unroute"]
        if p["pattern"]:
            args.append(p["pattern"])
        return args

    if name == "patchright_video_stop":
        if p["filename"]:
            _validate_cli_path(p["filename"])
        args = ["video-stop"]
        if p["filename"]:
            args.append(f"--filename={p['filename']}")
        return args

    if name == "patchright_pdf":
        if p["filename"]:
            _validate_cli_path(p["filename"])
        args = ["pdf"]
        if p["filename"]:
            args.append(f"--filename={p['filename']}")
        return args

    if name == "patchright_console":
        args = ["console"]
        if p["level"]:
            args.append(p["level"])
        if p["clear"]:
            args.append("--clear")
        return args

    if name in {"patchright_network", "patchright_requests"}:
        args = ["network" if name.endswith("network") else "requests"]
        if p["include_static"]:
            args.append("--static")
        if p["clear"]:
            args.append("--clear")
        return args

    if name == "patchright_request":
        args = ["request", str(p["id"])]
        if p["include_body"]:
            args.append("--body")
        return args

    if name == "patchright_highlight":
        args = ["highlight", p["ref"]]
        if p["style"]:
            args.append(f"--style={p['style']}")
        if p["hide"]:
            args.append("--hide")
        return args

    if name == "patchright_show":
        args = ["show", f"--show-port={p['port']}"]
        if p["annotate"]:
            args.append("--annotate")
        return args

    if name == "patchright_codegen_stop":
        args = ["codegen-stop"]
        if p["filename"]:
            _validate_cli_path(p["filename"])
            args.append(p["filename"])
        return args

    if name == "patchright_install_skills":
        return ["install", "--skills"]

    raise RuntimeError(f"No argument builder implemented for {name}")


async def _run_registered_tool(name: str, params: dict[str, Any]) -> str:
    session = params.get("session")
    args = _build_args(name, params)
    timeout = 300 if name in LONG_TIMEOUT_TOOLS else 180
    if name == "patchright_install_skills":
        return await _run_patchright(args, session=None, timeout=timeout)
    return await _run_patchright(args, session=session, timeout=timeout)


async def _snapshot_tool(params: dict[str, Any]) -> str:
    session = params["session"]
    filename = params["filename"]
    if filename:
        _validate_cli_path(filename)
        args = _build_args("patchright_snapshot", params)
        return await _run_patchright(args, session=session)

    with tempfile.NamedTemporaryFile(prefix="patchright-snapshot-", suffix=".yml", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        args = ["snapshot"]
        if params["ref"]:
            args.append(params["ref"])
        args.append(f"--filename={tmp_path}")
        if params["depth"] != 0:
            args.append(f"--depth={params['depth']}")
        if params["interactive"]:
            args.append("--interactive")
        if params["boxes"]:
            args.append("--boxes")
        await _run_patchright(args, session=session)
        return tmp_path.read_text(errors="replace")
    finally:
        tmp_path.unlink(missing_ok=True)


def _python_type(type_name: str) -> type:
    return {"string": str, "boolean": bool, "number": int}[type_name]


def _parameter_type(tool_name: str, param_name: str, type_name: str) -> Any:
    if tool_name == "patchright_attach" and param_name == "context":
        return Literal["new", "host"]

    if param_name == "button":
        return Literal["left", "right", "middle"]

    if tool_name == "patchright_wait_for" and param_name == "state":
        return Literal["attached", "detached", "visible", "hidden"]

    if tool_name == "patchright_cookie_set" and param_name == "same_site":
        return Literal["Strict", "Lax", "None"]

    if tool_name == "patchright_network_state_set" and param_name == "state":
        return Literal["online", "offline"]

    return _python_type(type_name)


def _register_generated_tool(name: str, spec: dict[str, Any]) -> None:
    params_spec = spec["params"]

    async def generated(**kwargs: Any) -> str:
        if name == "patchright_snapshot":
            return await _snapshot_tool(kwargs)
        return await _run_registered_tool(name, kwargs)

    parameters = []
    annotations: dict[str, Any] = {"return": str}
    for param_name, type_name, required, default, description in params_spec:
        base_type = _parameter_type(name, param_name, type_name)
        annotation = Annotated[base_type, Field(description=description)]

        annotations[param_name] = annotation
        parameters.append(
            inspect.Parameter(
                param_name,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=annotation,
                default=inspect.Parameter.empty if required else default,
            )
        )

    generated.__name__ = name
    generated.__qualname__ = name
    generated.__doc__ = spec["description"]
    generated.__annotations__ = annotations
    generated.__signature__ = inspect.Signature(parameters, return_annotation=str)
    server.tool(name=name, description=spec["description"])(generated)


for _tool_name, _tool_spec in TOOL_SPECS.items():
    _register_generated_tool(_tool_name, _tool_spec)


@server.tool(description="Capture the current Patchright page and return a displayable image.")
async def browser_screenshot(
    session: Annotated[str, Field(description="Named Patchright browser session.")] = "default",
    full_page: Annotated[
        bool, Field(description="Capture the full scrollable page instead of only the current viewport.")
    ] = False,
) -> ToolResult:
    _validate_session(session)

    with tempfile.TemporaryDirectory() as directory:
        screenshot = Path(directory) / "screenshot.png"
        args = ["screenshot", f"--filename={screenshot}"]
        if full_page:
            args.append("--full-page")

        await _run_patchright(args, session=session, timeout=300)

        return ToolResult(
            content=[
                ImageContent(
                    type="image",
                    data=base64.b64encode(screenshot.read_bytes()).decode("ascii"),
                    mime_type="image/png",
                )
            ]
        )


@server.tool(description="Delete a file or output directory created by Patchright.")
async def browser_delete_output(
    path: Annotated[
        str,
        Field(description="Absolute file or directory path beneath the MCP server's output directory."),
    ],
) -> str:
    requested = Path(path)
    if not requested.is_absolute():
        raise ValueError("Path must be absolute")
    if requested.is_symlink():
        raise ValueError("Symbolic links are not allowed")

    resolved = requested.resolve(strict=True)
    if resolved == OUTPUT_ROOT or OUTPUT_ROOT not in resolved.parents:
        raise ValueError(f"Path must be beneath {OUTPUT_ROOT}")

    if resolved.is_dir():
        shutil.rmtree(resolved)
    else:
        resolved.unlink()

    return f"Deleted {resolved}"


def run_mcp_server(*, http: bool = False, host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the MCP server over stdio or Streamable HTTP."""
    if http:
        server.run(transport="http", host=host, port=port)
    else:
        server.run(transport="stdio")


if __name__ == "__main__":
    run_mcp_server()
