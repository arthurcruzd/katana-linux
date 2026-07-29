#!/usr/bin/env python3

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


def ck(b):
    return (128 - (sum(b) % 128)) & 0x7F


def msg(enc, addr, size):
    body = a4(enc(addr)) + a4(enc(size))
    return bytes([0xF0, 0x41, 0x10, 0x01, 0x05, 0x07, 0x11] + body + [ck(body), 0xF7])


print("7bitize SETUP", msg(bit7, 0, 2).hex(" "))
print("7bitize AMP  ", msg(bit7, 0x20000600, 0x0A).hex(" "))
print("nibble  SETUP", msg(nibble, 0, 2).hex(" "))
print("nibble  AMP  ", msg(nibble, 0x20000600, 0x0A).hex(" "))
print("raw     SETUP", msg(lambda x: x, 0, 2).hex(" "))
print("raw     AMP  ", msg(lambda x: x, 0x20000600, 0x0A).hex(" "))
print("bit7(0x20000600)", hex(bit7(0x20000600)), a4(bit7(0x20000600)))
print("nibble(0x20000600)", hex(nibble(0x20000600)), a4(nibble(0x20000600)))
print("bit7(nibble)", hex(bit7(nibble(0x20000600))))
print("nibble(bit7)", hex(nibble(bit7(0x20000600))))

# BLE wrap helper
def wrap(sysex: bytes) -> str:
    # single packet if small enough
    data = list(sysex)
    if len(data) <= 18:
        if data[-1] == 0xF7:
            pkt = [0x80, 0x80] + data[:-1] + [0x80, 0xF7]
        else:
            pkt = [0x80, 0x80] + data
        return " ".join(f"0x{b:02X}" for b in pkt)
    # multi
    parts = []
    first = True
    while data:
        chunk = data[:16]
        data = data[16:]
        if first:
            pkt = [0x80, 0x80] + chunk
            first = False
        elif not data and chunk[-1] == 0xF7:
            pkt = [0x80] + chunk[:-1] + [0x80, 0xF7]
        else:
            pkt = [0x80] + chunk
        parts.append(" ".join(f"0x{b:02X}" for b in pkt))
    return " || ".join(parts)


for label, m in [
    ("7b SETUP", msg(bit7, 0, 2)),
    ("7b AMP", msg(bit7, 0x20000600, 0x0A)),
    ("nb SETUP", msg(nibble, 0, 2)),
    ("nb AMP", msg(nibble, 0x20000600, 0x0A)),
    ("raw SETUP", msg(lambda x: x, 0, 2)),
    ("raw AMP", msg(lambda x: x, 0x20000600, 0x0A)),
]:
    print(f"WRAP {label}: {wrap(m)}")
