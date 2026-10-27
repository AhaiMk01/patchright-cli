"""The daemon bounds how long a request may take to arrive and how large it may be."""

import asyncio
import json
import struct

import pytest

from patchright_cli import daemon


class FakeWriter:
    def __init__(self):
        self.data = b""
        self.closed = False

    def get_extra_info(self, name):
        return ("127.0.0.1", 9396)

    def write(self, data):
        self.data += data

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass

    def reply(self) -> dict:
        length = struct.unpack("!I", self.data[:4])[0]
        return json.loads(self.data[4 : 4 + length].decode("utf-8"))


def _frame(msg: dict) -> bytes:
    data = json.dumps(msg).encode("utf-8")
    return struct.pack("!I", len(data)) + data


def _state() -> daemon.DaemonState:
    state = daemon.DaemonState()
    state.auth_token = "tok"
    return state


async def test_read_message_returns_a_message_within_the_limit():
    reader = asyncio.StreamReader()
    frame = _frame({"command": "list"})
    reader.feed_data(frame)
    assert await daemon._read_message(reader, max_bytes=len(frame) - 4) == {"command": "list"}


async def test_read_message_rejects_an_oversized_length_without_reading_the_body():
    reader = asyncio.StreamReader()
    # Only the header arrives: reading the body would wait forever.
    reader.feed_data(struct.pack("!I", 0xFFFFFFFF))
    with pytest.raises(daemon.RequestTooLarge, match="4294967295 bytes"):
        await asyncio.wait_for(daemon._read_message(reader), 1)


def test_request_cap_is_well_above_a_large_script():
    assert daemon.MAX_REQUEST_BYTES >= 16 * 1024 * 1024


async def test_oversized_request_gets_an_error_and_runs_nothing(monkeypatch):
    ran = []

    async def handle_command(state, msg):
        ran.append(msg)
        return {"success": True, "output": ""}

    monkeypatch.setattr(daemon, "handle_command", handle_command)
    reader = asyncio.StreamReader()
    reader.feed_data(struct.pack("!I", daemon.MAX_REQUEST_BYTES + 1))
    writer = FakeWriter()

    await asyncio.wait_for(daemon._handle_client(reader, writer, _state()), 1)

    assert ran == []
    assert writer.closed
    assert writer.reply()["success"] is False
    assert writer.reply()["output"].startswith("Daemon error: request of")  # documented in docs/daemon-protocol.md


@pytest.mark.parametrize(
    "sent",
    [b"", b"\x00\x00", struct.pack("!I", 100) + b"{"],
    ids=["nothing", "partial-header", "partial-body"],
)
async def test_stalled_request_is_closed_after_the_read_timeout(monkeypatch, sent):
    monkeypatch.setattr(daemon, "REQUEST_READ_TIMEOUT", 0.05)
    reader = asyncio.StreamReader()
    reader.feed_data(sent)
    writer = FakeWriter()

    await asyncio.wait_for(daemon._handle_client(reader, writer, _state()), 1)

    assert writer.closed
    assert writer.data == b""


async def test_read_timeout_does_not_bound_the_command(monkeypatch):
    monkeypatch.setattr(daemon, "REQUEST_READ_TIMEOUT", 0.05)

    async def handle_command(state, msg):
        await asyncio.sleep(0.2)
        return {"success": True, "output": "done"}

    monkeypatch.setattr(daemon, "handle_command", handle_command)
    reader = asyncio.StreamReader()
    reader.feed_data(_frame({"command": "list", "args": [], "options": {}, "token": "tok"}))
    writer = FakeWriter()

    await asyncio.wait_for(daemon._handle_client(reader, writer, _state()), 2)

    assert writer.reply() == {"success": True, "output": "done"}
