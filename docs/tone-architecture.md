# Katana Gen 3 — tone architecture (from BTS + Tone Exchange .tsl)

Sources:
- BOSS Tone Studio Gen 3 JS (`address_map.js`, `chain_config.js`, `item.json`)
- Tone Exchange livesets (`.tsl` = JSON)

## .tsl file format

```json
{
  "name": "liveset name",
  "formatRev": "0000" | "0002",
  "device": "KATANA Gen3",
  "data": [ [ { "memo": "", "paramSet": { "PATCH%…": ["hh", ...] } } ] ]
}
```

- `paramSet` keys match BTS parameter paths (`PATCH%AMP`, `PATCH%FX(1)`, …).
- Values are arrays of **hex strings** (`"55"`) = raw 7-bit MIDI bytes (same as DT1 payload).
- A full tone has **~80 blocks** (amp, 3 boosters, 6 FX types + 6×225 detail, delays, assigns…).

## Patch memory layout (temp edit buffer)

Base: `0x20000000` (live/temp PATCH). User banks are other bases; we usually write temp.

| Path | Relative | Size (typ.) | Role |
|------|----------|-------------|------|
| COM | +0x0000 | 16 | Patch name ASCII |
| OTHER | +0x0200 | 3 | chain, cab resonance, master key |
| COLOR | +0x0400 | 5 | variation color BST/MOD/FX/DLY/REV |
| AMP | +0x0600 | 10 | amp type + knobs |
| SW | +0x0800 | 6 | block/mod/fx/dly/dly2/rev switches |
| BOOSTER(1..3) | +0x0A00 / C00 / E00 | 8 each | 3 color variants |
| FX(1..6) | +0x1000 … 1A00 | **1 each** | effect **type** id |
| FX_DETAIL(1..6) | +0x1C00 … 2600 | 225 each | all params for that type |
| DELAY(1..6) | +0x2800… | 17 | delay variants |
| REVERB(1..3) | +0x3400… | 13 | reverb variants |
| NS, PEDALFX, EQ_*, ASSIGN_*, SOLO_*, SENDRETURN, CONTOUR, … | (see map) | | |

### PATCH%OTHER (3 bytes)

| ofs | name | range | meaning |
|-----|------|-------|---------|
| 0 | CHAIN | 0–8 | which base signal order (see below) |
| 1 | CABINET_RESONANCE | 0–2 | cab resonance mode |
| 2 | MASTER_KEY | 0–11 | global key (harmonist etc.) |

### PATCH%COLOR (5 bytes)

Indices = color of each multi-slot block: **0=green, 1=red, 2=yellow**.

| ofs | block |
|-----|--------|
| 0 | BOOSTER → BOOSTER(1+color) |
| 1 | MOD → FX(1+color) + FX_DETAIL(1+color) |
| 2 | FX panel → FX(4+color) + FX_DETAIL(4+color) |
| 3 | DELAY → DELAY(1+color) |
| 4 | REVERB → REVERB(1+color) |

### MOD vs FX panel

- **MOD** uses slots **FX(1..3)** / **FX_DETAIL(1..3)**
- **FX** uses slots **FX(4..6)** / **FX_DETAIL(4..6)**
- Active slot = base + COLOR for that group

### FX type IDs (PATCH%FX(n) single byte)

Ordered list from BTS resource strings:

| id | name |
|----|------|
| 0 | T.WAH |
| 1 | AUTO WAH |
| 2 | PEDAL WAH |
| 3 | COMP |
| 4 | LIMITER |
| 5 | GRAPHIC EQ |
| 6 | PARAMETRIC EQ |
| 7 | GUITAR SIM |
| 8 | SLOW GEAR |
| 9 | WAVE SYNTH |
| 10 | OCTAVE |
| 11 | **PITCH SHIFTER** |
| 12 | HARMONIST |
| 13 | AC.PROCESSOR |
| 14 | PHASER |
| 15 | FLANGER |
| 16 | TREMOLO |
| 17 | ROTARY |
| 18 | UNI-V |
| 19 | SLICER |
| 20 | VIBRATO |
| 21 | RING MOD |
| 22 | HUMANIZER |
| 23 | CHORUS (default) |
| 24 | AC.GUITAR SIM |
| 25 | PHASER 90E |
| 26 | FLANGER 117E |
| 27 | WAH 95E |
| 28 | DC-30 |
| 29 | HEAVY OCTAVE |
| 30 | PEDAL BEND |

Pitch shifter params live inside the matching **FX_DETAIL** (offsets known: voice@0x48, mode@0x49, pitch@0x4A ofs−24, fine@0x4B, …).

Note: some firmwares/BlueZ stacks refuse **RQ1** on the 1-byte FX(type) addresses; DT1 write and .tsl import still carry the value.

## Signal chain

Chain is **not** a free permutation of every block. It is:

1. **CHAIN** index 0–8 → row in `CHAIN_BASE_FLOW` (depends also on pedal FX position + send/return position)
2. **EQ1 / EQ2 positions** insert `eq` / `eq2` around amp via `CHAIN_EQ_FLOW`
3. Always bookended by `input` … `speaker`

### Positions that reshape the flow

| Control | PID | values |
|---------|-----|--------|
| Chain preset | `PATCH%OTHER%0` | 0–8 |
| Pedal FX pos | `PATCH%PEDALFX_COM%0` | 0 = front, 1 = post-amp region |
| S/R pos | `PATCH%SENDRETURN%1` | 0 / 1 / 2 |
| EQ1 pos | `PATCH%EQ_EACH(1)%0` | 0 = pre-amp side, 1 = post |
| EQ2 pos | `PATCH%EQ_EACH(2)%0` | 0 / 1 |

### Base flows (pdlVal=0, srVal=0) — CHAIN 0..8

Human labels in UI string table (subset): CHAIN1, CHAIN2-1, CHAIN3-1, CHAIN4-1, CHAIN2-2, …

| CHAIN | order (then FV / SR / delays per row; amp position varies) |
|------|------|
| 0 | pdl → bst → **amp** → mod → fx → … |
| 1 | pdl → bst → mod → **amp** → fx → … |
| 2 | pdl → bst → mod → fx → **amp** → … |
| 3 | pdl → bst → mod → fx → dly1 → **amp** → … |
| 4 | pdl → **mod** → bst → **amp** → fx → … |
| 5 | pdl → mod → bst → fx → **amp** → … |
| 6 | pdl → mod → bst → fx → dly1 → **amp** → … |
| 7 | pdl → mod → fx → bst → **amp** → … |
| 8 | pdl → mod → fx → bst → dly1 → **amp** → … |

Full tables (all pdl×sr combinations) are in `tools/chain_tables.py`.

### EQ insert (`CHAIN_EQ_FLOW`)

| eq1 | eq2 | around amp |
|-----|-----|------------|
| 0 | 0 | eq → eq2 → amp |
| 1 | 1 | amp → eq → eq2 |
| 0 | 1 | eq → amp → eq2 |
| 1 | 0 | eq2 → amp → eq |

Final order =  
`[input] + base[:amp] + eq_list + base[amp+1:] + [speaker]`  
(with `amp` removed from base when splicing EQ list that already contains amp).

## SW block

| ofs | switch |
|-----|--------|
| 0 | booster |
| 1 | mod |
| 2 | fx |
| 3 | delay |
| 4 | delay2 |
| 5 | reverb |

## Example: Tone Exchange “Master of puppets”

From `.tsl` paramSet:

- OTHER = `[4, 2, 0]` → **CHAIN 4**, cab res 2
- COLOR = `[0, 0, 2, 0, 0]` → FX panel on **yellow** variant
- SW = `[1,0,0,0,0,1]` → booster + reverb on
- AMP brown-ish (type 5), booster(1) type 11…
- FX types: MOD green=23 CHORUS, FX yellow slot FX(6)=15 FLANGER (color 2 → FX 4+2=6)

## Example: “SRV Blues”

- OTHER = `[2, 0, 0]` → **CHAIN 2** (mod+fx before amp)
- SW booster + reverb
- FX(1)=3 COMP on MOD green

## What our Linux stack implements today

| Area | Status |
|------|--------|
| AMP / SW / delay / reverb / booster write | yes |
| Preset JSON load/save + UI | yes |
| Pitch detail params | yes |
| FX **type** byte | write attempted; RQ1 often fails over BLE |
| COLOR / OTHER.chain / EQ pos / full .tsl | **import tool**; live write expanding |
| ASSIGN / GAFC / solo / contour | in .tsl only so far |

## Tools

```bash
# Decode a Tone Exchange liveset
.venv/bin/python tools/tsl_import.py "/path/to/file.tsl" --summary

# Convert first tone → presets/*.json
.venv/bin/python tools/tsl_import.py "/path/to/file.tsl" --write-preset

# Print resolved chain order for a tone
.venv/bin/python tools/tsl_import.py "/path/to/file.tsl" --chain
```
