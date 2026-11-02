"""Debugger commands: holding a paused command, stepping, resuming, teardown."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from patchright_cli import cli, mcp_server
from patchright_cli.daemon import DaemonState, Session, handle_command
from patchright_cli.debugger import SessionDebugger


class FakeDriverDebugger:
    """Mimics the driver: an armed pause stops the next call; while paused, calls run through."""

    def __init__(self):
        self.paused_details = None
        self._pause_next = False
        self._release: asyncio.Future | None = None
        self._listeners = []

    def on(self, event, handler):
        assert event == "pausedstatechanged"
        self._listeners.append(handler)

    def _emit(self):
        for handler in self._listeners:
            handler()

    async def request_pause(self):
        if self.paused_details is not None:
            raise RuntimeError("Debugger is already paused")
        self._pause_next = True

    async def _let_go(self, pause_next: bool):
        if self.paused_details is None:
            raise RuntimeError("Debugger is not paused")
        self._pause_next = pause_next
        self.paused_details = None
        self._release.set_result(None)
        self._emit()

    async def resume(self):
        await self._let_go(False)

    async def next(self):
        await self._let_go(True)

    async def call(self, title: str):
        """One pausable API call."""
        if self._pause_next and self.paused_details is None:
            self._pause_next = False
            self._release = asyncio.get_running_loop().create_future()
            self.paused_details = {"title": title, "location": {"file": "daemon.py", "line": 1, "column": 0}}
            self._emit()
            await self._release
        await asyncio.sleep(0)


def _session(driver: FakeDriverDebugger) -> Session:
    async def close():
        await driver.call("Close context")

    async def evaluate(*_args, **_kwargs):
        await driver.call("Evaluate")

    context = MagicMock()
    context.debugger = driver
    context.close = AsyncMock(side_effect=close)
    session = Session("dbg", context, pages=[MagicMock()])
    page = MagicMock()
    page.is_closed.return_value = False
    page.evaluate = AsyncMock(side_effect=evaluate)
    session.pages = [page]
    session.tabs["default"].page = page
    return session


@pytest.fixture
def driver():
    return FakeDriverDebugger()


@pytest.fixture
def state(driver):
    state = MagicMock(spec=DaemonState)
    state.sessions = {"default": _session(driver)}
    state.profile_dirs = {}
    state.shutdown_event = None
    return state


async def _send(state, command, args=None):
    return await handle_command(state, {"command": command, "args": args or []})


@pytest.mark.asyncio
async def test_status_before_any_pause_is_off(state):
    reply = await _send(state, "debugger-status")
    assert reply["success"] is True
    assert reply["output"] == "Not paused."
    assert reply["data"]["state"] == "off"


@pytest.mark.asyncio
async def test_pause_arms_and_explains_once(state):
    first = await _send(state, "debugger-pause")
    assert first["success"] is True
    assert first["data"]["state"] == "armed"
    assert "pages cannot detect it" in first["output"]
    second = await _send(state, "debugger-pause")
    assert second["success"] is True
    assert "pages cannot detect it" not in second["output"]


@pytest.mark.asyncio
async def test_a_paused_command_is_held_and_resume_returns_its_result(state, driver):
    async def slow_title(*_):
        await driver.call("Click button")
        return {"success": True, "output": "clicked"}

    from patchright_cli import daemon

    daemon.COMMAND_HANDLERS["fake-click"] = slow_title
    try:
        await _send(state, "debugger-pause")
        held = await asyncio.wait_for(_send(state, "fake-click", ["e1"]), 2)
        assert held["paused"] is True
        assert held["data"] == {
            "state": "paused",
            "title": "Click button",
            "command": "fake-click e1",
            "location": {"file": "daemon.py", "line": 1, "column": 0},
        }
        assert "held: `fake-click e1`" in held["output"]
        assert "Other commands, including actions, still run immediately" in held["output"]

        # Other commands keep working while the call is paused.
        status = await asyncio.wait_for(_send(state, "debugger-status"), 2)
        assert status["data"]["state"] == "paused"

        resumed = await asyncio.wait_for(_send(state, "debugger-resume"), 2)
        assert resumed["success"] is True
        assert "`fake-click e1` finished:\nclicked" in resumed["output"]
        assert resumed["output"].endswith("Debugger off.")
        assert (await _send(state, "debugger-status"))["data"]["state"] == "off"
    finally:
        daemon.COMMAND_HANDLERS.pop("fake-click")


@pytest.mark.asyncio
async def test_step_stops_again_inside_the_same_command(state, driver):
    async def two_calls(*_):
        await driver.call("Evaluate")
        await driver.call("Click button")
        return {"success": True, "output": "done"}

    from patchright_cli import daemon

    daemon.COMMAND_HANDLERS["fake-two"] = two_calls
    try:
        await _send(state, "debugger-pause")
        held = await asyncio.wait_for(_send(state, "fake-two"), 2)
        assert held["data"]["title"] == "Evaluate"
        stepped = await asyncio.wait_for(_send(state, "debugger-step"), 2)
        assert stepped["paused"] is True
        assert stepped["data"]["title"] == "Click button"
        finished = await asyncio.wait_for(_send(state, "debugger-step"), 2)
        assert "`fake-two` finished:\ndone" in finished["output"]
        # `next` re-arms, so the next call pauses again.
        assert finished["output"].endswith(
            "Still armed: the next page action a command makes pauses again; `debugger-resume` turns the debugger off."
        )
        assert (await _send(state, "debugger-status"))["data"]["state"] == "armed"
    finally:
        daemon.COMMAND_HANDLERS.pop("fake-two")


@pytest.mark.asyncio
async def test_a_failing_held_command_reports_its_error(state, driver):
    async def boom(*_):
        await driver.call("Click button")
        raise RuntimeError("element detached")

    from patchright_cli import daemon

    daemon.COMMAND_HANDLERS["fake-boom"] = boom
    try:
        await _send(state, "debugger-pause")
        await asyncio.wait_for(_send(state, "fake-boom"), 2)
        resumed = await asyncio.wait_for(_send(state, "debugger-resume"), 2)
        # handle_command turns handler exceptions into a failed reply.
        assert resumed["success"] is False
        assert "element detached" in resumed["output"]
    finally:
        daemon.COMMAND_HANDLERS.pop("fake-boom")


@pytest.mark.asyncio
async def test_step_without_a_pause_fails_clearly(state):
    reply = await _send(state, "debugger-step")
    assert reply["success"] is False
    assert "run `debugger-pause`, then a command" in reply["output"]


@pytest.mark.asyncio
async def test_step_while_armed_says_nothing_is_paused_yet(state):
    await _send(state, "debugger-pause")
    reply = await _send(state, "debugger-step")
    assert reply["success"] is False
    assert reply["output"] == ("Armed, nothing paused yet: run a command to pause it, or `debugger-resume` to disarm.")


@pytest.mark.asyncio
async def test_a_command_without_a_pausable_call_runs_through_and_says_so(state):
    # Like `snapshot`: aria_snapshot is not a pausable call in the driver.
    async def no_pausable_call(*_):
        return {"success": True, "output": "- button [ref=e1]"}

    from patchright_cli import daemon

    daemon.COMMAND_HANDLERS["fake-snapshot"] = no_pausable_call
    try:
        armed = await _send(state, "debugger-pause")
        assert armed["output"].startswith(
            "Armed: the next page action, navigation or evaluate (click, fill, goto, eval, ...) pauses."
        )
        reply = await asyncio.wait_for(_send(state, "fake-snapshot"), 2)
        assert reply["success"] is True
        assert "paused" not in reply
        assert reply["output"] == (
            "- button [ref=e1]\nDebugger: `fake-snapshot` ran without pausing (it makes no page action); still armed."
        )
        assert (await _send(state, "debugger-status"))["data"]["state"] == "armed"
    finally:
        daemon.COMMAND_HANDLERS.pop("fake-snapshot")


@pytest.mark.asyncio
async def test_pause_while_paused_fails(state, driver):
    session = state.sessions["default"]
    debugger, _ = session.get_debugger()
    await debugger.request_pause()
    task = asyncio.ensure_future(driver.call("Click"))
    await asyncio.sleep(0)
    reply = await _send(state, "debugger-pause")
    assert reply["success"] is False
    assert "Already paused" in reply["output"]
    await _send(state, "debugger-resume")
    await task


@pytest.mark.asyncio
async def test_resume_cancels_an_unhit_pause(state, driver):
    await _send(state, "debugger-pause")
    reply = await asyncio.wait_for(_send(state, "debugger-resume"), 2)
    assert reply["output"] == "Pause request cancelled. Debugger off."
    assert reply["data"]["state"] == "off"
    assert driver._pause_next is False
    assert (await _send(state, "debugger-resume"))["output"] == "Not paused (no-op)."


@pytest.mark.asyncio
async def test_close_while_armed_does_not_hang(state, driver):
    await _send(state, "debugger-pause")
    session = state.sessions["default"]
    await asyncio.wait_for(session.end(), 2)
    session.context.close.assert_awaited_once()
    assert driver.paused_details is None


@pytest.mark.asyncio
async def test_close_while_paused_does_not_hang(state, driver):
    async def click(*_):
        await driver.call("Click button")
        return {"success": True, "output": "clicked"}

    from patchright_cli import daemon

    daemon.COMMAND_HANDLERS["fake-click"] = click
    try:
        await _send(state, "debugger-pause")
        await asyncio.wait_for(_send(state, "fake-click"), 2)
        session = state.sessions["default"]
        held = session.debugger.held_task
        await asyncio.wait_for(session.end(), 2)
        assert driver.paused_details is None
        await asyncio.wait_for(held, 2)
    finally:
        daemon.COMMAND_HANDLERS.pop("fake-click")


@pytest.mark.asyncio
async def test_commands_without_the_debugger_are_not_wrapped(state):
    assert state.sessions["default"].debugger is None
    reply = await _send(state, "debugger-status")
    assert isinstance(state.sessions["default"].debugger, SessionDebugger)
    assert reply["success"] is True


def test_debugger_commands_are_specced_and_categorised():
    names = ["debugger-pause", "debugger-step", "debugger-resume", "debugger-status"]
    for name in names:
        assert name in cli.COMMAND_SPECS
        assert f"patchright_{name.replace('-', '_')}" in mcp_server.TOOL_SPECS
        assert mcp_server._build_args(f"patchright_{name.replace('-', '_')}", {"session": "default"}) == [name]
    categorised = dict(cli.COMMAND_CATEGORIES)
    assert categorised["Debugger"] == names
