#!/usr/bin/env python3
"""Full BLE-MIDI + Roland SysEx handshake with the Katana Gen 3 (BT-DUAL)."""
import asyncio

from bleak import BleakClient, BleakScanner

ADDR = "E7:47:8F:03:0D:C4"
MIDI_SVC = "03b80e5a-ede8-4b33-a751-6ce34ec4c700"
IO_PREFIX = "00006bf3"

DEVICE_ID = 0x10
MODEL_ID = [0x00, 0x00, 0x00, 0x01, 0x05, 0x07]

rx_raw: list[bytes] = []
rx_sysex: list[bytes] = []
_buf = bytearray()


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


def wrap(sysex: bytes, mtu: int) -> list[bytes]:
    """BLE-MIDI framing: 0x80 header, 0x80 timestamp before first status and F7."""
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


def unwrap(pkt: bytes) -> None:
    """Strip BLE-MIDI header/timestamp bytes and reassemble SysEx."""
    global _buf
    for b in pkt[1:]:
        if b == 0xF0:
            _buf = bytearray([b])
        elif b == 0xF7:
            if _buf:
                _buf.append(b)
                rx_sysex.append(bytes(_buf))
                _buf = bytearray()
        elif b & 0x80:
            continue  # timestamp byte
        elif _buf:
            _buf.append(b)


def on_notify(_s, data: bytearray) -> None:
    rx_raw.append(bytes(data))
    unwrap(bytes(data))


def decode(msg: bytes) -> str:
    if len(msg) > 12 and msg[1] == 0x41 and msg[9] == 0x12:
        payload = msg[14:-2]
        return f"DT1 data={list(payload)}"
    return msg.hex(" ")


async def main() -> None:
    dev = await BleakScanner.find_device_by_address(ADDR, timeout=25.0)
    if dev is None:
        print("amp not advertising")
        return
    print(f"found {dev.name}")
    async with BleakClient(dev, timeout=30.0) as c:
        print(f"connected mtu={c.mtu_size}")
        svc = c.services.get_service(MIDI_SVC)
        io = next(x for x in svc.characteristics if x.uuid.startswith(IO_PREFIX))
        await asyncio.wait_for(c.start_notify(io, on_notify), timeout=45)
        print("notify ON")
        await asyncio.sleep(1.0)

        async def send(msg: bytes, label: str, wait: float = 2.5) -> None:
            before = len(rx_sysex)
            for p in wrap(msg, c.mtu_size):
                await c.write_gatt_char(io, p, response=False)
                await asyncio.sleep(0.03)
            await asyncio.sleep(wait)
            for m in rx_sysex[before:]:
                print(f"  <- [{label}] {decode(m)}")
            if len(rx_sysex) == before:
                print(f"  (no reply for {label})")

        await send(bytes([0xF0, 0x7E, 0x10, 0x06, 0x01, 0xF7]), "identity")
        await send(rq1(0x20000600, 0x0000000A), "PATCH temp AMP")
        await send(rq1(0x60000600, 0x0000000A), "0x60.. AMP guess")
        await send(rq1(0x00000000, 0x00000002), "SETUP patch#")

        await c.stop_notify(io)
    print(f"\nraw packets: {len(rx_raw)}  sysex msgs: {len(rx_sysex)}")


asyncio.run(main())
