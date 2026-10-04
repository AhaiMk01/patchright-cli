"""Unit tests for named Profiles (`--profile=<name>`, profile-list, profile-delete)."""

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from patchright_cli import daemon
from patchright_cli.daemon import DaemonState, Profile, handle_command


def _fake_page():
    page = MagicMock()
    page.url = "about:blank"
    page.close = AsyncMock()
    return page


def _fake_context():
    context = MagicMock()
    context.pages = [_fake_page()]
    context.new_cdp_session = AsyncMock(side_effect=RuntimeError("no CDP in unit tests"))
    context.close = AsyncMock()
    return context


@pytest.fixture
def root(tmp_path, monkeypatch):
    profiles = tmp_path / "profiles"
    monkeypatch.setattr(daemon, "PROFILES_ROOT", profiles)
    return profiles


@pytest.fixture
def state(root):
    st = DaemonState()
    st.playwright = MagicMock()
    st.playwright.devices = {}
    st.playwright.chromium.launch_persistent_context = AsyncMock(side_effect=lambda *a, **k: _fake_context())
    return st


@pytest.fixture
def no_page_info(monkeypatch):
    monkeypatch.setattr(daemon, "_page_info", AsyncMock(return_value={"success": True, "output": ""}))


def _launched_dirs(state):
    return [Path(c.args[0]) for c in state.playwright.chromium.launch_persistent_context.await_args_list]


def _held_by_outside_chrome(monkeypatch, held=True):
    monkeypatch.setattr(daemon, "chrome_holds_profile", lambda directory: held)


async def _run(state, command, args=None, cwd=None, **options):
    return await handle_command(state, {"command": command, "args": args or [], "options": options, "cwd": cwd})


# -- telling names from paths ---------------------------------------------------------


def test_a_plain_value_is_a_named_profile(root):
    assert Profile.parse("work") == Profile(root / "work", "work")


def test_names_may_contain_dots(root):
    assert Profile.parse("my.profile").name == "my.profile"


@pytest.mark.parametrize("spec", ["./work", "a/b", "~/work", "C:\\profiles\\work", "/tmp/work"])
def test_a_value_with_a_separator_is_a_directory(spec):
    profile = Profile.parse(spec)
    assert profile.name is None
    assert profile.directory.is_absolute()
    assert profile.label == str(profile.directory)


@pytest.mark.parametrize("bad", ["", "two words", "-flag", ".work", "~work", "x" * 65, "naïve", "C:work"])
def test_anything_else_must_be_a_valid_name(bad):
    with pytest.raises(ValueError, match="Invalid profile name"):
        Profile.parse(bad)


def test_relative_paths_resolve_against_the_callers_cwd(tmp_path):
    assert Profile.parse("./p", base=str(tmp_path)).directory == (tmp_path / "p").resolve()
    assert Profile.parse("sub/dir", base=str(tmp_path)).directory == (tmp_path / "sub" / "dir").resolve()


# -- which Profile a Session runs on ----------------------------------------------------


@pytest.mark.asyncio
async def test_without_profile_a_session_uses_a_profile_named_after_it(state, root):
    session = await state.get_or_create_session("shop")

    assert _launched_dirs(state) == [root / "shop"]
    assert session.profile.name == "shop"


@pytest.mark.asyncio
async def test_a_session_name_that_is_no_valid_profile_name_needs_an_explicit_profile(state, root):
    with pytest.raises(ValueError, match="cannot double as a profile name"):
        await state.get_or_create_session("../escape")

    assert _launched_dirs(state) == []
    assert (await state.get_or_create_session("../escape", profile="ok")).profile.name == "ok"


@pytest.mark.asyncio
async def test_any_session_can_run_on_a_named_profile(state, root):
    session = await state.get_or_create_session("agent-7", profile="work")

    assert _launched_dirs(state) == [root / "work"]
    assert session.profile.name == "work"
    assert (root / "work").is_dir()


@pytest.mark.asyncio
async def test_open_resolves_a_relative_profile_path_against_the_callers_cwd(state, tmp_path, no_page_info):
    caller = tmp_path / "project"
    caller.mkdir()

    await _run(state, "open", cwd=str(caller), session="s", profile="./.browser")

    assert _launched_dirs(state) == [(caller / ".browser").resolve()]


# -- one Session per Profile -------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_busy_profile_is_refused_with_a_pointer_to_tab(state):
    await state.get_or_create_session("a", profile="work")

    with pytest.raises(ValueError, match=r"in use by session 'a'.*--tab"):
        await state.get_or_create_session("b", profile="work")

    assert len(_launched_dirs(state)) == 1


@pytest.mark.asyncio
async def test_a_name_and_a_path_to_the_same_profile_count_as_one(state, root):
    await state.get_or_create_session("a", profile="work")

    with pytest.raises(ValueError, match="in use by session 'a'"):
        await state.get_or_create_session("b", profile=str(root / "work"))


@pytest.mark.asyncio
async def test_concurrent_opens_cannot_both_claim_a_profile(state):
    launch = state.playwright.chromium.launch_persistent_context

    async def slow_launch(*args, **kwargs):
        await asyncio.sleep(0.01)
        return _fake_context()

    launch.side_effect = slow_launch

    results = await asyncio.gather(
        state.get_or_create_session("a", profile="work"),
        state.get_or_create_session("b", profile="work"),
        return_exceptions=True,
    )

    assert sum(isinstance(r, ValueError) for r in results) == 1
    assert launch.await_count == 1


@pytest.mark.asyncio
async def test_closing_a_session_frees_its_profile(state):
    await state.get_or_create_session("a", profile="work")
    await state.close_session("a")

    assert (await state.get_or_create_session("b", profile="work")).profile.name == "work"


@pytest.mark.asyncio
async def test_kill_all_frees_profiles(state):
    await state.get_or_create_session("a", profile="work")

    await _run(state, "kill-all", session="a")

    assert state.profile_owners == {}


@pytest.mark.asyncio
async def test_reopening_a_session_on_another_profile_is_refused_not_ignored(state):
    first = await state.get_or_create_session("a", profile="work")

    assert await state.get_or_create_session("a", profile="work") is first
    with pytest.raises(ValueError, match="already open on profile 'work'"):
        await state.get_or_create_session("a", profile="home")


# -- a failed open never strands a Profile ------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_option",
    [{"device": "No Such Phone"}, {"viewport": {"width": "wide", "height": "1"}}, {"geolocation": {"lat": "x"}}],
)
async def test_a_bad_option_leaves_the_profile_free(state, bad_option):
    state.playwright.devices = {}

    with pytest.raises((ValueError, KeyError)):
        await state.get_or_create_session("a", profile="work", **bad_option)

    assert state.profile_owners == {}


@pytest.mark.asyncio
async def test_an_uncreatable_profile_directory_leaves_the_profile_free(state, monkeypatch):
    def refuse(self, *args, **kwargs):
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "mkdir", refuse)

    with pytest.raises(PermissionError):
        await state.get_or_create_session("a", profile="work")

    assert state.profile_owners == {}


@pytest.mark.asyncio
async def test_a_failed_launch_frees_the_profile(state):
    state.playwright.chromium.launch_persistent_context.side_effect = [RuntimeError("chrome crashed"), _fake_context()]

    with pytest.raises(RuntimeError, match="chrome crashed"):
        await state.get_or_create_session("a", profile="work")

    assert (await state.get_or_create_session("b", profile="work")).profile.name == "work"


@pytest.mark.asyncio
async def test_a_failure_after_launch_closes_chrome_and_frees_the_profile(state, root):
    context = _fake_context()
    context.on = MagicMock(side_effect=RuntimeError("listener boom"))
    state.playwright.chromium.launch_persistent_context.side_effect = None
    state.playwright.chromium.launch_persistent_context.return_value = context

    with pytest.raises(RuntimeError, match="listener boom"):
        await state.get_or_create_session("a", profile="work")

    context.close.assert_awaited_once()
    assert state.profile_owners == {}


@pytest.mark.asyncio
async def test_a_profile_held_by_an_outside_chrome_gets_a_clear_error(state, monkeypatch):
    _held_by_outside_chrome(monkeypatch)
    state.playwright.chromium.launch_persistent_context.side_effect = RuntimeError("Target page, context or browser")

    with pytest.raises(RuntimeError, match="open in another Chrome outside this daemon"):
        await state.get_or_create_session("a", profile="work")

    assert state.profile_owners == {}


# -- leftover lock files ------------------------------------------------------------------


def test_a_lockfile_left_by_a_crash_does_not_count(tmp_path):
    (tmp_path / "lockfile").write_text("")

    assert daemon.chrome_holds_profile(tmp_path) is False


def test_a_lockfile_chrome_holds_open_counts(tmp_path, monkeypatch):
    (tmp_path / "lockfile").write_text("")
    monkeypatch.setattr(daemon, "_lockfile_in_use", lambda path: True)

    assert daemon.chrome_holds_profile(tmp_path) is True


@pytest.mark.skipif(os.name == "nt", reason="SingletonLock is how Chrome locks on macOS/Linux")
def test_a_singleton_lock_pointing_at_a_dead_process_does_not_count(tmp_path):
    import socket

    os.symlink(f"{socket.gethostname()}-999999999", tmp_path / "SingletonLock")

    assert daemon.chrome_holds_profile(tmp_path) is False


@pytest.mark.skipif(os.name == "nt", reason="SingletonLock is how Chrome locks on macOS/Linux")
def test_a_singleton_lock_pointing_at_a_live_process_counts(tmp_path):
    import socket

    os.symlink(f"{socket.gethostname()}-{os.getpid()}", tmp_path / "SingletonLock")

    assert daemon.chrome_holds_profile(tmp_path) is True


# -- profile-list / profile-delete --------------------------------------------------------


@pytest.mark.asyncio
async def test_profile_list_shows_profiles_and_who_uses_them(state, root):
    (root / "idle").mkdir(parents=True)
    (root / "idle" / "data").write_bytes(b"x" * 2_000_000)
    (root / "not a profile").mkdir()
    await state.get_or_create_session("agent", profile="work")

    response = await _run(state, "profile-list")

    assert response["success"] is True
    assert "- idle (2.0 MB)" in response["output"]
    assert "- work (0.0 MB) -- in use by session 'agent'" in response["output"]
    assert "not a profile" not in response["output"]


@pytest.mark.asyncio
async def test_profile_list_when_there_are_none(state):
    response = await _run(state, "profile-list")

    assert "none yet" in response["output"]


@pytest.mark.asyncio
async def test_profile_delete_removes_an_idle_profile(state, root):
    (root / "old").mkdir(parents=True)

    response = await _run(state, "profile-delete", ["old"])

    assert response["success"] is True
    assert not (root / "old").exists()


@pytest.mark.asyncio
async def test_profile_delete_refuses_a_profile_in_use(state, root):
    await state.get_or_create_session("agent", profile="work")

    response = await _run(state, "profile-delete", ["work"])

    assert response["success"] is False
    assert "in use by session 'agent'" in response["output"]
    assert (root / "work").exists()


@pytest.mark.asyncio
async def test_profile_delete_refuses_paths(state, tmp_path):
    victim = tmp_path / "not-ours"
    victim.mkdir()

    response = await _run(state, "profile-delete", [str(victim)])

    assert response["success"] is False
    assert victim.exists()


@pytest.mark.asyncio
async def test_profile_delete_refuses_a_profile_an_outside_chrome_has_open(state, root, monkeypatch):
    (root / "work").mkdir(parents=True)
    _held_by_outside_chrome(monkeypatch)

    response = await _run(state, "profile-delete", ["work"])

    assert response["success"] is False
    assert (root / "work").exists()


@pytest.mark.asyncio
async def test_profile_delete_of_a_missing_profile(state):
    response = await _run(state, "profile-delete", ["ghost"])

    assert response["success"] is False
    assert "No profile named 'ghost'" in response["output"]


# -- delete-data only ever deletes the Session's own Profile ------------------------------


@pytest.mark.asyncio
async def test_delete_data_deletes_the_profile_named_after_the_session(state, root):
    await state.get_or_create_session("shop")

    response = await _run(state, "delete-data", session="shop")

    assert response["success"] is True, response
    assert not (root / "shop").exists()
    assert "shop" not in state.sessions


@pytest.mark.asyncio
async def test_delete_data_refuses_a_shared_named_profile(state, root):
    await state.get_or_create_session("a", profile="work")

    response = await _run(state, "delete-data", session="a")

    assert response["success"] is False
    assert "profile-delete work" in response["output"]
    assert (root / "work").exists()
    assert "a" in state.sessions


@pytest.mark.asyncio
async def test_delete_data_never_deletes_a_profile_given_as_a_path(state, tmp_path):
    mine = tmp_path / "my-chrome-data"
    await state.get_or_create_session("a", profile=str(mine))

    response = await _run(state, "delete-data", session="a")

    assert response["success"] is False
    assert "never deletes a directory" in response["output"]
    assert mine.exists()


@pytest.mark.asyncio
async def test_delete_data_refuses_once_another_session_took_the_profile(state, root):
    await state.get_or_create_session("b", profile="a")

    response = await _run(state, "delete-data", session="a")

    assert response["success"] is False
    assert "in use by session 'b'" in response["output"]
    assert (root / "a").exists()


@pytest.mark.asyncio
async def test_delete_data_refuses_a_profile_an_outside_chrome_has_open(state, root, monkeypatch):
    (root / "a").mkdir(parents=True)
    _held_by_outside_chrome(monkeypatch)

    response = await _run(state, "delete-data", session="a")

    assert response["success"] is False
    assert (root / "a").exists()
