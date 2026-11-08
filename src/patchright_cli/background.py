import asyncio
import secrets
from pathlib import Path

import aiohttp


async def _create_background_target(endpoint):
    async with (
        aiohttp.ClientSession() as http,
        http.ws_connect(endpoint) as ws,
    ):
        await ws.send_json(
            {
                "id": 1,
                "method": "Target.createTarget",
                "params": {"url": "about:blank", "background": True, "newWindow": True},
            }
        )
        async for message in ws:
            reply = message.json()
            if reply.get("id") == 1:
                if "error" in reply:
                    raise RuntimeError(f"Chrome could not open a background window: {reply['error']}")
                return
    raise RuntimeError("Chrome closed the background window connection without a response")


async def launch_background(chromium, profile: str, options: dict):
    port_file = Path(profile) / "DevToolsActivePort"
    before = port_file.stat().st_mtime_ns if port_file.exists() else None
    timeout = min(options.get("timeout") or 30_000, 30_000)
    options = {
        **options,
        "timeout": timeout,
        "args": [*options["args"], "--no-startup-window", "--remote-debugging-port=0"],
        "ignore_default_args": ["about:blank"],
    }
    launch = asyncio.create_task(chromium.launch_persistent_context(profile, **options))

    def fresh_endpoint():
        try:
            if port_file.stat().st_mtime_ns != before:
                lines = port_file.read_text().splitlines()
                if len(lines) >= 2 and 0 < int(lines[0]) < 65536 and lines[1].startswith("/devtools/browser/"):
                    return f"http://127.0.0.1:{int(lines[0])}{lines[1]}"
        except (OSError, UnicodeError, ValueError):
            pass
        return None

    try:
        async with asyncio.timeout(timeout / 1000):
            while True:
                if launch.done():
                    return await asyncio.shield(launch)
                endpoint = fresh_endpoint()
                if endpoint:
                    break
                await asyncio.sleep(0.05)
            await _create_background_target(endpoint)
            return await asyncio.shield(launch)
    except BaseException as original:
        failure = original

        async def close_late_context():
            while not launch.done():
                endpoint = fresh_endpoint()
                if endpoint:
                    try:
                        async with asyncio.timeout(5):
                            await _create_background_target(endpoint)
                    except Exception as error:
                        failure.add_note(f"Background bootstrap cleanup: {error}")
                    break
                await asyncio.sleep(0.05)
            try:
                context = await asyncio.shield(launch)
            except Exception as error:
                failure.add_note(f"Background launch ended during cleanup: {error}")
            else:
                await context.close()

        cleanup = asyncio.create_task(close_late_context())
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                continue
            except Exception as error:
                original.add_note(f"Background context cleanup: {error}")
        raise


async def new_background_page(context):
    correlation_url = f"about:blank#patchright-{secrets.token_hex(12)}"
    browser = context.browser
    if browser is None:
        raise RuntimeError("Background pages require Patchright >=1.64.4 with a persistent-context Browser")
    cdp = await browser.new_browser_cdp_session()
    try:
        async with context.expect_page(predicate=lambda page: page.url == correlation_url) as opened:
            await cdp.send(
                "Target.createTarget", {"url": correlation_url, "background": True, "newWindow": not context.pages}
            )
        return await opened.value
    finally:
        await cdp.detach()
