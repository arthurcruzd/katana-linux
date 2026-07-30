"""Katana Gen 3 chain tables extracted from BTS chain_config.js."""

from __future__ import annotations

# CHAIN_BLOCK_NUM_RELATION order (index = block number in UI internals)
BLOCK_IDS = [
    "in",
    "pdl",
    "bst",
    "mod",
    "eq",
    "eq2",
    "fv",
    "amp",
    "fx",
    "sr",
    "dly1",
    "dly2",
    "rev",
    "speaker",
]

# pdlVal, srVal → 9 chains (index 0..8)
CHAIN_BASE_FLOW: dict[tuple[int, int], list[list[str]]] = {
    (0, 0): [
        ["pdl", "bst", "amp", "mod", "fx", "fv", "sr", "dly1", "dly2", "rev"],
        ["pdl", "bst", "mod", "amp", "fx", "fv", "sr", "dly1", "dly2", "rev"],
        ["pdl", "bst", "mod", "fx", "amp", "fv", "sr", "dly1", "dly2", "rev"],
        ["pdl", "bst", "mod", "fx", "dly1", "amp", "fv", "sr", "dly2", "rev"],
        ["pdl", "mod", "bst", "amp", "fx", "fv", "sr", "dly1", "dly2", "rev"],
        ["pdl", "mod", "bst", "fx", "amp", "fv", "sr", "dly1", "dly2", "rev"],
        ["pdl", "mod", "bst", "fx", "dly1", "amp", "fv", "sr", "dly2", "rev"],
        ["pdl", "mod", "fx", "bst", "amp", "fv", "sr", "dly1", "dly2", "rev"],
        ["pdl", "mod", "fx", "bst", "dly1", "amp", "fv", "sr", "dly2", "rev"],
    ],
    (0, 1): [
        ["pdl", "bst", "amp", "mod", "fx", "fv", "dly1", "dly2", "rev", "sr"],
        ["pdl", "bst", "mod", "amp", "fx", "fv", "dly1", "dly2", "rev", "sr"],
        ["pdl", "bst", "mod", "fx", "amp", "fv", "dly1", "dly2", "rev", "sr"],
        ["pdl", "bst", "mod", "fx", "dly1", "amp", "fv", "dly2", "rev", "sr"],
        ["pdl", "mod", "bst", "amp", "fx", "fv", "dly1", "dly2", "rev", "sr"],
        ["pdl", "mod", "bst", "fx", "amp", "fv", "dly1", "dly2", "rev", "sr"],
        ["pdl", "mod", "bst", "fx", "dly1", "amp", "fv", "dly2", "rev", "sr"],
        ["pdl", "mod", "fx", "bst", "amp", "fv", "dly1", "dly2", "rev", "sr"],
        ["pdl", "mod", "fx", "bst", "dly1", "amp", "fv", "dly2", "rev", "sr"],
    ],
    (0, 2): [
        ["pdl", "bst", "amp", "sr", "mod", "fx", "fv", "dly1", "dly2", "rev"],
        ["pdl", "bst", "mod", "amp", "sr", "fx", "fv", "dly1", "dly2", "rev"],
        ["pdl", "bst", "mod", "fx", "amp", "sr", "fv", "dly1", "dly2", "rev"],
        ["pdl", "bst", "mod", "fx", "dly1", "amp", "sr", "fv", "dly2", "rev"],
        ["pdl", "mod", "bst", "amp", "sr", "fx", "fv", "dly1", "dly2", "rev"],
        ["pdl", "mod", "bst", "fx", "amp", "sr", "fv", "dly1", "dly2", "rev"],
        ["pdl", "mod", "bst", "fx", "dly1", "amp", "sr", "fv", "dly2", "rev"],
        ["pdl", "mod", "fx", "bst", "amp", "sr", "fv", "dly1", "dly2", "rev"],
        ["pdl", "mod", "fx", "bst", "dly1", "amp", "sr", "fv", "dly2", "rev"],
    ],
    (1, 0): [
        ["bst", "amp", "pdl", "mod", "fx", "fv", "sr", "dly1", "dly2", "rev"],
        ["bst", "mod", "amp", "pdl", "fx", "fv", "sr", "dly1", "dly2", "rev"],
        ["bst", "mod", "fx", "amp", "pdl", "fv", "sr", "dly1", "dly2", "rev"],
        ["bst", "mod", "fx", "dly1", "amp", "pdl", "fv", "sr", "dly2", "rev"],
        ["mod", "bst", "amp", "pdl", "fx", "fv", "sr", "dly1", "dly2", "rev"],
        ["mod", "bst", "fx", "amp", "pdl", "fv", "sr", "dly1", "dly2", "rev"],
        ["mod", "bst", "fx", "dly1", "amp", "pdl", "fv", "sr", "dly2", "rev"],
        ["mod", "fx", "bst", "amp", "pdl", "fv", "sr", "dly1", "dly2", "rev"],
        ["mod", "fx", "bst", "dly1", "amp", "pdl", "fv", "sr", "dly2", "rev"],
    ],
    (1, 1): [
        ["bst", "amp", "pdl", "mod", "fx", "fv", "dly1", "dly2", "rev", "sr"],
        ["bst", "mod", "amp", "pdl", "fx", "fv", "dly1", "dly2", "rev", "sr"],
        ["bst", "mod", "fx", "amp", "pdl", "fv", "dly1", "dly2", "rev", "sr"],
        ["bst", "mod", "fx", "dly1", "amp", "pdl", "fv", "dly2", "rev", "sr"],
        ["mod", "bst", "amp", "pdl", "fx", "fv", "dly1", "dly2", "rev", "sr"],
        ["mod", "bst", "fx", "amp", "pdl", "fv", "dly1", "dly2", "rev", "sr"],
        ["mod", "bst", "fx", "dly1", "amp", "pdl", "fv", "dly2", "rev", "sr"],
        ["mod", "fx", "bst", "amp", "pdl", "fv", "dly1", "dly2", "rev", "sr"],
        ["mod", "fx", "bst", "dly1", "amp", "pdl", "fv", "dly2", "rev", "sr"],
    ],
    (1, 2): [
        ["bst", "amp", "pdl", "sr", "mod", "fx", "fv", "dly1", "dly2", "rev"],
        ["bst", "mod", "amp", "pdl", "sr", "fx", "fv", "dly1", "dly2", "rev"],
        ["bst", "mod", "fx", "amp", "pdl", "sr", "fv", "dly1", "dly2", "rev"],
        ["bst", "mod", "fx", "dly1", "amp", "pdl", "sr", "fv", "dly2", "rev"],
        ["mod", "bst", "amp", "pdl", "sr", "fx", "fv", "dly1", "dly2", "rev"],
        ["mod", "bst", "fx", "amp", "pdl", "sr", "fv", "dly1", "dly2", "rev"],
        ["mod", "bst", "fx", "dly1", "amp", "pdl", "sr", "fv", "dly2", "rev"],
        ["mod", "fx", "bst", "amp", "pdl", "sr", "fv", "dly1", "dly2", "rev"],
        ["mod", "fx", "bst", "dly1", "amp", "pdl", "sr", "fv", "dly2", "rev"],
    ],
}

CHAIN_EQ_FLOW = {
    (0, 0): ["eq", "eq2", "amp"],
    (1, 1): ["amp", "eq", "eq2"],
    (0, 1): ["eq", "amp", "eq2"],
    (1, 0): ["eq2", "amp", "eq"],
}

CHAIN_LABELS = [
    "CHAIN1",
    "CHAIN2-1",
    "CHAIN3-1",
    "CHAIN4-1",
    "CHAIN2-2",
    "CHAIN3-2",
    "CHAIN4-2",
    "CHAIN5",  # not in short UI string; still valid 0..8
    "CHAIN6",
]

FX_TYPES = [
    "T.WAH",
    "AUTO WAH",
    "PEDAL WAH",
    "COMP",
    "LIMITER",
    "GRAPHIC EQ",
    "PARAMETRIC EQ",
    "GUITAR SIM",
    "SLOW GEAR",
    "WAVE SYNTH",
    "OCTAVE",
    "PITCH SHIFTER",
    "HARMONIST",
    "AC.PROCESSOR",
    "PHASER",
    "FLANGER",
    "TREMOLO",
    "ROTARY",
    "UNI-V",
    "SLICER",
    "VIBRATO",
    "RING MOD",
    "HUMANIZER",
    "CHORUS",
    "AC.GUITAR SIM",
    "PHASER 90E",
    "FLANGER 117E",
    "WAH 95E",
    "DC-30",
    "HEAVY OCTAVE",
    "PEDAL BEND",
]

AMP_TYPES = ["acoustic", "clean", "pushed", "crunch", "lead", "brown"]
COLOR_NAMES = ["green", "red", "yellow"]


def resolve_chain(
    chain: int,
    *,
    pdl_pos: int = 0,
    sr_pos: int = 0,
    eq1_pos: int = 0,
    eq2_pos: int = 0,
) -> list[str]:
    """Return full block id order including input/speaker."""
    chain = max(0, min(8, int(chain)))
    pdl_pos = int(pdl_pos) & 1
    sr_pos = max(0, min(2, int(sr_pos)))
    key = (pdl_pos, sr_pos)
    if key not in CHAIN_BASE_FLOW:
        key = (0, 0)
    base = list(CHAIN_BASE_FLOW[key][chain])
    eq_key = (int(eq1_pos) & 1, int(eq2_pos) & 1)
    eq_list = list(CHAIN_EQ_FLOW.get(eq_key, CHAIN_EQ_FLOW[(0, 0)]))
    amp_i = base.index("amp")
    # BTS: base[:amp] + eq_list + base[amp+1:]  (eq_list already contains amp)
    full = ["in", *base[:amp_i], *eq_list, *base[amp_i + 1 :], "speaker"]
    return full


def chain_label(chain: int) -> str:
    chain = int(chain)
    if 0 <= chain < len(CHAIN_LABELS):
        return CHAIN_LABELS[chain]
    return f"CHAIN({chain})"
