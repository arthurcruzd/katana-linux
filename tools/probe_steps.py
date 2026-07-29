#!/usr/bin/env python3
"""Instrumented BLE step test: which call hangs?"""
import asyncio

from bleak import BleakClient, BleakScanner

ADDR = "E7:47:8F:03:0D:C4"
MIDI_SVC = "03b80e5a-ede8-4b33-a751-6ce34ec4c700"


async def step(label, coro, t=15.0):
    print(f"[..] {label}", flush=True)
    try:
        r = await asyncio.wait_for(coro, timeout=t)
        print(f"[ok] {label} -> {r!r}", flush=True)
        return r
    except asyncio.TimeoutError:
        print(f"[TIMEOUT] {label}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[ERR] {label}: {type(e).__name__}: {e}", flush=True)
    return None


def on_notify(_s, data: bytearray) -> None:
    print(f"  <- {bytes(data).hex(' ')}", flush=True)


async def main() -> None:
    dev = await step("scan", BleakScanner.find_device_by_address(ADDR, timeout=20.0), 25)
    if dev is None:
        return
    client = BleakClient(dev)
    await step("connect", client.connect(), 25)
    print(f"connected={client.is_connected}", flush=True)
    svc = client.services.get_service(MIDI_SVC)
    io = next(c for c in svc.characteristics if "notify" in c.properties and "read" in c.properties)
    print(f"io={io.uuid} handle={io.handle}", flush=True)

    await step("read char", client.read_gatt_char(io), 10)
    await step("start_notify", client.start_notify(io, on_notify), 15)
    await step(
        "write identity (no response)",
        client.write_gatt_char(io, bytes([0x80, 0x80, 0xF0, 0x7E, 0x10, 0x06, 0x01, 0x80, 0xF7]), response=False),
        10,
    )
    await asyncio.sleep(3)
    await step("disconnect", client.disconnect(), 10)


asyncio.run(main())
