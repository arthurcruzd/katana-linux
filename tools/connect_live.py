#!/usr/bin/env python3
"""Robust connect: scan to repopulate the BlueZ object, then talk SysEx.

BlueZ drops the device object when it stops advertising ("UnknownObject" on
Connect), and a fresh LE scan is what brings it back. So each attempt scans
first, then connects via raw D-Bus (bleak's AcquireNotify does not work with
the BT-DUAL; plain StartNotify does).
"""
import asyncio
import sys

sys.path.insert(0, "/home/arthurd/Documents/CODE/katana-linux/tools")

from bleak import BleakScanner  # noqa: E402
from dbus_probe import Katana, payload_of  # noqa: E402

ADDR = "E7:47:8F:03:0D:C4"


async def attempt() -> bool:
    dev = await BleakScanner.find_device_by_address(ADDR, timeout=15.0)
    print(f"  scan: {dev.name if dev else 'not advertising'}", flush=True)
    k = Katana()
    await k.connect()
    print(f"  connected, io={k.io_path}", flush=True)
    await k.start_notify()
    await asyncio.sleep(0.5)

    n = len(k.sysex)
    await k.send(bytes([0xF0, 0x7E, 0x10, 0x06, 0x01, 0xF7]))
    await asyncio.sleep(3)
    for m in k.sysex[n:]:
        print(f"  <- IDENTITY: {m.hex(' ')}", flush=True)

    labels = [
        ("AMP (temp patch)", 0x20000600, 0x0A),
        ("COM (temp patch)", 0x20000000, 0x10),
        ("SETUP patch#", 0x00000000, 0x02),
        ("PATCH1 AMP", 0x20100600, 0x0A),
    ]
    hits = 0
    for label, addr, size in labels:
        got = await k.request(addr, size, 3.0)
        if got:
            hits += 1
            for m in got:
                print(f"  <- {label}: {payload_of(m)}", flush=True)
        else:
            print(f"  -- {label}: no reply", flush=True)

    await k.disconnect()
    print(f"  total sysex: {len(k.sysex)}, block hits: {hits}", flush=True)
    return len(k.sysex) > 0


async def main() -> None:
    for i in range(1, 9):
        print(f"[attempt {i}]", flush=True)
        try:
            if await attempt():
                print("SUCCESS", flush=True)
                return
        except Exception as e:  # noqa: BLE001
            print(f"  fail: {type(e).__name__}: {e}", flush=True)
        await asyncio.sleep(3)
    print("GAVE UP", flush=True)


asyncio.run(main())
