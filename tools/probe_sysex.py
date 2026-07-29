#!/usr/bin/env python3
"""Live protocol test: BLE MIDI -> Roland SysEx RQ1 against a Katana Gen 3.

Sends an RQ1 for TEMP patch AMP block and prints whatever comes back.
"""
import asyncio
import sys

from bleak import BleakClient, BleakScanner

ADDR = "E7:47:8F:03:0D:C4"
MIDI_SVC = "03b80e5a-ede8-4b33-a751-6ce34ec4c700"

MODEL_ID = [0x00, 0x00, 0x00, 0x01, 0x05, 0x07]  # padded per BTS: '010507'
DEVICE_ID = 0x10


def nibble(x: int) -> int:
    """Roland 'nibbled' address encoding used by BTS address_map.js."""
    return (
        ((x & 0x7F000000) >> 3)
        | ((x & 0x007F0000) >> 2)
        | ((x & 0x00007F00) >> 1)
        | (x & 0x0000007F)
    )


def addr_bytes(addr: int) -> list[int]:
    n = nibble(addr)
    return [(n >> 24) & 0x7F, (n >> 16) & 0x7F, (n >> 8) & 0x7F, n & 0x7F]


def checksum(payload: list[int]) -> int:
    return (128 - (sum(payload) % 128)) & 0x7F


def rq1(addr: int, size: int) -> bytes:
    body = addr_bytes(addr) + addr_bytes(size)
    msg = [0xF0, 0x41, DEVICE_ID] + MODEL_ID + [0x11] + body + [checksum(body), 0xF7]
    return bytes(msg)


def dt1(addr: int, data: list[int]) -> bytes:
    body = addr_bytes(addr) + data
    msg = [0xF0, 0x41, DEVICE_ID] + MODEL_ID + [0x12] + body + [checksum(body), 0xF7]
    return bytes(msg)


def ble_wrap(sysex: bytes, mtu: int = 20) -> list[bytes]:
    """Wrap a SysEx message in BLE-MIDI packets (header/timestamp framing)."""
    header = 0x80
    ts = 0x80
    out: list[bytes] = []
    payload = list(sysex)
    chunk = mtu - 2
    first = True
    while payload:
        part, payload = payload[:chunk], payload[chunk:]
        pkt = [header]
        if first:
            pkt.append(ts)
            first = False
        pkt.extend(part)
        if not payload:
            # timestamp byte must precede the terminating F7
            if pkt[-1] == 0xF7:
                pkt = pkt[:-1] + [ts, 0xF7]
        out.append(bytes(pkt))
    return out


received: list[bytes] = []


def on_notify(_sender, data: bytearray) -> None:
    received.append(bytes(data))
    print(f"  <- {data.hex(' ')}")


async def main() -> None:
    dev = await BleakScanner.find_device_by_address(ADDR, timeout=20.0)
    if dev is None:
        print("amp not found")
        sys.exit(1)
    async with BleakClient(dev) as client:
        svc = client.services.get_service(MIDI_SVC)
        io_char = None
        for ch in svc.characteristics:
            if "notify" in ch.properties and "write-without-response" in ch.properties:
                io_char = ch
        if io_char is None:
            for ch in svc.characteristics:
                if "notify" in ch.properties:
                    io_char = ch
        print(f"io char: {io_char.uuid} props={io_char.properties}")
        await client.start_notify(io_char, on_notify)
        await asyncio.sleep(0.5)

        # 1) universal identity request
        ident = bytes([0xF0, 0x7E, 0x10, 0x06, 0x01, 0xF7])
        print(f"-> identity request {ident.hex(' ')}")
        for pkt in ble_wrap(ident, client.mtu_size):
            await client.write_gatt_char(io_char, pkt, response=False)
        await asyncio.sleep(1.5)

        # 2) RQ1: TEMP patch AMP block (0x30000000 base? try PATCH temp 0x20000600)
        for label, addr, size in [
            ("PATCH(temp) AMP", 0x20000600, 0x0000000A),
            ("TEMP TEMP_COM", 0x30000000, 0x00000010),
        ]:
            msg = rq1(addr, size)
            print(f"-> RQ1 {label} @{addr:08X}: {msg.hex(' ')}")
            for pkt in ble_wrap(msg, client.mtu_size):
                await client.write_gatt_char(io_char, pkt, response=False)
            await asyncio.sleep(2.0)

        await client.stop_notify(io_char)
    print(f"\ntotal notifications: {len(received)}")


asyncio.run(main())
