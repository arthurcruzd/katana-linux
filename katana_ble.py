#!/usr/bin/env python3
"""Katana Gen 3 BLE-MIDI client (BlueZ D-Bus, bonded).

  .venv/bin/python katana_ble.py status
  .venv/bin/python katana_ble.py dump
  .venv/bin/python katana_ble.py save presets/foo.json
  .venv/bin/python katana_ble.py load presets/foo.json
  .venv/bin/python katana_ble.py set amp.gain 80
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Callable

from dbus_fast import BusType, Message, MessageType, Variant
from dbus_fast.aio import MessageBus

ADDR_DEFAULT = "E7:47:8F:03:0D:C4"
DEVICE_ID = 0x10
MODEL_ID = bytes([0x01, 0x05, 0x07])

# temp-patch logical addresses (on-wire after BTS nibble/_7bitize round-trip)
ADDR = {
    "com": 0x20000000,       # name (16 ascii)
    "amp": 0x20000600,       # 10 bytes
    "sw": 0x20000800,        # booster/mod/fx/delay/delay2/reverb switches
    "booster": 0x20000A00,   # booster var 1
    "fx": 0x20001000,        # FX type (slot 1)
    "fx_detail": 0x20001C00, # FX detail block (slot 1)
    "delay": 0x20002800,     # delay var 1
    "reverb": 0x20003400,    # reverb var 1
}

# FX type index in BTS resource list
FX_PITCH_SHIFTER = 11

# Pitch shifter fields inside FX_DETAIL (relative offsets)
PS_VOICE = 0x48
PS_MODE1 = 0x49
PS_PITCH1 = 0x4A   # ofs 24  -> stored = pitch + 24
PS_FINE1 = 0x4B    # ofs 50
PS_PREDELAY1 = 0x4C  # INTEGER4x4
PS_LEVEL1 = 0x50
PS_DIRECT_MIX = 0x5A

AMP_TYPE = ["acoustic", "clean", "pushed", "crunch", "lead", "brown"]
DELAY_TYPE = [
    "digital", "pan", "stereo", "analog", "tape_echo", "reverse", "modulate", "sde3000"
]
REVERB_TYPE = ["plate", "room", "hall", "spring", "modulate"]

AMP_FIELDS = [
    "gain", "volume", "bass", "middle", "treble", "presence",
    "poweramp_variation", "type", "resonance", "preamp_variation",
]
SW_FIELDS = ["booster", "mod", "fx", "delay", "delay2", "reverb"]


def a4(v: int) -> list[int]:
    return [(v >> 24) & 0x7F, (v >> 16) & 0x7F, (v >> 8) & 0x7F, v & 0x7F]


def checksum(body: list[int]) -> int:
    return (128 - (sum(body) % 128)) & 0x7F


def rq1(addr: int, size: int) -> bytes:
    body = a4(addr) + a4(size)
    return bytes([0xF0, 0x41, DEVICE_ID, *MODEL_ID, 0x11, *body, checksum(body), 0xF7])


def dt1(addr: int, data: list[int]) -> bytes:
    body = a4(addr) + list(data)
    return bytes([0xF0, 0x41, DEVICE_ID, *MODEL_ID, 0x12, *body, checksum(body), 0xF7])


def enc_4x4(v: int) -> list[int]:
    v = max(0, min(0xFFFF, int(v)))
    return [(v >> 12) & 0xF, (v >> 8) & 0xF, (v >> 4) & 0xF, v & 0xF]


def dec_4x4(b: list[int]) -> int:
    return ((b[0] & 0xF) << 12) | ((b[1] & 0xF) << 8) | ((b[2] & 0xF) << 4) | (b[3] & 0xF)


def ble_wrap(sysex: bytes, mtu: int = 20) -> list[bytes]:
    out: list[bytes] = []
    data = list(sysex)
    first = True
    chunk = max(mtu - 4, 12)
    while data:
        part, data = data[:chunk], data[chunk:]
        pkt = [0x80]
        if first:
            pkt.append(0x80)
            first = False
        if not data and part and part[-1] == 0xF7:
            pkt.extend(part[:-1] + [0x80, 0xF7])
        else:
            pkt.extend(part)
        out.append(bytes(pkt))
    return out


def ble_unwrap_feed(state: bytearray, pkt: bytes, on_sysex: Callable[[bytes], None]) -> None:
    for b in pkt[1:]:
        if b == 0xF0:
            state.clear()
            state.append(0xF0)
        elif b == 0xF7:
            if state:
                state.append(0xF7)
                on_sysex(bytes(state))
                state.clear()
        elif b & 0x80:
            continue
        elif state:
            state.append(b)


def parse_dt1(msg: bytes) -> tuple[int, list[int]] | None:
    if len(msg) < 15 or msg[0] != 0xF0 or msg[1] != 0x41 or msg[-1] != 0xF7:
        return None
    if msg[2] not in (DEVICE_ID, 0x7F):
        return None
    if bytes(msg[3:6]) != MODEL_ID or msg[6] != 0x12:
        return None
    addr = (msg[7] << 24) | (msg[8] << 16) | (msg[9] << 8) | msg[10]
    return addr, list(msg[11:-2])


class KatanaBLE:
    def __init__(self, addr: str = ADDR_DEFAULT) -> None:
        self.addr = addr
        self.dev = "/org/bluez/hci0/dev_" + addr.replace(":", "_")
        self.bus: MessageBus | None = None
        self.io_path: str | None = None
        self._buf = bytearray()
        self.sysex: list[bytes] = []

    async def _call(self, path, iface, member, sig="", body=None, dest="org.bluez"):
        assert self.bus
        msg = await self.bus.call(
            Message(
                destination=dest,
                path=path,
                interface=iface,
                member=member,
                signature=sig,
                body=body or [],
            )
        )
        if msg.message_type == MessageType.ERROR:
            raise RuntimeError(f"{member}: {msg.error_name} {msg.body}")
        return msg.body

    async def connect(self, *, force: bool = False) -> None:
        if force:
            await self.hard_reset_link()

        self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        self.io_path = None
        self.sysex.clear()
        self._buf = bytearray()

        await self._ensure_connected(retries=3)

        for _ in range(40):
            objs = await self._call(
                "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects"
            )
            for path, ifaces in objs[0].items():
                if not str(path).startswith(self.dev):
                    continue
                ch = ifaces.get("org.bluez.GattCharacteristic1")
                if not ch:
                    continue
                uuid = str(ch["UUID"].value).lower()
                if "6bf3" in uuid or "7772e5db" in uuid:
                    self.io_path = str(path)
                    break
            if self.io_path:
                break
            await asyncio.sleep(0.25)
        if not self.io_path:
            raise RuntimeError(
                "BLE-MIDI char not found. Amp in MIDI mode (nome KATANA 3 MIDI)? "
                "LED sólido? Celular desconectado?"
            )

        await self._call(
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "AddMatch",
            "s",
            [
                "type='signal',interface='org.freedesktop.DBus.Properties',"
                f"member='PropertiesChanged',path='{self.io_path}'"
            ],
            dest="org.freedesktop.DBus",
        )

        def handler(msg: Message) -> None:
            if msg.path != self.io_path or msg.member != "PropertiesChanged":
                return
            changed = msg.body[1]
            if "Value" not in changed:
                return
            raw = bytes(changed["Value"].value)

            def on_sysex(s: bytes) -> None:
                self.sysex.append(s)

            ble_unwrap_feed(self._buf, raw, on_sysex)

        self.bus.add_message_handler(handler)

        try:
            await self._call(self.io_path, "org.bluez.GattCharacteristic1", "StopNotify")
        except Exception:
            pass
        await asyncio.sleep(0.12)
        await self._call(self.io_path, "org.bluez.GattCharacteristic1", "StartNotify")
        await asyncio.sleep(0.35)

        try:
            await self.request(ADDR["com"], 0x10, timeout=3.0)
        except TimeoutError:
            if not force:
                await self.disconnect(drop_link=True)
                await asyncio.sleep(1.2)
                await self.connect(force=True)
                return
            raise TimeoutError(
                "Bluetooth ok, mas o amp não responde SysEx. "
                "Segure o BT-DUAL até piscar (MIDI), feche o app do celular e Conectar de novo."
            ) from None

    async def _is_connected(self) -> bool:
        try:
            props = await self._call(
                self.dev,
                "org.freedesktop.DBus.Properties",
                "Get",
                "ss",
                ["org.bluez.Device1", "Connected"],
            )
            return bool(props[0].value)
        except Exception:
            return False

    async def _ble_scan_pulse(self, seconds: float = 2.5) -> None:
        """Short LE discovery so BlueZ refreshes the peripheral advertisement."""
        adapter = "/org/bluez/hci0"
        try:
            await self._call(
                adapter,
                "org.bluez.Adapter1",
                "SetDiscoveryFilter",
                "a{sv}",
                [{"Transport": Variant("s", "le"), "DuplicateData": Variant("b", True)}],
            )
        except Exception:
            pass
        try:
            await self._call(adapter, "org.bluez.Adapter1", "StartDiscovery")
        except Exception:
            pass
        await asyncio.sleep(seconds)
        try:
            await self._call(adapter, "org.bluez.Adapter1", "StopDiscovery")
        except Exception:
            pass
        await asyncio.sleep(0.3)

    async def _ensure_connected(self, retries: int = 5) -> None:
        if await self._is_connected():
            return

        # Always stop discovery — active scan causes le-connection-abort-by-local
        try:
            await self._call("/org/bluez/hci0", "org.bluez.Adapter1", "StopDiscovery")
        except Exception:
            pass
        await asyncio.sleep(0.3)

        last_err: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                if attempt == 1:
                    # gentle: just Connect on already-bonded device
                    await self._call(self.dev, "org.bluez.Device1", "Connect")
                else:
                    try:
                        await self._call(self.dev, "org.bluez.Device1", "Disconnect")
                    except Exception:
                        pass
                    await asyncio.sleep(0.8 * attempt)
                    # brief scan only on later retries
                    await self._ble_scan_pulse(1.5)
                    try:
                        await self._call(
                            "/org/bluez/hci0", "org.bluez.Adapter1", "StopDiscovery"
                        )
                    except Exception:
                        pass
                    await asyncio.sleep(0.4)
                    await self._call(self.dev, "org.bluez.Device1", "Connect")

                for _ in range(30):
                    if await self._is_connected():
                        await asyncio.sleep(0.6)
                        return
                    await asyncio.sleep(0.15)
                last_err = RuntimeError("Connect returned but device stayed disconnected")
            except Exception as e:
                last_err = e
                msg = str(e).lower()
                if "already connected" in msg or "in progress" in msg:
                    await asyncio.sleep(1.0)
                    if await self._is_connected():
                        return
                    continue
                if any(
                    s in msg
                    for s in (
                        "abort-by-local",
                        "inprogress",
                        "le-connection",
                        "br-connection",
                        "busy",
                    )
                ):
                    await asyncio.sleep(1.2 * attempt)
                    continue
                await asyncio.sleep(0.8 * attempt)

        raise RuntimeError(
            f"falha ao conectar no BT-DUAL após {retries} tentativas: {last_err}. "
            "Confira: luz piscando/sólida em MIDI, app do celular fechado, amp ligado."
        ) from last_err

    async def hard_reset_link(self) -> None:
        """Drop BlueZ ACL link so the next Connect is fresh."""
        try:
            if not self.bus:
                self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            try:
                await self._call(self.dev, "org.bluez.Device1", "Disconnect")
            except Exception:
                pass
            await asyncio.sleep(1.5)
            try:
                await self._call("/org/bluez/hci0", "org.bluez.Adapter1", "StopDiscovery")
            except Exception:
                pass
        except Exception:
            pass
        self.io_path = None
        self.sysex.clear()
        self._buf = bytearray()

    async def disconnect(self, *, drop_link: bool = False) -> None:
        if self.io_path and self.bus:
            try:
                await self._call(
                    self.io_path, "org.bluez.GattCharacteristic1", "StopNotify"
                )
            except Exception:
                pass
        if drop_link and self.bus:
            try:
                await self._call(self.dev, "org.bluez.Device1", "Disconnect")
            except Exception:
                pass
            await asyncio.sleep(0.8)
        self.io_path = None

    async def send_sysex(self, msg: bytes, *, gap: float = 0.012) -> None:
        assert self.io_path
        pkts = ble_wrap(msg)
        for i, pkt in enumerate(pkts):
            await self._call(
                self.io_path,
                "org.bluez.GattCharacteristic1",
                "WriteValue",
                "aya{sv}",
                [bytes(pkt), {"type": Variant("s", "command")}],
            )
            # only pace multi-packet SysEx; single-packet is fire-and-forget
            if gap > 0 and i + 1 < len(pkts):
                await asyncio.sleep(gap)

    async def request(self, addr: int, size: int, timeout: float = 2.5) -> list[int]:
        before = len(self.sysex)
        await self.send_sysex(rq1(addr, size), gap=0.008)
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            for s in self.sysex[before:]:
                p = parse_dt1(s)
                if p and p[0] == addr:
                    return p[1]
            await asyncio.sleep(0.015)
        raise TimeoutError(f"no DT1 for {addr:#010x}")

    async def write_bytes(
        self, addr: int, data: list[int], *, gap: float = 0.012, pace: float = 0.0
    ) -> None:
        """Write DT1. pace=extra delay after each chunk (0 for low-latency)."""
        off = 0
        while off < len(data):
            chunk = data[off : off + 40]
            await self.send_sysex(dt1(addr + off, chunk), gap=gap)
            if pace > 0:
                await asyncio.sleep(pace)
            off += len(chunk)

    async def set_pitch(self, semis: int, *, slots: int = 1) -> int:
        """Low-latency pitch change. slots=1 writes only MOD green (DET1)."""
        semis = max(-24, min(24, int(semis)))
        stored = (semis + 24) & 0x7F
        dets = (0x1C00, 0x1E00, 0x2000)[: max(1, min(3, slots))]
        for det in dets:
            # single-byte DT1 — one BLE packet
            await self.write_bytes(0x20000000 + det + PS_PITCH1, [stored], gap=0.0, pace=0.0)
        return semis

    async def arm_pitch_shifter(self, semis: int = -1) -> None:
        """One-shot setup: MOD on + pitch params. Call once per session."""
        semis = max(-24, min(24, int(semis)))
        stored = (semis + 24) & 0x7F
        # MOD on without full SW read when possible
        try:
            sw = await self.request(ADDR["sw"], 0x06, timeout=1.5)
            if sw[1] != 1:
                sw[1] = 1
                await self.write_bytes(ADDR["sw"], sw, pace=0.0)
        except Exception:
            await self.write_bytes(ADDR["sw"], [0, 1, 0, 1, 0, 1], pace=0.0)

        for rel in (0x1000, 0x1200, 0x1400):
            try:
                await self.write_bytes(0x20000000 + rel, [11], pace=0.0)
            except Exception:
                pass

        # Batch pitch block: voice, mode, pitch, fine at 0x48..0x4B
        # then level @0x50 and direct @0x5A
        for det in (0x1C00, 0x1E00, 0x2000):
            base = 0x20000000 + det
            await self.write_bytes(base + PS_VOICE, [0, 1, stored, 50], pace=0.0)
            await self.write_bytes(base + PS_PREDELAY1, enc_4x4(0), pace=0.0)
            await self.write_bytes(base + PS_LEVEL1, [100], pace=0.0)
            await self.write_bytes(base + PS_DIRECT_MIX, [0], pace=0.0)

    async def identity(self) -> bytes | None:
        before = len(self.sysex)
        await self.send_sysex(bytes([0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7]))
        await asyncio.sleep(1.2)
        for s in self.sysex[before:]:
            if len(s) > 5 and s[1] == 0x7E and s[3] == 0x06 and s[4] == 0x02:
                return s
        return None

    async def read_status_light(self) -> dict[str, Any]:
        """Fast status: name + amp + sw + pitch only (4 RQ1s)."""
        name_raw = await self.request(ADDR["com"], 0x10, timeout=2.0)
        name = bytes(b for b in name_raw if 32 <= b < 127).decode("ascii", "replace").rstrip()
        amp = await self.request(ADDR["amp"], 0x0A, timeout=2.0)
        sw = await self.request(ADDR["sw"], 0x06, timeout=2.0)
        pitch = 0
        try:
            d = await self.request(0x20001C48, 4, timeout=1.5)
            pitch = d[2] - 24
        except Exception:
            pass
        amp_d = {f: amp[i] for i, f in enumerate(AMP_FIELDS)}
        amp_d["type_name"] = (
            AMP_TYPE[amp_d["type"]] if amp_d["type"] < len(AMP_TYPE) else str(amp_d["type"])
        )
        return {
            "name": name,
            "amp": amp_d,
            "sw": {f: sw[i] for i, f in enumerate(SW_FIELDS)},
            "pitch": pitch,
        }

    async def read_patch(self) -> dict[str, Any]:
        name_raw = await self.request(ADDR["com"], 0x10)
        name = bytes(b for b in name_raw if 32 <= b < 127).decode("ascii", "replace").rstrip()
        amp = await self.request(ADDR["amp"], 0x0A)
        sw = await self.request(ADDR["sw"], 0x06)
        dly = await self.request(ADDR["delay"], 0x11)
        rev = await self.request(ADDR["reverb"], 0x0D)
        bst = await self.request(ADDR["booster"], 0x08)

        amp_d = {f: amp[i] for i, f in enumerate(AMP_FIELDS)}
        amp_d["type_name"] = AMP_TYPE[amp_d["type"]] if amp_d["type"] < len(AMP_TYPE) else str(amp_d["type"])

        delay_time = dec_4x4(dly[1:5])
        rev_pre = dec_4x4(rev[3:7])

        return {
            "name": name,
            "name_raw": name_raw,
            "amp": amp_d,
            "sw": {f: sw[i] for i, f in enumerate(SW_FIELDS)},
            "booster": {
                "type": bst[0],
                "drive": bst[1],
                "bottom": bst[2] - 50,
                "tone": bst[3] - 50,
                "solo_sw": bst[4],
                "solo_level": bst[5],
                "effect_level": bst[6],
                "direct_mix": bst[7],
                "raw": bst,
            },
            "delay": {
                "type": dly[0],
                "type_name": DELAY_TYPE[dly[0]] if dly[0] < len(DELAY_TYPE) else str(dly[0]),
                "time_ms": delay_time,
                "feedback": dly[5],
                "high_cut": dly[6],
                "effect_level": dly[7],
                "direct_level": dly[8],
                "raw": dly,
            },
            "reverb": {
                "type": rev[0],
                "type_name": REVERB_TYPE[rev[0]] if rev[0] < len(REVERB_TYPE) else str(rev[0]),
                "layer_mode": rev[1],
                "time": rev[2],
                "pre_delay_ms": rev_pre,
                "low_cut": rev[7],
                "high_cut": rev[8],
                "density": rev[9],
                "effect_level": rev[10],
                "direct_level": rev[11],
                "spring_color": rev[12] if len(rev) > 12 else 0,
                "raw": rev,
            },
        }

    async def apply_raw_blocks(
        self,
        raw_blocks: dict[str, list[int]],
        *,
        volume_cap: int | None = None,
        progress: bool = False,
    ) -> dict[str, int]:
        """Write a full .tsl-style paramSet (PATCH%… → bytes) into temp patch."""
        import sys
        from pathlib import Path

        tools = str(Path(__file__).resolve().parent / "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        from patch_map import ordered_blocks  # type: ignore

        blocks = ordered_blocks(raw_blocks)
        wrote = 0
        skipped = 0
        for name, addr, data in blocks:
            if not data:
                skipped += 1
                continue
            payload = list(data)
            # optional safety: don't blast face with Tone Exchange volumes
            if volume_cap is not None and name == "AMP" and len(payload) > 1:
                payload[1] = min(payload[1], int(volume_cap) & 0x7F)
            # After FX type bytes, brief settle so detail overlay can attach
            try:
                await self.write_bytes(addr, payload, gap=0.008, pace=0.0)
                wrote += 1
                if progress and wrote % 10 == 0:
                    print(f"  … {wrote}/{len(blocks)} blocks", flush=True)
                if name.startswith("FX(") and not name.startswith("FX_DETAIL"):
                    await asyncio.sleep(0.05)
                # pace large dumps so BlueZ/amp don't stall
                elif len(payload) > 64:
                    await asyncio.sleep(0.025)
                elif wrote % 5 == 0:
                    await asyncio.sleep(0.012)
            except Exception as e:
                skipped += 1
                if progress:
                    print(f"  skip {name}: {e}", flush=True)
        await asyncio.sleep(0.4)
        return {"wrote": wrote, "skipped": skipped, "total": len(blocks)}

    async def apply_preset(self, preset: dict[str, Any], *, volume_cap: int | None = 55) -> None:
        """Write a preset dict. If raw_blocks present (from .tsl), do full fidelity write."""
        raw = preset.get("raw_blocks")
        if isinstance(raw, dict) and raw:
            # Prefer explicit cap from preset amp if lower
            amp = preset.get("amp") or {}
            cap = volume_cap
            if "volume" in amp and volume_cap is not None:
                cap = min(int(amp["volume"]), int(volume_cap))
            elif "volume" in amp:
                cap = int(amp["volume"])
            await self.apply_raw_blocks(raw, volume_cap=cap, progress=False)
            return

        name = (preset.get("name") or "PRESET")[:16].ljust(16)
        await self.write_bytes(ADDR["com"], [ord(c) & 0x7F for c in name])

        amp = preset["amp"]
        amp_bytes = [int(amp[f]) & 0x7F for f in AMP_FIELDS]
        if volume_cap is not None:
            amp_bytes[1] = min(amp_bytes[1], int(volume_cap))
        await self.write_bytes(ADDR["amp"], amp_bytes)

        sw = preset.get("sw") or {}
        sw_bytes = [int(sw.get(f, 0)) & 0x7F for f in SW_FIELDS]
        await self.write_bytes(ADDR["sw"], sw_bytes)

        # chain / color if present in high-level form
        ch = preset.get("chain") or {}
        if "index" in ch:
            other = [
                int(ch.get("index", 2)) & 0x7F,
                int(ch.get("cabinet_resonance", 1)) & 0x7F,
                int(ch.get("master_key", 0)) & 0x7F,
            ]
            await self.write_bytes(0x20000200, other)
            if "pdl_pos" in ch:
                await self.write_bytes(0x20004800, [int(ch["pdl_pos"]) & 0x7F])
            if "sr_pos" in ch:
                # SENDRETURN byte1 = position
                try:
                    sr = await self.request(0x20005A00, 5, timeout=1.2)
                    sr[1] = int(ch["sr_pos"]) & 0x7F
                    await self.write_bytes(0x20005A00, sr)
                except Exception:
                    await self.write_bytes(0x20005A01, [int(ch["sr_pos"]) & 0x7F])
            if "eq1_pos" in ch:
                await self.write_bytes(0x20004C00, [int(ch["eq1_pos"]) & 0x7F])
            if "eq2_pos" in ch:
                await self.write_bytes(0x20004E00, [int(ch["eq2_pos"]) & 0x7F])

        col = preset.get("color") or {}
        if "raw" in col and isinstance(col["raw"], list):
            await self.write_bytes(0x20000400, [int(x) & 0x7F for x in col["raw"][:5]])

        d = preset.get("delay") or {}
        if "raw" in d and len(d["raw"]) >= 9:
            dly = list(d["raw"])
        else:
            dly = [0] * 0x11
            dly[0] = int(d.get("type", 0))
            t = enc_4x4(int(d.get("time_ms", 400)))
            dly[1:5] = t
            dly[5] = int(d.get("feedback", 22))
            dly[6] = int(d.get("high_cut", 10))
            dly[7] = int(d.get("effect_level", 35))
            dly[8] = int(d.get("direct_level", 100))
            dly[9] = int(d.get("tap_time", 50))
            dly[10] = int(d.get("mod_rate", 40))
            dly[11] = int(d.get("mod_depth", 55))
            dly[12] = int(d.get("lpf", 1))
        await self.write_bytes(ADDR["delay"], dly)

        r = preset.get("reverb") or {}
        if "raw" in r and len(r["raw"]) >= 12:
            rev = list(r["raw"])
        else:
            rev = [0] * 0x0D
            rev[0] = int(r.get("type", 0))
            rev[1] = int(r.get("layer_mode", 2))
            rev[2] = int(r.get("time", 35))
            rev[3:7] = enc_4x4(int(r.get("pre_delay_ms", 20)))
            rev[7] = int(r.get("low_cut", 14))
            rev[8] = int(r.get("high_cut", 8))
            rev[9] = int(r.get("density", 8))
            rev[10] = int(r.get("effect_level", 40))
            rev[11] = int(r.get("direct_level", 100))
            rev[12] = int(r.get("spring_color", 100))
        await self.write_bytes(ADDR["reverb"], rev)

        b = preset.get("booster")
        if b:
            if "raw" in b and len(b["raw"]) >= 8:
                bst = list(b["raw"])
            else:
                bst = [0] * 8
                bst[0] = int(b.get("type", 0))
                bst[1] = int(b.get("drive", 50))
                bst[2] = int(b.get("bottom", 0)) + 50
                bst[3] = int(b.get("tone", 0)) + 50
                bst[4] = int(b.get("solo_sw", 0))
                bst[5] = int(b.get("solo_level", 50))
                bst[6] = int(b.get("effect_level", 50))
                bst[7] = int(b.get("direct_mix", 0))
            await self.write_bytes(ADDR["booster"], bst)

        fx = preset.get("fx")
        if fx:
            ftype = int(fx.get("type", FX_PITCH_SHIFTER))
            for rel in (0x1000, 0x1200, 0x1400):
                await self.write_bytes(0x20000000 + rel, [ftype & 0x7F])
            if ftype == FX_PITCH_SHIFTER or fx.get("pitch_semitones") is not None:
                pitch = max(-24, min(24, int(fx.get("pitch_semitones", -1))))
                mode = int(fx.get("mode", 1))
                level = int(fx.get("level", 100))
                direct = int(fx.get("direct_mix", 0))
                for det in (0x1C00, 0x1E00, 0x2000):
                    base = 0x20000000 + det
                    await self.write_bytes(base + PS_VOICE, [0, mode & 0x7F, (pitch + 24) & 0x7F, 50])
                    await self.write_bytes(base + PS_PREDELAY1, enc_4x4(0))
                    await self.write_bytes(base + PS_LEVEL1, [level & 0x7F])
                    await self.write_bytes(base + PS_DIRECT_MIX, [direct & 0x7F])
            await self.write_bytes(0x20000400 + 0x01, [0])

        await asyncio.sleep(0.2)


def print_patch(p: dict[str, Any]) -> None:
    a = p["amp"]
    print(f"name: {p['name']!r}")
    print(
        f"AMP  type={a['type_name']}({a['type']})  gain={a['gain']}  vol={a['volume']}  "
        f"bass={a['bass']} mid={a['middle']} treble={a['treble']} "
        f"presence={a['presence']} reso={a['resonance']}"
    )
    print(f"SW   {p['sw']}")
    if p.get("booster"):
        b = p["booster"]
        print(
            f"BST  type={b['type']} drive={b['drive']} bottom={b['bottom']} "
            f"tone={b['tone']} lvl={b['effect_level']}"
        )
    d = p["delay"]
    print(
        f"DLY  {d['type_name']}  {d['time_ms']}ms  fb={d['feedback']}  "
        f"lvl={d['effect_level']}  dir={d['direct_level']}"
    )
    r = p["reverb"]
    print(
        f"REV  {r['type_name']}  time={r['time']}  pre={r['pre_delay_ms']}ms  "
        f"lvl={r['effect_level']}  dir={r['direct_level']}"
    )


async def cmd_status(k: KatanaBLE) -> None:
    ident = await k.identity()
    print("identity:", ident.hex(" ") if ident else "(none)")
    p = await k.read_patch()
    print_patch(p)


async def cmd_dump(k: KatanaBLE) -> None:
    p = await k.read_patch()
    print(json.dumps(p, indent=2))


async def cmd_save(k: KatanaBLE, path: Path) -> None:
    p = await k.read_patch()
    path.parent.mkdir(parents=True, exist_ok=True)
    # drop bulky raw if we have structured fields; keep raw for fidelity
    path.write_text(json.dumps(p, indent=2) + "\n")
    print(f"saved {path}  ({p['name']!r})")


async def cmd_load(k: KatanaBLE, path: Path, *, volume_cap: int | None = 55) -> None:
    preset = json.loads(path.read_text())
    raw_n = len(preset.get("raw_blocks") or {})
    mode = f"full-tsl ({raw_n} blocks)" if raw_n else "structured"
    print(f"loading {path} -> {preset.get('name')!r}  [{mode}]  vol_cap={volume_cap}")
    await k.apply_preset(preset, volume_cap=volume_cap)
    p = await k.read_patch()
    print("readback:")
    print_patch(p)
    if preset.get("chain"):
        ch = preset["chain"]
        print(f"chain meta: {ch.get('label')} #{ch.get('index')}  order={' → '.join(ch.get('order') or [])}")


async def cmd_load_tsl(k: KatanaBLE, path: Path, *, index: int = 0, volume_cap: int | None = 45) -> None:
    import sys
    from pathlib import Path as P

    tools = str(P(__file__).resolve().parent / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    from tsl_import import decode_tone, iter_tones, load_tsl  # type: ignore

    tsl = load_tsl(path)
    tones = list(iter_tones(tsl))
    if not tones:
        raise SystemExit("no tones in tsl")
    i = max(0, min(len(tones) - 1, index))
    _, tone = tones[i]
    preset = decode_tone(tone, liveset_name=str(tsl.get("name") or ""))
    print(
        f"loading tsl {path.name} [{i}/{len(tones)}] -> {preset['name']!r}  "
        f"chain={preset['chain']['label']}  blocks={len(preset.get('raw_blocks') or {})}  vol_cap={volume_cap}"
    )
    stats = await k.apply_raw_blocks(preset["raw_blocks"], volume_cap=volume_cap, progress=True)
    print("write stats:", stats)
    print("chain order:", " → ".join(preset["chain"]["order"]))
    # readback is best-effort — bulk write can leave notify queue busy
    try:
        await asyncio.sleep(0.5)
        p = await k.read_status_light()
        print("readback:")
        print(f"  name: {p.get('name')!r}")
        amp = p.get("amp") or {}
        print(f"  amp: {amp.get('type_name')} gain={amp.get('gain')} vol={amp.get('volume')}")
        print(f"  sw: {p.get('sw')}")
    except Exception as e:
        print(f"readback skipped ({type(e).__name__}: {e}) — writes were sent; reconnect if needed")


async def cmd_set(k: KatanaBLE, field: str, value: int) -> None:
    if field.startswith("amp.") or field in AMP_FIELDS:
        key = field.split(".", 1)[-1]
        if key not in AMP_FIELDS:
            raise SystemExit(f"amp fields: {AMP_FIELDS}")
        off = AMP_FIELDS.index(key)
        await k.write_bytes(ADDR["amp"] + off, [value & 0x7F])
        await asyncio.sleep(0.25)
        amp = await k.request(ADDR["amp"], 0x0A)
        print(f"set amp.{key}={value} -> {amp[off]}")
        return
    if field.startswith("sw.") or field in SW_FIELDS:
        key = field.split(".", 1)[-1]
        off = SW_FIELDS.index(key)
        await k.write_bytes(ADDR["sw"] + off, [value & 0x7F])
        await asyncio.sleep(0.25)
        sw = await k.request(ADDR["sw"], 0x06)
        print(f"set sw.{key}={value} -> {sw[off]}")
        return
    raise SystemExit("use amp.<field> or sw.<field>")


async def cmd_get(k: KatanaBLE, field: str) -> None:
    p = await k.read_patch()
    if field == "name":
        print(p["name"])
        return
    if field.startswith("amp.") or field in AMP_FIELDS:
        key = field.split(".", 1)[-1]
        print(p["amp"][key])
        return
    raise SystemExit("unknown field")


async def main() -> None:
    ap = argparse.ArgumentParser(description="Katana Gen 3 BLE control")
    ap.add_argument("--addr", default=ADDR_DEFAULT)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("dump")
    p = sub.add_parser("save")
    p.add_argument("path")
    p = sub.add_parser("load")
    p.add_argument("path")
    p.add_argument("--volume-cap", type=int, default=55)
    p.add_argument("--no-volume-cap", action="store_true")
    p = sub.add_parser("load-tsl", help="Load Tone Exchange .tsl with full paramSet")
    p.add_argument("path")
    p.add_argument("--index", type=int, default=0)
    p.add_argument("--volume-cap", type=int, default=45)
    p.add_argument("--no-volume-cap", action="store_true")
    p = sub.add_parser("get")
    p.add_argument("field")
    p = sub.add_parser("set")
    p.add_argument("field")
    p.add_argument("value", type=int)
    args = ap.parse_args()

    k = KatanaBLE(args.addr)
    await k.connect()
    print(f"connected io={k.io_path}", flush=True)
    try:
        if args.cmd == "status":
            await cmd_status(k)
        elif args.cmd == "dump":
            await cmd_dump(k)
        elif args.cmd == "save":
            await cmd_save(k, Path(args.path))
        elif args.cmd == "load":
            cap = None if args.no_volume_cap else args.volume_cap
            await cmd_load(k, Path(args.path), volume_cap=cap)
        elif args.cmd == "load-tsl":
            cap = None if args.no_volume_cap else args.volume_cap
            await cmd_load_tsl(k, Path(args.path), index=args.index, volume_cap=cap)
        elif args.cmd == "get":
            await cmd_get(k, args.field)
        elif args.cmd == "set":
            await cmd_set(k, args.field, args.value)
    finally:
        await k.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
