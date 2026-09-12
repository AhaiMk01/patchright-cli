"""Unit tests for the `show --annotate` round-trip."""

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from patchright_cli.dashboard import DashboardState

PNG_1PX = base64.b64encode(
    bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000a49444154789c6300010000050001"
        "0d0a2db40000000049454e44ae426082"
    )
).decode()


def _state():
    daemon_state = MagicMock()
    daemon_state.sessions = {}
    return DashboardState(daemon_state)


@pytest.mark.asyncio
async def test_open_annotation_returns_a_unique_token():
    state = _state()
    first, _ = state.open_annotation("default", "default")
    second, _ = state.open_annotation("default", "default")
    assert first != second
    assert set(state.annotations) == {first, second}


@pytest.mark.asyncio
async def test_resolving_an_annotation_completes_its_waiter():
    state = _state()
    token, waiter = state.open_annotation("default", "default")

    assert state.resolve_annotation(token, {"notes": "shift the CTA left"}) is True

    result = await asyncio.wait_for(waiter, timeout=1)
    assert result["notes"] == "shift the CTA left"


@pytest.mark.asyncio
async def test_resolving_an_unknown_token_reports_failure():
    state = _state()
    assert state.resolve_annotation("not-a-token", {"notes": ""}) is False


@pytest.mark.asyncio
async def test_resolving_twice_only_lands_once():
    state = _state()
    token, waiter = state.open_annotation("default", "default")

    assert state.resolve_annotation(token, {"notes": "first"}) is True
    assert state.resolve_annotation(token, {"notes": "second"}) is False

    assert (await asyncio.wait_for(waiter, timeout=1))["notes"] == "first"


@pytest.mark.asyncio
async def test_cancelling_an_annotation_drops_it():
    state = _state()
    token, waiter = state.open_annotation("default", "default")

    state.cancel_annotation(token)

    assert token not in state.annotations
    assert waiter.cancelled() or waiter.done()


@pytest.mark.asyncio
async def test_annotation_target_is_recorded_for_the_page_to_read():
    state = _state()
    token, _ = state.open_annotation("work", "inbox")
    target = state.annotation_target(token)
    assert target == {"session": "work", "tab": "inbox"}


@pytest.mark.asyncio
async def test_annotation_target_of_an_unknown_token_is_none():
    assert _state().annotation_target("nope") is None


# -- decoding ----------------------------------------------------------------


def test_decode_annotation_image_accepts_a_data_url():
    from patchright_cli.dashboard import decode_annotation_image

    raw = decode_annotation_image(f"data:image/png;base64,{PNG_1PX}")
    assert raw.startswith(b"\x89PNG")


def test_decode_annotation_image_accepts_bare_base64():
    from patchright_cli.dashboard import decode_annotation_image

    assert decode_annotation_image(PNG_1PX).startswith(b"\x89PNG")


def test_decode_annotation_image_rejects_a_non_png():
    from patchright_cli.dashboard import decode_annotation_image

    with pytest.raises(ValueError):
        decode_annotation_image(base64.b64encode(b"not an image").decode())


def test_decode_annotation_image_rejects_garbage():
    from patchright_cli.dashboard import decode_annotation_image

    with pytest.raises(ValueError):
        decode_annotation_image("%%%not base64%%%")


def test_decode_annotation_image_accepts_nothing_submitted():
    from patchright_cli.dashboard import decode_annotation_image

    assert decode_annotation_image(None) is None
    assert decode_annotation_image("") is None


# -- the show --annotate handler --------------------------------------------


@pytest.fixture
def annotate_session(tmp_path):
    from patchright_cli.daemon import DaemonState, Session

    context = MagicMock()
    page = MagicMock()
    page.url = "https://example.com"
    page.title = AsyncMock(return_value="Example")
    page.screenshot = AsyncMock(return_value=b"\x89PNG-bytes")
    page.is_closed = MagicMock(return_value=False)
    session = Session("default", context, [page])

    state = MagicMock(spec=DaemonState)
    state.sessions = {"default": session}
    return state, session, page


@pytest.mark.asyncio
async def test_show_annotate_returns_the_submitted_feedback(annotate_session, tmp_path, monkeypatch):
    from patchright_cli import daemon as daemon_mod

    state, session, page = annotate_session
    dashboard_state = _state()

    async def fake_start(daemon_state, port=9322):
        return MagicMock(), "http://127.0.0.1:9322", dashboard_state

    monkeypatch.setattr(daemon_mod, "_dashboard_runners", {})
    monkeypatch.setattr("patchright_cli.dashboard.start_dashboard_server", fake_start)
    monkeypatch.setattr(
        daemon_mod, "take_snapshot", AsyncMock(return_value=("- button \"Ship it\" [ref=e1]", MagicMock()))
    )

    async def submit_soon():
        await asyncio.sleep(0.05)
        token = next(iter(dashboard_state.annotations))
        dashboard_state.resolve_annotation(token, {"notes": "tighten the spacing", "image": PNG_1PX})

    asyncio.ensure_future(submit_soon())

    result = await daemon_mod.cmd_show(
        session, page, [], {"annotate": True, "wait": "5"}, str(tmp_path), state
    )

    assert result["success"] is True
    assert "tighten the spacing" in result["output"]
    assert result["annotation_path"].endswith(".png")


@pytest.mark.asyncio
async def test_show_annotate_times_out_without_a_submission(annotate_session, tmp_path, monkeypatch):
    from patchright_cli import daemon as daemon_mod

    state, session, page = annotate_session
    dashboard_state = _state()

    async def fake_start(daemon_state, port=9322):
        return MagicMock(), "http://127.0.0.1:9322", dashboard_state

    monkeypatch.setattr(daemon_mod, "_dashboard_runners", {})
    monkeypatch.setattr("patchright_cli.dashboard.start_dashboard_server", fake_start)

    result = await daemon_mod.cmd_show(session, page, [], {"annotate": True, "wait": "0.1"}, str(tmp_path), state)

    assert result["success"] is False
    assert "annotation" in result["output"].lower()
    assert dashboard_state.annotations == {}


@pytest.mark.asyncio
async def test_show_without_annotate_does_not_block(annotate_session, tmp_path, monkeypatch):
    from patchright_cli import daemon as daemon_mod

    state, session, page = annotate_session
    dashboard_state = _state()

    async def fake_start(daemon_state, port=9322):
        return MagicMock(), "http://127.0.0.1:9322", dashboard_state

    monkeypatch.setattr(daemon_mod, "_dashboard_runners", {})
    monkeypatch.setattr("patchright_cli.dashboard.start_dashboard_server", fake_start)

    result = await asyncio.wait_for(
        daemon_mod.cmd_show(session, page, [], {}, str(tmp_path), state), timeout=1
    )

    assert result["success"] is True
    assert "127.0.0.1:9322" in result["output"]
    assert dashboard_state.annotations == {}


@pytest.mark.asyncio
async def test_show_annotate_opens_the_review_link(annotate_session, tmp_path, monkeypatch):
    from patchright_cli import daemon as daemon_mod

    state, session, page = annotate_session
    dashboard_state = _state()
    opened = []

    async def fake_start(daemon_state, port=9322):
        return MagicMock(), "http://127.0.0.1:9322", dashboard_state

    monkeypatch.setattr(daemon_mod, "_dashboard_runners", {})
    monkeypatch.setattr("patchright_cli.dashboard.start_dashboard_server", fake_start)
    monkeypatch.setattr("webbrowser.open", lambda u: opened.append(u))

    await daemon_mod.cmd_show(session, page, [], {"annotate": True, "wait": "0.1"}, str(tmp_path), state)

    assert len(opened) == 1
    assert "/annotate?token=" in opened[0]


@pytest.mark.asyncio
async def test_show_annotate_no_open_suppresses_the_browser(annotate_session, tmp_path, monkeypatch):
    from patchright_cli import daemon as daemon_mod

    state, session, page = annotate_session
    dashboard_state = _state()
    opened = []

    async def fake_start(daemon_state, port=9322):
        return MagicMock(), "http://127.0.0.1:9322", dashboard_state

    monkeypatch.setattr(daemon_mod, "_dashboard_runners", {})
    monkeypatch.setattr("patchright_cli.dashboard.start_dashboard_server", fake_start)
    monkeypatch.setattr("webbrowser.open", lambda u: opened.append(u))

    await daemon_mod.cmd_show(
        session, page, [], {"annotate": True, "wait": "0.1", "no-open": True}, str(tmp_path), state
    )

    assert opened == []


@pytest.mark.asyncio
async def test_annotation_pins_the_page_it_was_opened_against():
    from patchright_cli.dashboard import _target_page

    state = _state()
    asked_about = MagicMock(name="page-the-agent-was-on")
    token, _ = state.open_annotation("default", "default", asked_about)

    # The session moves on -- a popup, a tab-select, the review page itself.
    moved_on = MagicMock(name="some-other-page")
    session = MagicMock()
    session.page = moved_on
    session.tabs = {"default": MagicMock(page=moved_on)}
    state.daemon_state.sessions = {"default": session}

    assert _target_page(state, token) is asked_about


@pytest.mark.asyncio
async def test_annotation_target_does_not_leak_the_page_object():
    state = _state()
    token, _ = state.open_annotation("work", "inbox", MagicMock())
    assert state.annotation_target(token) == {"session": "work", "tab": "inbox"}
