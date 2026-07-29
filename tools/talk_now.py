#!/usr/bin/env python3
"""Talk to already-connected Katana BT-DUAL via BlueZ D-Bus (no bleak connect)."""
import asyncio
import sys

from dbus_fast import BusType, Message, MessageType, Variant
from dbus_fast.aio import MessageBus

ADDR = "E7:47:8F:03:0D:C4"
DEV = "/org/bluez/hci0/dev_" + ADDR.replace(":", "_")
DEVICE_ID = 0x10
MODEL = [0x00, 0x00, 0x00, 0x01, 0x05, 0x07]

sysex_msgs: list[bytes] = []
_buf = bytearray()
io_path: str | None = None


def nibble(x: int) -> int:
    return (
        ((x & 0x7F000000) >> 3)
        | ((x & 0x007F0000) >> 2)
        | ((x & 0x00007F00) >> 1)
        | (x & 0x0000007F)
    )


def a4(x: int) -> list[int]:
    n = nibble(x)
    return [(n >> 24) & 0x7F, (n >> 16) & 0x7F, (n >> 8) & 0x7F, n & 0x7F]


def ck(b: list[int]) -> int:
    return (128 - (sum(b) % 128)) & 0x7F


def make_rq1(addr: int, size: int) -> bytes:
    body = a4(addr) + a4(size)
    return bytes([0xF0, 0x41, DEVICE_ID] + MODEL + [0x11] + body + [ck(body), 0xF7])


def wrap(sysex: bytes) -> list[bytes]:
    """BLE-MIDI packetize (header 0x80 + timestamp 0x80)."""
    out, data, first = [], list(sysex), True
    while data:
        # leave room for header (+ optional ts) and possible ts before F7
        room = 18
        part, data = data[:room], data[room:]
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


def feed(pkt: bytes) -> None:
    global _buf
    # BLE-MIDI: skip header byte; 0x80-0xBF are timestamps when high bit set
    for b in pkt[1:]:
        if b == 0xF0:
            _buf = bytearray([0xF0])
        elif b == 0xF7:
            if _buf:
                _buf.append(0xF7)
                sysex_msgs.append(bytes(_buf))
                print(f"  SYSEX: {_buf.hex(' ')}", flush=True)
                _buf = bytearray()
        elif b & 0x80:
            continue  # timestamp
        elif _buf:
            _buf.append(b)


async def call(bus, path, iface, member, sig="", body=None, dest="org.bluez"):
    msg = await bus.call(
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


async def main() -> None:
    global io_path
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

    # confirm connected
    props = await call(
        bus, DEV, "org.freedesktop.DBus.Properties", "Get", "ss",
        ["org.bluez.Device1", "Connected"],
    )
    print(f"Connected prop: {props[0].value}", flush=True)

    # find MIDI IO char (ends with 6bf3)
    objs = await call(bus, "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects")
    for path, ifaces in objs[0].items():
        if not path.startswith(DEV):
            continue
        ch = ifaces.get("org.bluez.GattCharacteristic1")
        if not ch:
            continue
        uuid = str(ch["UUID"].value)
        print(f"  char {path.split('/')[-1]} uuid={uuid} props={ch.get('Flags', Variant('as', [])).value}", flush=True)
        if "6bf3" in uuid.lower() or uuid.lower().endswith("6bf3"):
            io_path = path
        # also accept full BLE-MIDI UUID
        if "7772e5db" in uuid.lower():
            io_path = path

    if not io_path:
        # fallback: any notify+write char under the MIDI service
        for path, ifaces in objs[0].items():
            ch = ifaces.get("org.bluez.GattCharacteristic1")
            if not ch or not path.startswith(DEV):
                continue
            flags = ch.get("Flags", Variant("as", [])).value
            if "notify" in flags and ("write" in flags or "write-without-response" in flags):
                if "service0007" in path or "03b80e5a" in str(ifaces):
                    io_path = path
                    break
        if not io_path:
            for path, ifaces in objs[0].items():
                ch = ifaces.get("org.bluez.GattCharacteristic1")
                if not ch or not path.startswith(DEV):
                    continue
                flags = ch.get("Flags", Variant("as", [])).value
                if "notify" in flags and "write-without-response" in flags:
                    io_path = path
                    break

    print(f"IO path: {io_path}", flush=True)
    if not io_path:
        print("NO IO CHAR", flush=True)
        return

    # match PropertiesChanged for Value
    await call(
        bus, "/org/freedesktop/DBus", "org.freedesktop.DBus", "AddMatch", "s",
        [f"type='signal',interface='org.freedesktop.DBus.Properties',"
         f"member='PropertiesChanged',path='{io_path}'"],
        dest="org.freedesktop.DBus",
    )

    def on_msg(msg: Message) -> None:
        if msg.path != io_path or msg.member != "PropertiesChanged":
            return
        changed = msg.body[1]
        if "Value" in changed:
            raw = bytes(changed["Value"].value)
            print(f"  RAW: {raw.hex(' ')}", flush=True)
            feed(raw)

    bus.add_message_handler(on_msg)

    print("StartNotify...", flush=True)
    await call(bus, io_path, "org.bluez.GattCharacteristic1", "StartNotify")
    print("notify ON", flush=True)
    await asyncio.sleep(0.8)

    async def send(label: str, data: bytes, wait: float = 2.5) -> None:
        before = len(sysex_msgs)
        print(f"-> {label}: {data.hex(' ')}", flush=True)
        for pkt in wrap(data):
            # write-without-response style options
            await call(
                bus, io_path, "org.bluez.GattCharacteristic1", "WriteValue",
                "aya{sv}",
                [list(pkt), {"type": Variant("s", "command")}],
            )
            await asyncio.sleep(0.04)
        await asyncio.sleep(wait)
        if len(sysex_msgs) == before:
            print(f"   (no sysex reply for {label})", flush=True)

    # Identity request (universal)
    await send("identity", bytes([0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7]), 2.0)
    await send("identity-dev10", bytes([0xF0, 0x7E, 0x10, 0x06, 0x01, 0xF7]), 2.0)

    # Roland RQ1s
    await send("RQ1 AMP temp", make_rq1(0x20000600, 0x0000000A), 3.0)
    await send("RQ1 SETUP", make_rq1(0x00000000, 0x00000002), 3.0)
    await send("RQ1 COM temp", make_rq1(0x20000000, 0x00000010), 3.0)

    try:
        await call(bus, io_path, "org.bluez.GattCharacteristic1", "StopNotify")
    except Exception:
        pass

    print(f"\nDone. sysex count={len(sysex_msgs)}", flush=True)


asyncio.run(main())
