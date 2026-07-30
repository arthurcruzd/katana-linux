"""Map PATCH%block names (as in .tsl paramSet) → temp-patch absolute addresses."""

from __future__ import annotations

# Relative offsets under PATCH base 0x20000000 (live/temp)
PATCH_REL: dict[str, int] = {
    "COM": 0x0000,
    "OTHER": 0x0200,
    "COLOR": 0x0400,
    "AMP": 0x0600,
    "SW": 0x0800,
    "BOOSTER(1)": 0x0A00,
    "BOOSTER(2)": 0x0C00,
    "BOOSTER(3)": 0x0E00,
    "FX(1)": 0x1000,
    "FX(2)": 0x1200,
    "FX(3)": 0x1400,
    "FX(4)": 0x1600,
    "FX(5)": 0x1800,
    "FX(6)": 0x1A00,
    "FX_DETAIL(1)": 0x1C00,
    "FX_DETAIL(2)": 0x1E00,
    "FX_DETAIL(3)": 0x2000,
    "FX_DETAIL(4)": 0x2200,
    "FX_DETAIL(5)": 0x2400,
    "FX_DETAIL(6)": 0x2600,
    "DELAY(1)": 0x2800,
    "DELAY(2)": 0x2A00,
    "DELAY(3)": 0x2C00,
    "DELAY(4)": 0x2E00,
    "DELAY(5)": 0x3000,
    "DELAY(6)": 0x3200,
    "REVERB(1)": 0x3400,
    "REVERB(2)": 0x3600,
    "REVERB(3)": 0x3800,
    "SOLO_COM": 0x3A00,
    "SOLO_EQ": 0x3C00,
    "SOLO_DELAY": 0x3E00,
    "CONTOUR_COM": 0x4000,
    "CONTOUR(1)": 0x4200,
    "CONTOUR(2)": 0x4400,
    "CONTOUR(3)": 0x4600,
    "PEDALFX_COM": 0x4800,
    "PEDALFX": 0x4A00,
    "EQ_EACH(1)": 0x4C00,
    "EQ_EACH(2)": 0x4E00,
    "EQ_PEQ(1)": 0x5000,
    "EQ_PEQ(2)": 0x5200,
    "EQ_GE10(1)": 0x5400,
    "EQ_GE10(2)": 0x5600,
    "NS": 0x5800,
    "SENDRETURN": 0x5A00,
    "ASSIGN_KNOBS": 0x5C00,
    "ASSIGN_EXPPDL_FUNC": 0x5E00,
    "ASSIGN_EXPPDL_DETAIL": 0x6000,
    "ASSIGN_EXPPDL_MIN": 0x6200,
    "ASSIGN_EXPPDL_MAX": 0x6400,
    "ASSIGN_FS": 0x6600,
    "ASSIGN_GAFCFS(1)": 0x6800,
    "ASSIGN_GAFCFS(2)": 0x6A00,
    "ASSIGN_GAFCEXPPDL1_FUNC(1)": 0x6C00,
    "ASSIGN_GAFCEXPPDL1_FUNC(2)": 0x6E00,
    "ASSIGN_GAFCEXPPDL1_DETAIL(1)": 0x7000,
    "ASSIGN_GAFCEXPPDL1_DETAIL(2)": 0x7200,
    "ASSIGN_GAFCEXPPDL1_MIN(1)": 0x7400,
    "ASSIGN_GAFCEXPPDL1_MIN(2)": 0x7600,
    "ASSIGN_GAFCEXPPDL1_MAX(1)": 0x7800,
    "ASSIGN_GAFCEXPPDL1_MAX(2)": 0x7A00,
    "ASSIGN_GAFCEXPPDL2_FUNC(1)": 0x7C00,
    "ASSIGN_GAFCEXPPDL2_FUNC(2)": 0x7E00,
    # note: map jumps to 0x10000 for remaining GAFC blocks
    "ASSIGN_GAFCEXPPDL2_DETAIL(1)": 0x10000,
    "ASSIGN_GAFCEXPPDL2_DETAIL(2)": 0x10200,
    "ASSIGN_GAFCEXPPDL2_MIN(1)": 0x10400,
    "ASSIGN_GAFCEXPPDL2_MIN(2)": 0x10600,
    "ASSIGN_GAFCEXPPDL2_MAX(1)": 0x10800,
    "ASSIGN_GAFCEXPPDL2_MAX(2)": 0x10A00,
}

# Complete from address_map continuation if needed
_EXTRA = {
    "ASSIGN_GAFCEXPPDL3_FUNC(1)": 0x10C00,
    "ASSIGN_GAFCEXPPDL3_FUNC(2)": 0x10E00,
    "ASSIGN_GAFCEXPPDL3_DETAIL(1)": 0x11000,
    "ASSIGN_GAFCEXPPDL3_DETAIL(2)": 0x11200,
    "ASSIGN_GAFCEXPPDL3_MIN(1)": 0x11400,
    "ASSIGN_GAFCEXPPDL3_MIN(2)": 0x11600,
    "ASSIGN_GAFCEXPPDL3_MAX(1)": 0x11800,
    "ASSIGN_GAFCEXPPDL3_MAX(2)": 0x11A00,
    "PATCH_KNOB_READONLY": 0x11C00,  # may differ — skip write
    "PATCH_KNOB_SOLO_DELAY_READONLY": 0x11E00,
}
PATCH_REL.update(_EXTRA)

TEMP_PATCH_BASE = 0x20000000

# Prefer writing structure before switches (SW last so FX turn on configured)
WRITE_PRIORITY = [
    "COM",
    "OTHER",
    "COLOR",
    "PEDALFX_COM",
    "PEDALFX",
    "EQ_EACH(1)",
    "EQ_EACH(2)",
    "EQ_PEQ(1)",
    "EQ_PEQ(2)",
    "EQ_GE10(1)",
    "EQ_GE10(2)",
    "SENDRETURN",
    "NS",
    "CONTOUR_COM",
    "CONTOUR(1)",
    "CONTOUR(2)",
    "CONTOUR(3)",
    "SOLO_COM",
    "SOLO_EQ",
    "SOLO_DELAY",
    # FX types before details
    "FX(1)",
    "FX(2)",
    "FX(3)",
    "FX(4)",
    "FX(5)",
    "FX(6)",
    "BOOSTER(1)",
    "BOOSTER(2)",
    "BOOSTER(3)",
    "FX_DETAIL(1)",
    "FX_DETAIL(2)",
    "FX_DETAIL(3)",
    "FX_DETAIL(4)",
    "FX_DETAIL(5)",
    "FX_DETAIL(6)",
    "DELAY(1)",
    "DELAY(2)",
    "DELAY(3)",
    "DELAY(4)",
    "DELAY(5)",
    "DELAY(6)",
    "REVERB(1)",
    "REVERB(2)",
    "REVERB(3)",
    "AMP",
    # assigns (optional bulk)
    "ASSIGN_KNOBS",
    "ASSIGN_EXPPDL_FUNC",
    "ASSIGN_EXPPDL_DETAIL",
    "ASSIGN_EXPPDL_MIN",
    "ASSIGN_EXPPDL_MAX",
    "ASSIGN_FS",
    "ASSIGN_GAFCFS(1)",
    "ASSIGN_GAFCFS(2)",
    "ASSIGN_GAFCEXPPDL1_FUNC(1)",
    "ASSIGN_GAFCEXPPDL1_FUNC(2)",
    "ASSIGN_GAFCEXPPDL1_DETAIL(1)",
    "ASSIGN_GAFCEXPPDL1_DETAIL(2)",
    "ASSIGN_GAFCEXPPDL1_MIN(1)",
    "ASSIGN_GAFCEXPPDL1_MIN(2)",
    "ASSIGN_GAFCEXPPDL1_MAX(1)",
    "ASSIGN_GAFCEXPPDL1_MAX(2)",
    "ASSIGN_GAFCEXPPDL2_FUNC(1)",
    "ASSIGN_GAFCEXPPDL2_FUNC(2)",
    "ASSIGN_GAFCEXPPDL2_DETAIL(1)",
    "ASSIGN_GAFCEXPPDL2_DETAIL(2)",
    "ASSIGN_GAFCEXPPDL2_MIN(1)",
    "ASSIGN_GAFCEXPPDL2_MIN(2)",
    "ASSIGN_GAFCEXPPDL2_MAX(1)",
    "ASSIGN_GAFCEXPPDL2_MAX(2)",
    "ASSIGN_GAFCEXPPDL3_FUNC(1)",
    "ASSIGN_GAFCEXPPDL3_FUNC(2)",
    "ASSIGN_GAFCEXPPDL3_DETAIL(1)",
    "ASSIGN_GAFCEXPPDL3_DETAIL(2)",
    "ASSIGN_GAFCEXPPDL3_MIN(1)",
    "ASSIGN_GAFCEXPPDL3_MIN(2)",
    "ASSIGN_GAFCEXPPDL3_MAX(1)",
    "ASSIGN_GAFCEXPPDL3_MAX(2)",
    "SW",  # last
]

SKIP_WRITE = {
    "PATCH_KNOB_READONLY",
    "PATCH_KNOB_SOLO_DELAY_READONLY",
}


def param_key_to_name(key: str) -> str | None:
    """'PATCH%AMP' → 'AMP'; 'PATCH%FX(1)' → 'FX(1)'."""
    if not key.startswith("PATCH%"):
        return None
    return key[len("PATCH%") :]


def resolve_addr(block_name: str, *, base: int = TEMP_PATCH_BASE) -> int | None:
    rel = PATCH_REL.get(block_name)
    if rel is None:
        return None
    return base + rel


def ordered_blocks(raw_blocks: dict[str, list[int]]) -> list[tuple[str, int, list[int]]]:
    """Return (name, addr, data) in write order."""
    by_name: dict[str, list[int]] = {}
    for k, data in raw_blocks.items():
        name = param_key_to_name(k) if k.startswith("PATCH%") else k
        if not name or name in SKIP_WRITE:
            continue
        if name not in PATCH_REL:
            continue
        by_name[name] = [int(x) & 0x7F for x in data]

    out: list[tuple[str, int, list[int]]] = []
    seen = set()
    for name in WRITE_PRIORITY:
        if name in by_name:
            addr = resolve_addr(name)
            if addr is not None:
                out.append((name, addr, by_name[name]))
                seen.add(name)
    for name, data in sorted(by_name.items()):
        if name not in seen:
            addr = resolve_addr(name)
            if addr is not None:
                out.append((name, addr, data))
    return out
