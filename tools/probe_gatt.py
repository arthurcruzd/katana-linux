#!/usr/bin/env python3
"""Probe GATT services of the Katana BT-DUAL adapter."""
import asyncio
import sys

from bleak import BleakClient, BleakScanner

ADDR = sys.argv[1] if len(sys.argv) > 1 else "E7:47:8F:03:0D:C4"


async def main() -> None:
    print(f"scanning for {ADDR} ...")
    dev = await BleakScanner.find_device_by_address(ADDR, timeout=20.0)
    if dev is None:
        print("NOT FOUND — amp on? BT-DUAL seated? not connected to phone?")
        return
    print(f"found: {dev.name} [{dev.address}]")
    async with BleakClient(dev) as client:
        print(f"connected={client.is_connected}  mtu={client.mtu_size}")
        for svc in client.services:
            print(f"\n[service] {svc.uuid}  {svc.description}")
            for ch in svc.characteristics:
                print(f"  [char] {ch.uuid}  props={','.join(ch.properties)}")
                for d in ch.descriptors:
                    print(f"    [desc] {d.uuid}")


asyncio.run(main())
