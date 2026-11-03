"""Pause, step and resume the daemon's own browser calls (BrowserContext.debugger).

Playwright's debugger is not a JavaScript debugger. It lives in the driver and
holds an *API call* (click, fill, goto, an evaluate, ...) before the driver runs
it; the page's scripts keep running and no CDP Debugger domain is touched, so
pages cannot see it. `request_pause` arms it, the next pausable call stops,
`next` lets that call go and stops at the one after, `resume` lets everything go.

For a CLI the paused call is one a command made, so that command would block
until someone resumed it -- and an agent issues commands one at a time. The
command is therefore *held*: its reply says where it paused, it keeps waiting in
the background, and `debugger-step` / `debugger-resume` return its real result.

Two driver rules shape the rest:
- While a call is paused every other call runs unpaused, so other commands keep
  working (that is how you look at the page mid-pause).
- An armed pause cannot be cancelled -- it waits for the next pausable call, and
  closing a page or the context is one. Teardown therefore runs `unattended`,
  resuming any pause it meets.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

logger = logging.getLogger("patchright-cli.debugger")

STATE_OFF = "off"
STATE_ARMED = "armed"
STATE_PAUSED = "paused"

# What the driver pauses on. Calls marked pausable in its protocol metainfo:
# actions, navigation, evaluate and element getters. Snapshots (ariaSnapshot),
# page title, cookies, storage state, new pages and highlight are not.
PAUSES_ON = "the next page action, navigation or evaluate (click, fill, goto, eval, ...)"
ARMED = f"Armed: {PAUSES_ON} pauses."
STILL_ARMED = (
    "Still armed: the next page action a command makes pauses again; `debugger-resume` turns the debugger off."
)


class SessionDebugger:
    """One session's view of `context.debugger`, created by the first debugger-* command."""

    def __init__(self, debugger):
        self._debugger = debugger
        # A pause is requested (debugger-pause, or debugger-step) and no call has hit it yet.
        self.armed = False
        # The command whose call is paused, and the task still running it.
        self.held_command: str | None = None
        self.held_task: asyncio.Future | None = None
        self._pause_hit = asyncio.Event()
        # Held by the one command (or step) waiting to see whether the armed
        # pause stops it; other commands wait their turn instead of all
        # claiming the same pause.
        self._pause_owner = asyncio.Lock()
        # Whether `debugger-pause` has explained what pausing does.
        self.announced = False
        debugger.on("pausedstatechanged", self._on_paused_state_changed)

    # -- state ---------------------------------------------------------------

    @property
    def details(self) -> dict | None:
        return self._debugger.paused_details

    @property
    def paused(self) -> bool:
        return self._debugger.paused_details is not None

    @property
    def state(self) -> str:
        if self.paused:
            return STATE_PAUSED
        return STATE_ARMED if self.armed else STATE_OFF

    def _on_paused_state_changed(self, *_):
        if self.paused:
            # The driver clears its pause request when it pauses.
            self.armed = False
            self._pause_hit.set()

    # -- running commands ----------------------------------------------------

    async def run(self, command: str, coro) -> dict:
        """Run a command's coroutine; hold it if it hits the armed pause.

        Only an armed debugger can pause a call. While something is already
        paused the driver lets every other call through, so the command runs
        as usual.
        """
        if not self.armed or self.paused:
            return await coro
        async with self._pause_owner:
            if self.armed and not self.paused:
                task = asyncio.ensure_future(coro)
                result = await self._until_done_or_paused(command, task)
                if self.armed and not result.get("paused") and isinstance(result.get("output"), str):
                    # Snapshots, titles, cookies and the like make no pausable call.
                    result = dict(result)
                    result["output"] += (
                        f"\nDebugger: `{command}` ran without pausing (it makes no page action); still armed."
                    )
                return result
        # The command ahead of this one paused, or the debugger went off, while this one waited.
        return await coro

    async def _until_done_or_paused(self, command: str, task: asyncio.Future) -> dict:
        self._pause_hit.clear()
        if self.paused:
            self._pause_hit.set()
        waiter = asyncio.ensure_future(self._pause_hit.wait())
        try:
            await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            waiter.cancel()
        if task.done():
            self._clear_held(task)
            return task.result()
        self.held_command, self.held_task = command, task
        task.add_done_callback(_log_orphan_failure)
        return {
            "success": True,
            "paused": True,
            "output": f"{self.describe()}\n{_NEXT_STEPS}",
            "data": self.status_data(),
        }

    def _clear_held(self, task: asyncio.Future | None = None) -> None:
        if task is None or task is self.held_task:
            self.held_command, self.held_task = None, None

    async def request_pause(self) -> None:
        await self._debugger.request_pause()
        self.armed = True

    async def step(self) -> dict:
        """Let the paused call go and pause before the next one."""
        command, task = self.held_command, self.held_task
        async with self._pause_owner:
            await self._debugger.next()
            self.armed = True
            if task is None or task.done():
                self._clear_held(task)
                return _released(command, task, still_armed=self.armed)
            # The held command may finish, or stop again at one of its own later calls.
            result = await self._until_done_or_paused(command, task)
        if result.get("paused"):
            return result
        return _released(command, task, still_armed=self.armed)

    async def resume(self) -> dict:
        command, task = self.held_command, self.held_task
        await self._debugger.resume()
        self.armed = False
        if task is not None:
            with contextlib.suppress(Exception):
                await asyncio.shield(task)
        self._clear_held(task)
        return _released(command, task, still_armed=False)

    async def disarm(self, probe) -> None:
        """Cancel an armed pause that nothing has hit yet.

        The driver offers no call for it, so `probe` -- a harmless pausable call --
        walks into the pause and is resumed straight away.
        """
        await self.unattended(probe)

    async def unattended(self, coro):
        """Await `coro`, resuming every pause it meets. For teardown, which must not hang."""
        task = asyncio.ensure_future(coro)
        while True:
            if self.paused:
                with contextlib.suppress(Exception):
                    await self._debugger.resume()
            if not self.armed and not self.paused:
                return await task
            self._pause_hit.clear()
            waiter = asyncio.ensure_future(self._pause_hit.wait())
            try:
                await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                waiter.cancel()
            if task.done():
                if self.paused:
                    with contextlib.suppress(Exception):
                        await self._debugger.resume()
                return task.result()

    # -- reporting -----------------------------------------------------------

    def describe(self) -> str:
        details = self.details
        if details is not None:
            line = f"Paused before: {details.get('title') or 'an API call'}"
            if self.held_command:
                line += f" (held: `{self.held_command}`)"
            return line
        if self.armed:
            return ARMED
        return "Not paused."

    def status_data(self) -> dict:
        details = self.details or {}
        return {
            "state": self.state,
            "title": details.get("title"),
            "command": self.held_command,
            "location": details.get("location"),
        }


_NEXT_STEPS = (
    "The command is held and finishes in the background: `debugger-step` runs to the next call, "
    "`debugger-resume` lets it finish; both print its result. Other commands, including actions, "
    "still run immediately meanwhile, so their side effects can land before the held one's."
)


def _released(command: str | None, task: asyncio.Future | None, *, still_armed: bool) -> dict:
    tail = STILL_ARMED if still_armed else "Debugger off."
    if task is None:
        return {"success": True, "output": tail}
    if task.cancelled():
        return {"success": False, "output": f"`{command}` was cancelled.\n{tail}"}
    exc = task.exception()
    if exc is not None:
        return {"success": False, "output": f"`{command}` failed: {exc}\n{tail}"}
    result = dict(task.result())
    result["output"] = f"`{command}` finished:\n{result.get('output', '')}\n{tail}"
    return result


def _log_orphan_failure(task: asyncio.Future) -> None:
    # A held command whose session closed fails with nobody awaiting it.
    if not task.cancelled() and task.exception() is not None:
        logger.debug("Held command ended with: %s", task.exception())
