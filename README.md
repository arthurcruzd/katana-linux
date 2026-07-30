# katana-linux

Control a **BOSS Katana Gen 3** (with **BT-DUAL**) from Linux over Bluetooth LE MIDI.

Tested on Fedora 44 + BlueZ 5.87 + Katana-50 Gen 3 + BT-DUAL.

## What works

- Pair/bond once with BlueZ
- Read identity, patch name, AMP block (gain/eq/type/…)
- Write parameters (DT1) with readback
- Protocol taken from the official BOSS Tone Studio for Katana Gen 3 (JS sources)

## One-time pairing

```bash
# Amp on, hold BT-DUAL button until LED blinks fast (MIDI mode, not Audio)
bluetoothctl
# in bluetoothctl:
agent on
default-agent
scan on
# wait until you see "KATANA 3 MIDI"
connect E7:47:8F:03:0D:C4
# when asked Accept pairing -> yes
# then:
menu gatt
select-attribute /org/bluez/hci0/dev_E7_47_8F_03_0D_C4/service0007/char000b
notify on
# should say Notify started / Notifying: yes
trust E7:47:8F:03:0D:C4
quit
```

Solid blue LED = connected. Name must be **KATANA 3 MIDI** (not "KATANA 3 Audio").

## Setup

```bash
cd ~/Documents/CODE/katana-linux
python3 -m venv .venv
.venv/bin/pip install bleak dbus-fast
```

(`bleak` is only used for scanning helpers; live I/O goes through BlueZ D-Bus because bleak's AcquireNotify is refused by the BT-DUAL.)

## Web UI (pitch slider)

```bash
cd ~/Documents/CODE/katana-linux
.venv/bin/python ui_server.py
# open http://127.0.0.1:8765
```

Requires BT-DUAL already bonded and in **KATANA 3 MIDI** mode. Pitch shifter type must have been set once (phone BTS → MOD → Pitch Shifter); the slider then changes semitones live.

```bash
.venv/bin/python katana_ble.py status
.venv/bin/python katana_ble.py get gain
.venv/bin/python katana_ble.py set presence 50
.venv/bin/python katana_ble.py set gain 80
```

## Protocol (Katana Gen 3)

```
F0 41 <deviceId=10> 01 05 07 <11=RQ1 | 12=DT1> <addr 4 bytes> <data|size> <checksum> F7
checksum = (128 - sum(addr+payload) % 128) & 0x7F
```

Useful temp-patch addresses (logical / on-wire):

| Block | Address    |
|-------|------------|
| COM (name) | `0x20000000` |
| AMP        | `0x20000600` |
| SW         | `0x20000800` |
| SETUP patch# | `0x00000000` |

AMP layout (1 byte each): gain, volume, bass, middle, treble, presence, poweramp_variation, type, resonance, preamp_variation.

Full 888-parameter map extracted from Tone Studio lives under `~/katana3/extracted/` (not necessarily in this repo).

## Pitfalls

- BT-DUAL toggles **MIDI** vs **Audio** roles; only MIDI exposes the BLE-MIDI service `03b80e5a-…`.
- Notify requires bonding. First `notify on` triggers pairing authorization.
- BlueZ drops the device object when advertising stops; reconnect after power-cycle may need a short `scan on`.
- Phone BTS app steals the single BLE link — close it while using Linux.
- Official app ships Windows/macOS only; this project is the Linux path.

## Layout

```
katana_ble.py       # main CLI client
tools/              # probes used while reverse-engineering
.venv/              # local virtualenv (not committed)
```

## License

Personal / experimental. Protocol constants © Roland/BOSS; this driver is independent.
