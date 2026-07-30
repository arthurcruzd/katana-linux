# katana-linux

Control a **BOSS Katana Gen 3** (with **BT-DUAL**) from Linux over Bluetooth LE MIDI.

Tested on Fedora 44 + BlueZ 5.87 + Katana-50 Gen 3 + BT-DUAL.

## What works

- Pair/bond once with BlueZ
- Read identity, patch name, AMP block (gain/eq/type/…)
- Write parameters (DT1) with readback
- Protocol taken from the official BOSS Tone Studio for Katana Gen 3 (JS sources)

## One-time MIDI pairing

Important: the physical BT-DUAL pairing button controls **Bluetooth Audio**.
Holding it until the blue indicator flashes rapidly advertises `KATANA 3 Audio`,
not MIDI. The blue indicator is therefore not proof of a BLE-MIDI session.

For MIDI, power-cycle the amp and do **not** press the BT-DUAL button:

```bash
bluetoothctl
# in bluetoothctl:
agent on
default-agent
scan le
# wait until you see exactly "KATANA 3 MIDI"; never pair "KATANA 3 Audio"
pair E7:47:8F:03:0D:C4
trust E7:47:8F:03:0D:C4
connect E7:47:8F:03:0D:C4
quit
```

The application starts GATT notifications itself. Connection is considered usable
after the BlueZ link and BLE-MIDI notify are active; SysEx readback is diagnostic
because writes can remain functional when a DT1 read response is delayed.

## Setup

```bash
cd ~/Documents/CODE/katana-linux
python3 -m venv .venv
.venv/bin/pip install bleak dbus-fast
```

(`bleak` is only used for scanning helpers; live I/O goes through BlueZ D-Bus because bleak's AcquireNotify is refused by the BT-DUAL.)

## Full .tsl load (chain + all blocks)

```bash
# Direct from Tone Exchange file (safe volume cap default 45)
.venv/bin/python katana_ble.py load-tsl samples/tsl/SRV\ Blues.tsl --volume-cap 40

# Or import then load via UI / load command
.venv/bin/python tools/tsl_import.py file.tsl --write-preset
.venv/bin/python katana_ble.py load presets/tsl-srv-blues.json --volume-cap 40
```

Full loads write ~78 DT1 blocks (types, details, EQ, assigns, chain). Use `--no-volume-cap` only if you trust the patch volume. After a bulk load, if status hangs, disconnect/reconnect BT once.

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

## Live mode (atomic preset switching)

Direct `.tsl` loads can write around 80 blocks and sound progressive while the
DSP updates. In the UI, filter/search down to 1–10 presets and click **Preparar
Live**. This overwrites the corresponding internal Katana user slots, waits for
the `PATCH_WRITE` acknowledgement, verifies the readable COM/AMP/SW blocks, and
saves a local hash manifest in `.katana-live.json`.

Prepared cards show `LIVE · N`. Their normal click/arrow load path sends one
`PATCH_SELECT` command instead of rebuilding the temporary patch. Editing a
preset invalidates its prepared hash; run **Preparar Live** again. Unprepared or
stale presets automatically fall back to the regular loader. Preparation uses a
volume cap of 50 and the UI reports that effective volume.

## UI stress tests

The browser test launches headless Chrome, rapidly switches presets and adjusts
volume, pitch, gain, middle and presence. It snapshots/restores `presets/*.json`,
so autosave does not alter the preset library during the test.

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python tools/browser_stress.py

# State-machine regressions (no amp required)
.venv/bin/python tools/test_state_machine.py

# Longer API-level normal-use simulation (non-destructive by default)
.venv/bin/python tools/sim_usage.py --skip-connect
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
