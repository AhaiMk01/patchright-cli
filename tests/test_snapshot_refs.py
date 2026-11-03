"""Ref resolution on Playwright's native refs: format check, tab guard, heal-once, stale errors."""

from functools import partial
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from patchright_cli import daemon
from patchright_cli.daemon import DaemonState, Session, Tab, handle_command

RAW = '- generic [active] [ref=e1]:\n  - button "Go" [ref=e2]\n  - combobox "Color" [ref=e3]:\n    - option "Red"\n'


@pytest.fixture
def state():
    state = MagicMock(spec=DaemonState)
    state.sessions = {}
    state.profile_dirs = {}
    state.shutdown_event = None
    return state


@pytest.fixture
def session(state):
    session = MagicMock(spec=Session)
    session.name = "default"
    session.pages = []
    session.current_tab = 0
    session.context = MagicMock()
    session.page = MagicMock()
    session.page.url = "https://example.com/"
    session.page.title = AsyncMock(return_value="Example")
    session._codegen = None
    session.highlights = {}
    session._video_recording = False
    session._video_show_actions = None
    session.tabs = {"default": MagicMock()}
    session.activate_tab = MagicMock()
    session.uses_host_context = False
    session.network_guard = None
    session.goto = partial(Session.goto, session)
    session.check_navigation = partial(Session.check_navigation, session)
    session.last_raw = RAW
    state.sessions = {"default": session}
    return session


def _locator(*counts):
    loc = MagicMock()
    loc.count = AsyncMock(side_effect=list(counts))
    for method in ("click", "evaluate", "wait_for", "select_option"):
        setattr(loc, method, AsyncMock(return_value=None))
    return loc


async def _run(daemon_state, command, args, **options):
    with (
        patch("patchright_cli.daemon.ai_snapshot", AsyncMock(return_value=RAW)) as snap,
        patch("patchright_cli.daemon.save_snapshot", return_value="/tmp/snap.yml"),
    ):
        response = await handle_command(daemon_state, {"command": command, "args": args, "options": options})
    return response, snap


@pytest.mark.parametrize("spelling", ["e2", "@e2", "ref=e2", "[ref=e2]"])
async def test_ref_spellings_resolve_to_the_same_ref(state, session, spelling):
    loc = _locator(1)
    session.page.get_by_ref = MagicMock(return_value=loc)
    response, _ = await _run(state, "click", [spelling])
    assert response["success"] is True, response
    session.page.get_by_ref.assert_called_with("e2")
    loc.click.assert_awaited_once()


async def test_a_malformed_ref_is_a_usage_error(state, session):
    response, _ = await _run(state, "click", ["submit"])
    assert response["success"] is False
    assert response["exit_code"] == 2
    assert '"submit" is not a ref (refs look like e5 or f3e5, from snapshot)' in response["output"]


async def test_a_ref_from_another_tab_or_no_snapshot_is_refused(state, session):
    session.page.get_by_ref = MagicMock()
    response, _ = await _run(state, "click", ["e9"])
    assert response["success"] is False
    assert "e9 is not from a snapshot of this tab; run snapshot (refs are per tab" in response["output"]
    session.page.get_by_ref.assert_not_called()

    session.last_raw = None
    response, _ = await _run(state, "click", ["e2"])
    assert "e2 is not from a snapshot of this tab" in response["output"]


async def test_a_ref_that_matches_nothing_heals_once_with_a_fresh_snapshot(state, session):
    loc = _locator(0, 1)
    session.page.get_by_ref = MagicMock(return_value=loc)
    response, snap = await _run(state, "click", ["e2"])
    assert response["success"] is True, response
    assert loc.count.await_count == 2
    # One heal snapshot before the click, one for the page info after it.
    assert snap.await_count == 2
    loc.click.assert_awaited_once()


async def test_a_stale_ref_fails_fast_without_acting(state, session):
    loc = _locator(0, 0)
    session.page.get_by_ref = MagicMock(return_value=loc)
    response, _ = await _run(state, "click", ["e2"])
    assert response["success"] is False
    assert (
        "Ref e2 no longer matches an element (removed or changed since the snapshot). Run snapshot."
        in (response["output"])
    )
    loc.click.assert_not_awaited()


async def test_a_ref_from_a_page_the_tab_left(state, session):
    session.last_raw = '- button "Go" [ref=f1e3]'
    loc = MagicMock()
    loc.count = AsyncMock(side_effect=Exception('Locator.count: Invalid frame in aria-ref selector "aria-ref=f1e3"'))
    session.page.get_by_ref = MagicMock(return_value=loc)
    response, _ = await _run(state, "click", ["f1e3"])
    assert response["success"] is False
    assert "Ref f1e3 is from a page this tab has left. Run snapshot." in response["output"]


@pytest.mark.parametrize("wait_state", ["hidden", "detached"])
async def test_waiting_for_an_element_to_go_waits_on_the_element_not_the_ref(state, session, wait_state):
    element = MagicMock()
    element.wait_for_element_state = AsyncMock()
    session.page.wait_for_function = AsyncMock()
    loc = _locator()
    loc.element_handles = AsyncMock(return_value=[element])
    session.page.get_by_ref = MagicMock(return_value=loc)
    response, _ = await _run(state, "wait-for", ["e2"], state=wait_state)
    assert response["success"] is True, response
    loc.wait_for.assert_not_awaited()
    if wait_state == "hidden":
        element.wait_for_element_state.assert_awaited_once_with("hidden")
    else:
        session.page.wait_for_function.assert_awaited_once_with("e => !e.isConnected", arg=element)


@pytest.mark.parametrize("wait_state", ["hidden", "detached"])
async def test_waiting_for_an_element_that_is_already_gone_succeeds_after_one_heal(state, session, wait_state):
    loc = _locator()
    loc.element_handles = AsyncMock(return_value=[])
    session.page.get_by_ref = MagicMock(return_value=loc)
    response, snap = await _run(state, "wait-for", ["e2"], state=wait_state)
    assert response["success"] is True, response
    assert response["output"] == f"Element is {wait_state}"
    assert loc.element_handles.await_count == 2
    assert snap.await_count == 1


async def test_select_refuses_a_ref_as_the_option(state, session):
    session.page.get_by_ref = MagicMock(return_value=_locator(1))
    response, _ = await _run(state, "select", ["e3", "e4"])
    assert response["success"] is False
    assert "select takes the option label shown under the combobox, e.g. select e3 Green" in response["output"]


async def test_select_timeout_lists_the_options(state, session):
    loc = _locator(1)
    loc.select_option = AsyncMock(side_effect=Exception("Locator.select_option: Timeout 30000ms exceeded."))
    session.page.get_by_ref = MagicMock(return_value=loc)
    response, _ = await _run(state, "select", ["e3", "Purple"])
    assert response["success"] is False
    lines = response["output"].splitlines()
    assert lines[0] == "Error: select on e3 timed out after 30s"
    assert lines[1:3] == ["Options:", '- option "Red"']
    assert lines[-1].startswith("help: run `snapshot` to check e3")


async def test_snapshot_of_a_ref_slices_one_full_snapshot(state, session, tmp_path):
    with patch("patchright_cli.daemon.ai_snapshot", AsyncMock(return_value=RAW)) as snap:
        response = await handle_command(
            state, {"command": "snapshot", "args": ["e3"], "options": {}, "cwd": str(tmp_path)}
        )
    assert response["success"] is True
    snap.assert_awaited_once_with(session.page, boxes=False)
    assert (
        Path(response["snapshot_path"]).read_text(encoding="utf-8")
        == '- combobox "Color" [ref=e3]:\n  - option "Red"\n'
    )
    # The ref map was refreshed from the full page, not from the slice.
    assert session.last_raw == RAW


async def test_snapshot_of_a_ref_gone_from_the_page(state, session, tmp_path):
    with patch("patchright_cli.daemon.ai_snapshot", AsyncMock(return_value='- button "Go" [ref=e2]')):
        response = await handle_command(
            state, {"command": "snapshot", "args": ["e3"], "options": {}, "cwd": str(tmp_path)}
        )
    assert response["success"] is False
    assert "Ref e3 no longer matches an element" in response["output"]


async def test_boxes_inside_an_iframe_are_page_coordinates():
    page = MagicMock()
    page.aria_snapshot = AsyncMock(
        return_value=(
            "- generic [ref=e1] [box=0,0,800,600]:\n"
            "  - iframe [ref=e4] [box=70,80,404,204]:\n"
            '    - button "Inner" [ref=f1e4] [box=260,9,58,21]'
        )
    )
    inner = MagicMock(bounding_box=AsyncMock(return_value={"x": 332.0, "y": 91.0, "width": 58, "height": 21}))
    page.get_by_ref = MagicMock(return_value=inner)
    session = MagicMock()
    raw = await daemon._ai_snapshot(session, page, boxes=True)
    page.get_by_ref.assert_called_once_with("f1e4")
    assert '- button "Inner" [ref=f1e4] [box=332,91,58,21]' in raw
    assert "- iframe [ref=e4] [box=70,80,404,204]:" in raw
    assert session.last_raw == raw


def test_a_tab_starts_without_a_snapshot():
    tab = Tab("t")
    assert tab.last_raw is None
    assert not tab.snapshot_lock.locked()
