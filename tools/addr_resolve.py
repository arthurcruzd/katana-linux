def nibble(x):
    return (
        ((x & 0x7F000000) >> 3)
        | ((x & 0x007F0000) >> 2)
        | ((x & 0x00007F00) >> 1)
        | (x & 0x0000007F)
    )


def bit7(x):
    return (
        ((x & 0x0FE00000) << 3)
        | ((x & 0x001FC000) << 2)
        | ((x & 0x00003F80) << 1)
        | (x & 0x0000007F)
    )


def a4(v):
    return [(v >> 24) & 0x7F, (v >> 16) & 0x7F, (v >> 8) & 0x7F, v & 0x7F]


def path(levels, label):
    s = 0
    for L in levels:
        s += nibble(L)
    print(label, "sum_nib", hex(s), "wireA", [hex(x) for x in a4(bit7(s))], "raw", [hex(x) for x in a4(sum(levels))])


path([0x20000000, 0x1000, 0], "FX1 type")
path([0x20000000, 0x600, 0], "AMP gain")
path([0x20000000, 0xA00, 0], "BST1")
path([0x20000000, 0x1C00, 0x4A], "PS pitch")
path([0x20000000, 0x2800, 0], "DELAY")

# Special: what if 0x1000 nibbles wrong for high bits in low word
print("nibble(0x1000)", hex(nibble(0x1000)))
print("nibble(0x1C00)", hex(nibble(0x1C00)))
print("nibble(0x2000)", hex(nibble(0x2000)))
print("nibble(0x2800)", hex(nibble(0x2800)))
