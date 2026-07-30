#!/usr/bin/env python3
"""Regression tests for UI server connection/readback state semantics."""
from __future__ import annotations

import asyncio
import hashlib
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ui_server as s
from katana_ble import KatanaBLE, addr_add


class FakeKatana:
    def __init__(self, *, read_fails=False):
        self.read_fails = read_fails
        self.applied = []
        self.selected = []
        self.written_slots = []
        self.verified_slots = []

    async def _is_connected(self):
        return True

    async def read_status_light(self):
        if self.read_fails:
            raise TimeoutError("no DT1 for 0x20000000")
        return {"name": "FAKE", "pitch": -1, "amp": {"volume": 38}, "sw": {}}

    async def apply_preset(self, preset, volume_cap=None):
        self.applied.append((preset.get("name"), volume_cap))

    async def select_patch(self, slot):
        self.selected.append(slot)

    async def write_current_patch_to_slot(self, slot, timeout=4.0):
        self.written_slots.append(slot)

    async def verify_slot_matches_live(self, slot, ranges, timeout=3.0):
        self.verified_slots.append((slot, ranges))
        return sum(size for _, size in ranges)

    async def request(self, *args, **kwargs):
        if self.read_fails:
            raise TimeoutError("no DT1")
        return [0, 0, 23] + [0] * 9


async def reset():
    s._connect_task = None
    s._katana = None
    if hasattr(s, "_live_slots"):
        s._live_slots.clear()
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


async def test_live_slot_protocol() -> None:
    k = KatanaBLE()
    k.write_bytes = AsyncMock()
    await k.select_patch(3)
    k.write_bytes.assert_awaited_once_with(0x7F000100, [0, 3], gap=0.0, pace=0.0)

    try:
        await k.select_patch(10)
    except ValueError:
        pass
    else:
        raise AssertionError("slot 10 should be rejected")
    print("PASS live slot select protocol")


async def test_live_slot_write_ack() -> None:
    from katana_ble import dt1

    k = KatanaBLE()
    writes = []

    async def fake_write(addr, data, **kwargs):
        writes.append((addr, list(data)))
        if addr == 0x7F000104:
            k.sysex.append(dt1(addr, data))

    k.write_bytes = fake_write
    await k.write_current_patch_to_slot(4, timeout=0.2)
    assert writes == [(0x7F000001, [1]), (0x7F000104, [0, 4])]
    print("PASS live slot write acknowledgement")


async def test_live_prepared_preset_uses_atomic_select() -> None:
    await reset()
    preset_id = "like-a-stone-intro.json"
    path = s.PRESETS / preset_id
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    s._live_slots = {preset_id: {"slot": 2, "digest": digest, "volume_cap": 37}}
    fake = FakeKatana()
    s._katana = fake
    s._state["connected"] = True

    result = await s.load_preset(preset_id)
    assert result["atomic"] is True
    assert result["live_slot"] == 2
    assert result["amp"]["volume"] <= 37
    assert fake.selected == [2]
    assert fake.applied == []
    print("PASS prepared preset uses one atomic slot select")


async def test_prepare_live_writes_and_maps_slots() -> None:
    await reset()
    fake = FakeKatana()
    s._katana = fake
    s._state["connected"] = True
    ids = ["like-a-stone-intro.json", "master-of-puppets-riff.json"]
    body = s.LivePrepareIn(preset_ids=ids)

    with tempfile.TemporaryDirectory() as td:
        state_path = Path(td) / "live.json"
        with patch.object(s, "LIVE_STATE", state_path):
            result = await s.prepare_live(body)
            assert state_path.exists(), "prepare_live must persist the slot manifest"
    assert result["ok"] is True
    assert result["prepared"] == 2
    assert fake.written_slots == [0, 1]
    assert [slot for slot, _ in fake.verified_slots] == [0, 1]
    assert fake.selected[-1] == 0
    assert s._live_slots[ids[0]]["slot"] == 0
    assert s._live_slots[ids[1]]["slot"] == 1
    assert all(s._live_slots[x]["digest"] for x in ids)
    print("PASS live preparation writes, maps and recalls first slot")


async def test_live_manifest_round_trip() -> None:
    with tempfile.TemporaryDirectory() as td:
        state_path = Path(td) / "live.json"
        with patch.object(s, "LIVE_STATE", state_path):
            s._live_slots.clear()
            s._live_slots["a.json"] = {"slot": 0, "digest": "abc", "name": "A"}
            s._save_live_slots()
            s._live_slots.clear()
            s._load_live_slots()
            assert s._live_slots == {"a.json": {"slot": 0, "digest": "abc", "name": "A"}}
    print("PASS live manifest survives backend restart")


async def test_roland_address_addition() -> None:
    assert addr_add(0x20000070, 0x20) == 0x20000110
    assert addr_add(0x20007E00, 0x200) == 0x20010200
    assert addr_add(0x20000000, 0) == 0x20000000
    print("PASS Roland base-128 address addition")


async def test_slot_readback_verification() -> None:
    k = KatanaBLE()
    calls = []

    async def same_request(addr, size, **kwargs):
        calls.append((addr, size))
        return [size & 0x7F] * size

    k.request = same_request
    await k.verify_slot_matches_live(0, [(0x0000, 16), (0x0600, 10)])
    assert calls == [
        (0x20000000, 16), (0x20100000, 16),
        (0x20000600, 10), (0x20100600, 10),
    ]

    async def different_request(addr, size, **kwargs):
        return ([2] if addr == 0x20100000 else [1]) * size

    k.request = different_request
    try:
        await k.verify_slot_matches_live(0, [(0x0000, 16)])
    except RuntimeError as e:
        assert "slot 0" in str(e)
    else:
        raise AssertionError("divergent persistent slot must fail verification")
    print("PASS persistent slot readback verification")


async def test_verification_ranges_skip_unreadable_fx_blocks() -> None:
    structured = s._verification_ranges({"name": "x"})
    offsets = {rel for rel, _ in structured}
    assert offsets == {0x0000, 0x0600, 0x0800}

    raw = {
        "raw_blocks": {
            "PATCH%COM": [1] * 16,
            "PATCH%AMP": [2] * 10,
            "PATCH%SW": [1] * 6,
            "PATCH%BOOSTER(1)": [2] * 8,
            "PATCH%DELAY(1)": [3] * 17,
            "PATCH%REVERB(1)": [4] * 13,
            "PATCH%FX(1)": [3],
            "PATCH%FX_DETAIL(1)": [4] * 91,
        }
    }
    raw_offsets = {rel for rel, _ in s._verification_ranges(raw)}
    assert raw_offsets == {0x0000, 0x0600, 0x0800}
    print("PASS verification skips firmware-unreadable FX blocks")


async def main() -> None:
    await test_shared_connect()
    await test_command_waits_connect()
    await test_load_succeeds_when_readback_fails()
    await test_status_stays_connected_on_read_timeout()
    await test_audio_name_is_rejected()
    await test_live_slot_protocol()
    await test_live_slot_write_ack()
    await test_live_prepared_preset_uses_atomic_select()
    await test_prepare_live_writes_and_maps_slots()
    await test_live_manifest_round_trip()
    await test_roland_address_addition()
    await test_slot_readback_verification()
    await test_verification_ranges_skip_unreadable_fx_blocks()
    print("PASS all state-machine regressions")


if __name__ == "__main__":
    asyncio.run(main())
