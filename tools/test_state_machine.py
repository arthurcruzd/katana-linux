#!/usr/bin/env python3
"""Regression tests for UI server connection/readback state semantics."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ui_server as s
from katana_ble import KatanaBLE


class FakeKatana:
    def __init__(self, *, read_fails=False):
        self.read_fails = read_fails
        self.applied = []

    async def _is_connected(self):
        return True

    async def read_status_light(self):
        if self.read_fails:
            raise TimeoutError("no DT1 for 0x20000000")
        return {"name": "FAKE", "pitch": -1, "amp": {"volume": 38}, "sw": {}}

    async def apply_preset(self, preset, volume_cap=None):
        self.applied.append((preset.get("name"), volume_cap))

    async def request(self, *args, **kwargs):
        if self.read_fails:
            raise TimeoutError("no DT1")
        return [0, 0, 23] + [0] * 9


async def reset():
    s._connect_task = None
    s._katana = None
    s._state.update(
        connected=False,
        pitch=-1,
        name="",
        error="",
        pitch_armed=False,
        busy="",
    )


async def test_shared_connect():
    await reset()
    calls = 0

    async def fake_run():
        nonlocal calls
        calls += 1
        s._state["busy"] = "connecting"
        await asyncio.sleep(0.08)
        s._katana = FakeKatana()
        s._state["connected"] = True
        s._state["busy"] = ""
        return {"ok": True, "connected": True, "name": "FAKE", "pitch": -1}

    with patch.object(s, "_run_connect", fake_run):
        a, b = await asyncio.gather(s.connect(), s.connect())
    assert calls == 1, calls
    assert a["connected"] and b["connected"]
    print("PASS two concurrent connects share one task")


async def test_command_waits_connect():
    await reset()

    async def connecting():
        await asyncio.sleep(0.08)
        s._katana = FakeKatana()
        s._state["connected"] = True
        return {"ok": True}

    s._connect_task = asyncio.create_task(connecting())

    async def op(k):
        assert k is s._katana
        return "ran"

    result = await s.with_ops(op)
    assert result == "ran"
    print("PASS command waits for in-flight connect")


async def test_load_succeeds_when_readback_fails():
    await reset()
    fake = FakeKatana(read_fails=True)
    s._katana = fake
    s._state["connected"] = True
    result = await s.load_preset("like-a-stone-intro.json")
    assert result["ok"] is True
    assert result["readback_ok"] is False
    assert "preset aplicado" in result["warning"]
    assert fake.applied
    assert s._state["connected"] is True
    print("PASS preset write success survives readback timeout")


async def test_status_stays_connected_on_read_timeout():
    await reset()
    s._katana = FakeKatana(read_fails=True)
    s._state["connected"] = True
    s._state["name"] = "CACHED"
    result = await s.status()
    body = result.body.decode() if hasattr(result, "body") else ""
    assert '"connected":true' in body
    assert "warning" in body
    assert s._state["connected"] is True
    print("PASS read timeout does not create false disconnect")


async def test_audio_name_is_rejected() -> None:
    k = KatanaBLE()
    k._is_connected = AsyncMock(return_value=False)
    k._call = AsyncMock(side_effect=RuntimeError("not discovering"))
    k._btctl = AsyncMock(return_value=(0, "scan complete"))
    k._device_name = AsyncMock(return_value="KATANA 3 Audio")
    try:
        await k._ensure_connected(retries=1)
    except RuntimeError as exc:
        assert "dispositivo errado" in str(exc)
        assert "Audio" in str(exc)
    else:
        raise AssertionError("Audio device was accepted as MIDI")
    print("PASS KATANA 3 Audio is rejected before connect")


async def main() -> None:
    await test_shared_connect()
    await test_command_waits_connect()
    await test_load_succeeds_when_readback_fails()
    await test_status_stays_connected_on_read_timeout()
    await test_audio_name_is_rejected()
    print("PASS all state-machine regressions")


if __name__ == "__main__":
    asyncio.run(main())
