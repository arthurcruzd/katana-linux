#!/usr/bin/env python3
"""Katana Gen 3 BLE-MIDI client (BlueZ D-Bus, bonded).

Requires the BT-DUAL already paired/bonded (once). Then:
  connect -> StartNotify -> RQ1/DT1 SysEx over BLE-MIDI framing.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Callable

from dbus_fast import BusType, Message, MessageType, Variant
from dbus_fast.aio import MessageBus

ADDR_DEFAULT = "E7:47:8F:03:0D:C4"
DEVICE_ID = 0x10
MODEL_ID = bytes([0x01, 0x05, 0x07])  # product_setting.js modelId '010507'

# temp-patch AMP block (logical addresses as on the wire after BTS nibble/_7bitize)
ADDR_SETUP_PATCH = 0x00000000
ADDR_PATCH_COM = 0x20000000
ADDR_PATCH_AMP = 0x20000600
ADDR_PATCH_SW = 0x20000800

AMP_FIELDS = [
    ("gain", 0),
    ("volume", 1),
    ("bass", 2),
    ("middle", 3),
    ("treble", 4),
    ("presence", 5),
    ("poweramp_variation", 6),
    ("type", 7),
    ("resonance", 8),
    ("preamp_variation", 9),
]


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


def ble_wrap(sysex: bytes, mtu: int = 20) -> list[bytes]:
    """BLE-MIDI framing: header 0x80, timestamp 0x80 before status and F7."""
    out: list[bytes] = []
    data = list(sysex)
    first = True
    # keep packets comfortably under ATT default
    chunk = max(mtu - 4, 12)
    while data:
        part, data = data[:chunk], data[chunk:]
        pkt = [0x80]
        if first:
            pkt.append(0x80)
            first = False
        if not data and part and part[-1] == 0xF7:
            pkt.extend(part[:-1])
            pkt.append(0x80)
            pkt.append(0xF7)
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
            continue  # timestamp
        elif state:
            state.append(b)


def parse_dt1(msg: bytes) -> tuple[int, list[int]] | None:
    """Return (addr, data) for Roland DT1, or None."""
    if len(msg) < 15 or msg[0] != 0xF0 or msg[1] != 0x41 or msg[-1] != 0xF7:
        return None
    # F0 41 dev model(3) 12 addr(4) data... ck F7
    if msg[2] not in (DEVICE_ID, 0x7F):
        return None
    if bytes(msg[3:6]) != MODEL_ID:
        return None
    if msg[6] != 0x12:
        return None
    addr = (msg[7] << 24) | (msg[8] << 16) | (msg[9] << 8) | msg[10]
    data = list(msg[11:-2])
    return addr, data


class KatanaBLE:
    def __init__(self, addr: str = ADDR_DEFAULT) -> None:
        self.addr = addr
        self.dev = "/org/bluez/hci0/dev_" + addr.replace(":", "_")
        self.bus: MessageBus | None = None
        self.io_path: str | None = None
        self._buf = bytearray()
        self.sysex: list[bytes] = []
        self._waiters: list[asyncio.Future] = []

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

    async def connect(self) -> None:
        self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        # connect if needed
        try:
            props = await self._call(
                self.dev,
                "org.freedesktop.DBus.Properties",
                "Get",
                "ss",
                ["org.bluez.Device1", "Connected"],
            )
            if not props[0].value:
                await self._call(self.dev, "org.bluez.Device1", "Connect")
                await asyncio.sleep(1.5)
        except RuntimeError as e:
            # try connect anyway
            try:
                await self._call(self.dev, "org.bluez.Device1", "Connect")
                await asyncio.sleep(1.5)
            except RuntimeError:
                raise e from None

        # resolve IO characteristic (...6bf3)
        for _ in range(30):
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
            await asyncio.sleep(0.3)
        if not self.io_path:
            raise RuntimeError("BLE-MIDI characteristic not found (is amp in MIDI mode?)")

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
                for fut in list(self._waiters):
                    if not fut.done():
                        fut.set_result(s)

            ble_unwrap_feed(self._buf, raw, on_sysex)

        self.bus.add_message_handler(handler)
        await self._call(self.io_path, "org.bluez.GattCharacteristic1", "StartNotify")

    async def disconnect(self) -> None:
        if self.io_path and self.bus:
            try:
                await self._call(
                    self.io_path, "org.bluez.GattCharacteristic1", "StopNotify"
                )
            except Exception:
                pass

    async def send_sysex(self, msg: bytes) -> None:
        assert self.io_path
        for pkt in ble_wrap(msg):
            await self._call(
                self.io_path,
                "org.bluez.GattCharacteristic1",
                "WriteValue",
                "aya{sv}",
                [bytes(pkt), {"type": Variant("s", "command")}],
            )
            await asyncio.sleep(0.03)

    async def request(self, addr: int, size: int, timeout: float = 3.0) -> list[int]:
        """RQ1 and return DT1 data bytes."""
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._waiters.append(fut)
        try:
            await self.send_sysex(rq1(addr, size))
            msg = await asyncio.wait_for(fut, timeout=timeout)
        finally:
            if fut in self._waiters:
                self._waiters.remove(fut)
        parsed = parse_dt1(msg)
        if not parsed:
            # may have gotten identity etc; wait for matching addr
            deadline = loop.time() + timeout
            while loop.time() < deadline:
                for s in reversed(self.sysex):
                    p = parse_dt1(s)
                    if p and p[0] == addr:
                        return p[1]
                await asyncio.sleep(0.1)
            raise TimeoutError(f"no DT1 for addr {addr:#010x}, last={msg.hex(' ')}")
        if parsed[0] != addr:
            # search buffer
            for s in reversed(self.sysex):
                p = parse_dt1(s)
                if p and p[0] == addr:
                    return p[1]
        return parsed[1]

    async def write(self, addr: int, data: list[int]) -> None:
        await self.send_sysex(dt1(addr, data))

    async def identity(self) -> bytes | None:
        before = len(self.sysex)
        await self.send_sysex(bytes([0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7]))
        await asyncio.sleep(1.5)
        for s in self.sysex[before:]:
            if len(s) > 5 and s[1] == 0x7E and s[3] == 0x06 and s[4] == 0x02:
                return s
        return None


async def cmd_status(k: KatanaBLE) -> None:
    ident = await k.identity()
    print("identity:", ident.hex(" ") if ident else "(none)")
    name_raw = await k.request(ADDR_PATCH_COM, 0x10)
    name = bytes(b for b in name_raw if 32 <= b < 127).decode("ascii", errors="replace").strip()
    print(f"patch name: {name!r}  raw={name_raw}")
    amp = await k.request(ADDR_PATCH_AMP, 0x0A)
    print("AMP:")
    for field, off in AMP_FIELDS:
        print(f"  {field:20} = {amp[off]}")


async def cmd_get(k: KatanaBLE, field: str) -> None:
    amp = await k.request(ADDR_PATCH_AMP, 0x0A)
    lookup = {n: i for n, i in AMP_FIELDS}
    if field not in lookup:
        raise SystemExit(f"unknown field {field}, choose from {list(lookup)}")
    print(f"{field} = {amp[lookup[field]]}")


async def cmd_set(k: KatanaBLE, field: str, value: int) -> None:
    lookup = {n: i for n, i in AMP_FIELDS}
    if field not in lookup:
        raise SystemExit(f"unknown field {field}, choose from {list(lookup)}")
    off = lookup[field]
    await k.write(ADDR_PATCH_AMP + off, [value & 0x7F])
    await asyncio.sleep(0.3)
    amp = await k.request(ADDR_PATCH_AMP, 0x0A)
    print(f"set {field}={value} -> readback {amp[off]}")


async def main() -> None:
    ap = argparse.ArgumentParser(description="Katana Gen 3 BLE control")
    ap.add_argument("--addr", default=ADDR_DEFAULT)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    g = sub.add_parser("get")
    g.add_argument("field")
    s = sub.add_parser("set")
    s.add_argument("field")
    s.add_argument("value", type=int)
    args = ap.parse_args()

    k = KatanaBLE(args.addr)
    await k.connect()
    print(f"connected io={k.io_path}", flush=True)
    try:
        if args.cmd == "status":
            await cmd_status(k)
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
