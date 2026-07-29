#!/usr/bin/env python3
"""Katana Gen 3 over BLE-MIDI, talking raw D-Bus to BlueZ.

bleak's start_notify uses AcquireNotify (a socket handshake) which the BT-DUAL
adapter refuses; plain org.bluez.GattCharacteristic1.StartNotify works, so we
drive BlueZ directly via dbus-fast.
"""
import asyncio
import sys

from dbus_fast import BusType, Message, MessageType
from dbus_fast.aio import MessageBus

ADDR = "E7:47:8F:03:0D:C4"
DEV_PATH = "/org/bluez/hci0/dev_" + ADDR.replace(":", "_")

DEVICE_ID = 0x10
MODEL_ID = [0x00, 0x00, 0x00, 0x01, 0x05, 0x07]
MIDI_SVC = "03b80e5a-ede8-4b33-a751-6ce34ec4c700"
IO_UUID_PREFIX = "00006bf3"


def nibble(x: int) -> int:
    return (
        ((x & 0x7F000000) >> 3)
        | ((x & 0x007F0000) >> 2)
        | ((x & 0x00007F00) >> 1)
        | (x & 0x0000007F)
    )


def addr4(x: int) -> list[int]:
    n = nibble(x)
    return [(n >> 24) & 0x7F, (n >> 16) & 0x7F, (n >> 8) & 0x7F, n & 0x7F]


def cks(b: list[int]) -> int:
    return (128 - (sum(b) % 128)) & 0x7F


def rq1(addr: int, size: int) -> bytes:
    body = addr4(addr) + addr4(size)
    return bytes([0xF0, 0x41, DEVICE_ID] + MODEL_ID + [0x11] + body + [cks(body), 0xF7])


def dt1(addr: int, data: list[int]) -> bytes:
    body = addr4(addr) + data
    return bytes([0xF0, 0x41, DEVICE_ID] + MODEL_ID + [0x12] + body + [cks(body), 0xF7])


def wrap(sysex: bytes, mtu: int = 20) -> list[bytes]:
    pkts, data, chunk, first = [], list(sysex), max(mtu - 3, 16), True
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
        pkts.append(bytes(pkt))
    return pkts


class Katana:
    def __init__(self) -> None:
        self.bus: MessageBus | None = None
        self.io_path: str | None = None
        self.sysex: list[bytes] = []
        self._buf = bytearray()

    async def _call(self, path, iface, member, sig="", body=None, dest="org.bluez"):
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
            raise RuntimeError(f"{member}: {msg.error_name}: {msg.body}")
        return msg.body

    async def connect(self) -> None:
        self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        for attempt in range(6):
            try:
                props = await self._call(
                    DEV_PATH,
                    "org.freedesktop.DBus.Properties",
                    "Get",
                    "ss",
                    ["org.bluez.Device1", "Connected"],
                )
                if props[0].value:
                    print("  (already connected)")
                    break
            except RuntimeError:
                pass
            try:
                await self._call(DEV_PATH, "org.bluez.Device1", "Connect")
                break
            except RuntimeError as e:
                if "AlreadyConnected" in str(e):
                    break
                print(f"  connect attempt {attempt + 1} failed: {e}")
                await asyncio.sleep(3)
        else:
            raise RuntimeError("could not connect after 6 attempts")
        for _ in range(40):
            objs = await self._call(
                "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects"
            )
            for path, ifaces in objs[0].items():
                ch = ifaces.get("org.bluez.GattCharacteristic1")
                if ch and path.startswith(DEV_PATH):
                    if str(ch["UUID"].value).startswith(IO_UUID_PREFIX):
                        self.io_path = path
            if self.io_path:
                return
            await asyncio.sleep(0.5)
        raise RuntimeError("MIDI characteristic not found")

    def _feed(self, pkt: bytes) -> None:
        for b in pkt[1:]:
            if b == 0xF0:
                self._buf = bytearray([b])
            elif b == 0xF7:
                if self._buf:
                    self._buf.append(b)
                    self.sysex.append(bytes(self._buf))
                    self._buf = bytearray()
            elif b & 0x80:
                continue
            elif self._buf:
                self._buf.append(b)

    async def start_notify(self) -> None:
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
            if msg.member != "PropertiesChanged" or msg.path != self.io_path:
                return
            changed = msg.body[1]
            if "Value" in changed:
                self._feed(bytes(changed["Value"].value))

        self.bus.add_message_handler(handler)
        print("  calling StartNotify...", flush=True)
        await asyncio.wait_for(
            self._call(self.io_path, "org.bluez.GattCharacteristic1", "StartNotify"),
            timeout=20,
        )
        print("  StartNotify returned", flush=True)

    async def send(self, sysex: bytes) -> None:
        for pkt in wrap(sysex):
            await self._call(
                self.io_path,
                "org.bluez.GattCharacteristic1",
                "WriteValue",
                "aya{sv}",
                [list(pkt), {}],
            )
            await asyncio.sleep(0.02)

    async def request(self, addr: int, size: int, wait: float = 2.0) -> list[bytes]:
        before = len(self.sysex)
        await self.send(rq1(addr, size))
        await asyncio.sleep(wait)
        return self.sysex[before:]

    async def disconnect(self) -> None:
        try:
            await self._call(self.io_path, "org.bluez.GattCharacteristic1", "StopNotify")
        except Exception:  # noqa: BLE001
            pass


def payload_of(msg: bytes) -> list[int]:
    return list(msg[14:-2]) if len(msg) > 16 else []


async def main() -> None:
    k = Katana()
    await k.connect()
    print(f"io char: {k.io_path}")
    await k.start_notify()
    print("notify ON")
    await asyncio.sleep(0.5)

    n = len(k.sysex)
    await k.send(bytes([0xF0, 0x7E, 0x10, 0x06, 0x01, 0xF7]))
    await asyncio.sleep(2)
    for m in k.sysex[n:]:
        print(f"  <- identity: {m.hex(' ')}")

    for label, addr, size in [
        ("PATCH temp AMP", 0x20000600, 0x0000000A),
        ("PATCH temp COM", 0x20000000, 0x00000010),
        ("SETUP patch#", 0x00000000, 0x00000002),
        ("PATCH(1) AMP", 0x20100600, 0x0000000A),
    ]:
        got = await k.request(addr, size)
        if got:
            for m in got:
                print(f"  <- {label}: {payload_of(m)}")
        else:
            print(f"  (no reply) {label}")

    await k.disconnect()
    print(f"\ntotal sysex received: {len(k.sysex)}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:  # noqa: BLE001
        print(f"FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
