#!/usr/bin/env python3
"""Import BOSS Tone Exchange / Tone Studio .tsl livesets (JSON).

Examples:
  python tools/tsl_import.py ~/Downloads/SRV\\ Blues.tsl --summary --chain
  python tools/tsl_import.py ~/Downloads/Master\\ of\\ Puppets\\ tone.tsl --write-preset
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

from chain_tables import (  # noqa: E402
    AMP_TYPES,
    COLOR_NAMES,
    FX_TYPES,
    chain_label,
    resolve_chain,
)

PRESETS = ROOT / "presets"

# Logical addresses we already use in katana_ble (temp patch)
ADDR = {
    "com": 0x20000000,
    "other": 0x20000200,
    "color": 0x20000400,
    "amp": 0x20000600,
    "sw": 0x20000800,
    "booster": 0x20000A00,  # BOOSTER(1)
    "fx1": 0x20001000,
    "fx4": 0x20001600,
    "fx_detail1": 0x20001C00,
    "delay": 0x20002800,
    "reverb": 0x20003400,
}


def hx(vals) -> list[int]:
    out = []
    for v in vals:
        if isinstance(v, str):
            out.append(int(v, 16) if re.fullmatch(r"[0-9a-fA-F]{1,2}", v) else int(v))
        else:
            out.append(int(v))
    return out


def load_tsl(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def iter_tones(tsl: dict):
    """Yield (index, tone_dict with paramSet)."""
    data = tsl.get("data") or []
    n = 0
    for group in data:
        if not isinstance(group, list):
            continue
        for tone in group:
            if isinstance(tone, dict) and "paramSet" in tone:
                yield n, tone
                n += 1


def ps_get(ps: dict, key: str) -> list[int] | None:
    if key not in ps:
        return None
    return hx(ps[key])


def decode_tone(tone: dict, *, liveset_name: str = "") -> dict:
    ps = tone.get("paramSet") or {}
    com = ps_get(ps, "PATCH%COM") or []
    name = bytes(b for b in com if 32 <= b < 127).decode("ascii", "replace").rstrip() or "tone"
    other = ps_get(ps, "PATCH%OTHER") or [2, 1, 0]
    color = ps_get(ps, "PATCH%COLOR") or [0, 0, 0, 0, 0]
    amp = ps_get(ps, "PATCH%AMP") or [0] * 10
    sw = ps_get(ps, "PATCH%SW") or [0] * 6
    bst = ps_get(ps, "PATCH%BOOSTER(1)") or [0] * 8
    dly = ps_get(ps, "PATCH%DELAY(1)") or [0] * 17
    rev = ps_get(ps, "PATCH%REVERB(1)") or [0] * 13
    pdl_com = ps_get(ps, "PATCH%PEDALFX_COM") or [0, 0, 0]
    sr = ps_get(ps, "PATCH%SENDRETURN") or [0, 0, 0, 50, 50]
    eq1 = ps_get(ps, "PATCH%EQ_EACH(1)") or [0, 0, 0]
    eq2 = ps_get(ps, "PATCH%EQ_EACH(2)") or [0, 0, 0]
    ns = ps_get(ps, "PATCH%NS") or [0, 0, 0]

    chain = other[0] if other else 2
    pdl_pos = pdl_com[0] if pdl_com else 0
    sr_pos = sr[1] if len(sr) > 1 else 0
    eq1_pos = eq1[0] if eq1 else 0
    eq2_pos = eq2[0] if eq2 else 0
    order = resolve_chain(chain, pdl_pos=pdl_pos, sr_pos=sr_pos, eq1_pos=eq1_pos, eq2_pos=eq2_pos)

    def fx_type(i: int) -> int | None:
        v = ps_get(ps, f"PATCH%FX({i})")
        return v[0] if v else None

    mod_color = color[1] if len(color) > 1 else 0
    fx_color = color[2] if len(color) > 2 else 0
    mod_slot = 1 + mod_color  # FX(1..3)
    fx_slot = 4 + fx_color  # FX(4..6)
    mod_t = fx_type(mod_slot)
    fx_t = fx_type(fx_slot)

    amp_type = amp[7] if len(amp) > 7 else 0
    # Some dumps put type at last index; BTS AMP_FIELDS: gain,vol,bass,mid,treble,presence,poweramp_var,type,...
    # Our AMP_FIELDS from katana_ble: verify - historically type at index 7 or 9
    # TSL AMP n=10: SRV [65,65,45,50,60,65,1,1,50,1] → type likely index 7 = 1 clean? or 9?
    # MoP [55,12,68,22,74,58,1,5,65,0] type 5 = brown fits index 7.
    if amp_type > 5 and len(amp) > 9:
        amp_type = amp[9]

    preset = {
        "name": name[:16],
        "song": liveset_name or "",
        "tuning": "",
        "notes": (tone.get("memo") or "").strip()
        or f"Imported from Tone Exchange .tsl. chain={chain_label(chain)} ({chain}).",
        "chain": {
            "index": chain,
            "label": chain_label(chain),
            "order": order,
            "pdl_pos": pdl_pos,
            "sr_pos": sr_pos,
            "eq1_pos": eq1_pos,
            "eq2_pos": eq2_pos,
            "cabinet_resonance": other[1] if len(other) > 1 else 0,
            "master_key": other[2] if len(other) > 2 else 0,
        },
        "color": {
            "booster": COLOR_NAMES[color[0]] if color[0] < 3 else color[0],
            "mod": COLOR_NAMES[color[1]] if len(color) > 1 and color[1] < 3 else color[1],
            "fx": COLOR_NAMES[color[2]] if len(color) > 2 and color[2] < 3 else color[2],
            "delay": COLOR_NAMES[color[3]] if len(color) > 3 and color[3] < 3 else color[3],
            "reverb": COLOR_NAMES[color[4]] if len(color) > 4 and color[4] < 3 else color[4],
            "raw": color,
        },
        "amp": {
            "gain": amp[0],
            "volume": amp[1],
            "bass": amp[2],
            "middle": amp[3],
            "treble": amp[4],
            "presence": amp[5],
            "poweramp_variation": amp[6] if len(amp) > 6 else 0,
            "type": amp_type,
            "type_name": AMP_TYPES[amp_type] if amp_type < len(AMP_TYPES) else str(amp_type),
            "resonance": amp[8] if len(amp) > 8 else 0,
            "preamp_variation": amp[9] if len(amp) > 9 else 0,
        },
        "sw": {
            "booster": sw[0],
            "mod": sw[1],
            "fx": sw[2],
            "delay": sw[3],
            "delay2": sw[4],
            "reverb": sw[5],
        },
        "booster": {
            "type": bst[0],
            "drive": bst[1],
            "bottom": bst[2],
            "tone": bst[3],
            "solo_sw": bst[4],
            "solo_level": bst[5],
            "effect_level": bst[6],
            "direct_mix": bst[7],
        },
        "mod": {
            "slot": mod_slot,
            "type": mod_t,
            "type_name": FX_TYPES[mod_t] if mod_t is not None and mod_t < len(FX_TYPES) else None,
        },
        "fx": {
            "slot": fx_slot,
            "type": fx_t,
            "type_name": FX_TYPES[fx_t] if fx_t is not None and fx_t < len(FX_TYPES) else None,
        },
        "delay": {
            "type": dly[0],
            "time_raw": dly[1:5],
            "feedback": dly[5] if len(dly) > 5 else 0,
            "effect_level": dly[7] if len(dly) > 7 else 0,
            "direct_level": dly[8] if len(dly) > 8 else 100,
            "raw": dly,
        },
        "reverb": {
            "type": rev[0],
            "time": rev[2] if len(rev) > 2 else 0,
            "effect_level": rev[10] if len(rev) > 10 else 0,
            "raw": rev,
        },
        "ns": {"sw": ns[0], "threshold": ns[1] if len(ns) > 1 else 0, "release": ns[2] if len(ns) > 2 else 0},
        "param_set_keys": sorted(ps.keys()),
        # Keep raw blocks needed for full write later
        "raw_blocks": {
            k: hx(v) for k, v in ps.items() if k.startswith("PATCH%")
        },
    }
    return preset


def slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s.strip()).strip("-").lower()
    return s[:48] or "tone"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tsl", type=Path)
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--chain", action="store_true")
    ap.add_argument("--write-preset", action="store_true", help="Write presets/<slug>.json")
    ap.add_argument("--index", type=int, default=0, help="Tone index inside liveset")
    ap.add_argument("--all", action="store_true", help="Process all tones")
    args = ap.parse_args()

    tsl = load_tsl(args.tsl)
    tones = list(iter_tones(tsl))
    if not tones:
        print("no tones in", args.tsl, file=sys.stderr)
        return 1

    print(f"liveset: {tsl.get('name')!r}  device={tsl.get('device')}  format={tsl.get('formatRev')}  tones={len(tones)}")

    indices = range(len(tones)) if args.all else [max(0, min(len(tones) - 1, args.index))]
    for i in indices:
        _, tone = tones[i]
        dec = decode_tone(tone, liveset_name=str(tsl.get("name") or ""))
        if args.summary or not (args.chain or args.write_preset):
            print(f"\n[{i}] {dec['name']}")
            print(f"  amp: {dec['amp']['type_name']} gain={dec['amp']['gain']} vol={dec['amp']['volume']}")
            print(f"  sw: {dec['sw']}")
            print(f"  mod[{dec['mod']['slot']}]: {dec['mod']['type_name']} ({dec['mod']['type']})")
            print(f"  fx[{dec['fx']['slot']}]: {dec['fx']['type_name']} ({dec['fx']['type']})")
            print(f"  color: {dec['color']}")
            print(f"  chain: {dec['chain']['label']} #{dec['chain']['index']}")
        if args.chain:
            print(f"  order: {' → '.join(dec['chain']['order'])}")
        if args.write_preset:
            PRESETS.mkdir(exist_ok=True)
            # Drop huge raw_blocks from default preset used by apply_preset unless useful
            out = {k: v for k, v in dec.items() if k not in ("param_set_keys",)}
            # keep raw_blocks for full-fidelity reload experiments
            fn = PRESETS / f"tsl-{slug(dec['name'])}.json"
            fn.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
            print(f"  wrote {fn}  ({len(dec.get('raw_blocks') or {})} raw blocks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
