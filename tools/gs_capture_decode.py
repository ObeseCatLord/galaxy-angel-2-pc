#!/usr/bin/env python3
"""Decode a GA2_GS_CAPTURE file (runtime gs_pgs.cpp): one line per draw batch,
transfer and vsync.

usage: gs_capture_decode.py <capture>

Draw lines: primitive, context, frame buffer (block address/width/format,
write mask), TEST, depth buffer, texture, ALPHA as A B C D digits
(0 = Cs, 1 = Cd, 2 = 0 / As / FIX), vertex count, screen bounds and the
first vertex colour (AABBGGRR). Consecutive draws with the same state
are merged.
"""
import struct
import sys

PSM = {0: 'CT32', 1: 'CT24', 2: 'CT16', 0xA: 'CT16S', 0x13: 'T8', 0x14: 'T4', 0x1B: 'T8H', 0x24: 'T4HL',
       0x2C: 'T4HH', 0x30: 'Z32', 0x31: 'Z24', 0x32: 'Z16', 0x3A: 'Z16S'}
PRIMS = ['POINT', 'LINE', 'LSTRIP', 'TRI', 'TSTRIP', 'TFAN', 'SPRITE', 'BAD']

def bits(v, lo, n):
    return (v >> lo) & ((1 << n) - 1)


def psm(p):
    return PSM.get(p, hex(p))


class GS:
    def __init__(self, out):
        self.r = {}
        self.out = out
        self.batch = None
        self.verts = []
        self.rgba = 0
        self.prim = 0
        self.paths = {}

    def flush(self):
        if self.batch:
            key, n, box, col = self.batch
            self.out(f"  draw {key} n={n} box=({box[0]:.0f},{box[1]:.0f})-({box[2]:.0f},{box[3]:.0f}) rgba={col:08x}")
        self.batch = None

    def attrs(self):
        if bits(self.r.get(0x1A, 1), 0, 1) == 0:
            return self.r.get(0x1B, 0)
        return self.prim

    def state_key(self):
        a = self.attrs()
        ctx = bits(a, 9, 1)
        fr = self.r.get(0x4C + ctx, 0)
        key = (f"{PRIMS[self.prim & 7]} ctx{ctx} FB={bits(fr, 0, 9) * 32:#x}/{bits(fr, 16, 6)}/{psm(bits(fr, 24, 6))}"
               f" msk={bits(fr, 32, 32):08x}")
        zb = self.r.get(0x4E + ctx, 0)
        test = self.r.get(0x47 + ctx, 0)
        key += f" TEST={test:x}"
        if bits(test, 16, 1):
            key += f" Z={bits(zb, 0, 9) * 32:#x}/{psm(bits(zb, 24, 4) | 0x30)}{'/nowrite' if bits(zb, 32, 1) else ''}"
        if bits(a, 4, 1):
            t = self.r.get(0x06 + ctx, 0)
            key += (f" TEX={bits(t, 0, 14):#x}/{bits(t, 14, 6)}/{psm(bits(t, 20, 6))} {1 << bits(t, 26, 4)}x"
                    f"{1 << bits(t, 30, 4)} tcc{bits(t, 34, 1)} tfx{bits(t, 35, 2)}")
            if bits(t, 20, 6) in (0x13, 0x14, 0x1B, 0x24, 0x2C):
                key += f" CBP={bits(t, 37, 14):#x}/{psm(bits(t, 51, 4))}"
        if bits(a, 6, 1):
            al = self.r.get(0x42 + ctx, 0)
            key += f" ALPHA={bits(al, 0, 2)}{bits(al, 2, 2)}{bits(al, 4, 2)}{bits(al, 6, 2)} fix={bits(al, 32, 8)}"
        if bits(a, 8, 1) == 0 and bits(a, 4, 1):
            key += " STQ"
        return key

    def vertex(self, x, y, kick):
        ctx = bits(self.attrs(), 9, 1)
        off = self.r.get(0x18 + ctx, 0)
        fx, fy = (x - bits(off, 0, 16)) / 16, (y - bits(off, 32, 16)) / 16
        if not kick:
            return
        key = self.state_key()
        if self.batch and self.batch[0] == key:
            k, n, b, c = self.batch
            self.batch = (k, n + 1, (min(b[0], fx), min(b[1], fy), max(b[2], fx), max(b[3], fy)), c)
        else:
            self.flush()
            self.batch = (key, 1, (fx, fy, fx, fy), self.rgba)

    def write(self, addr, v):
        if addr == 0x00:
            self.prim = v
        elif addr == 0x01:
            self.rgba = v & 0xFFFFFFFF
        elif addr in (0x04, 0x05):
            self.vertex(bits(v, 0, 16), bits(v, 16, 16), True)
            return
        elif addr in (0x0C, 0x0D):
            self.vertex(bits(v, 0, 16), bits(v, 16, 16), False)
            return
        self.r[addr] = v
        if addr == 0x53:
            self.flush()
            b = self.r.get(0x50, 0)
            p = self.r.get(0x51, 0)
            g = self.r.get(0x52, 0)
            d = bits(v, 0, 2)
            self.out(f"  xfer dir={d} src={bits(b, 0, 14):#x}/{bits(b, 16, 6)}/{psm(bits(b, 24, 6))}"
                     f" dst={bits(b, 32, 14):#x}/{bits(b, 48, 6)}/{psm(bits(b, 56, 6))}"
                     f" src@({bits(p, 0, 11)},{bits(p, 16, 11)}) dst@({bits(p, 32, 11)},{bits(p, 48, 11)})"
                     f" {bits(g, 0, 12)}x{bits(g, 32, 12)}")

    def packed(self, reg, lo, hi):
        if reg == 0x0:
            self.write(0x00, lo)
        elif reg == 0x1:
            self.rgba = (lo & 0xFF) | ((lo >> 32 & 0xFF) << 8) | ((hi & 0xFF) << 16) | ((hi >> 32 & 0xFF) << 24)
        elif reg in (0x4, 0x5):
            adc = bits(hi, 47, 1)
            self.vertex(bits(lo, 0, 16), bits(lo, 32, 16), not adc)
        elif reg in (0xC, 0xD):
            self.vertex(bits(lo, 0, 16), bits(lo, 32, 16), False)
        elif reg == 0xE:
            self.write(bits(hi, 0, 8), lo)
        elif reg in (0x6, 0x7, 0x8, 0x9):
            self.write(reg, lo)

    def packet(self, path, data):
        """GIF data of one path; a tag's data may continue in later calls."""
        st = self.paths.setdefault(path, {'left': 0})
        i = 0
        while i + 16 <= len(data):
            if st['left'] == 0:
                lo, hi = struct.unpack_from('<QQ', data, i)
                i += 16
                nloop, flg, nreg = bits(lo, 0, 15), bits(lo, 58, 2), bits(lo, 60, 4) or 16
                if bits(lo, 46, 1):
                    self.write(0x00, bits(lo, 47, 11))
                st.update(flg=flg, regs=[bits(hi, 4 * k, 4) for k in range(nreg)], k=0)
                if flg == 0:
                    st['left'] = nloop * nreg
                elif flg == 1:
                    st['count'] = nloop * nreg
                    st['left'] = st['count'] + st['count'] % 2  # 64-bit words incl. padding
                else:
                    st['left'] = nloop  # qwords of image data
                continue
            if st['flg'] == 0:
                a, b = struct.unpack_from('<QQ', data, i)
                i += 16
                self.packed(st['regs'][st['k'] % len(st['regs'])], a, b)
                st['k'] += 1
                st['left'] -= 1
            elif st['flg'] == 1:
                for _ in range(2):
                    if st['left'] == 0:
                        break
                    (v,) = struct.unpack_from('<Q', data, i)
                    i += 8
                    reg = st['regs'][st['k'] % len(st['regs'])]
                    if st['k'] < st['count'] and reg not in (0xE, 0xF):
                        self.write(reg, v)
                    st['k'] += 1
                    st['left'] -= 1
                if i % 16:
                    i += 8
            else:
                take = min(st['left'], (len(data) - i) // 16)
                i += 16 * take
                st['left'] -= take

def main():
    raw = open(sys.argv[1], 'rb').read()
    lines = []
    gs = GS(lines.append)
    off, vs = 0, 0
    while off + 8 <= len(raw):
        path, size = struct.unpack_from('<II', raw, off)
        off += 8
        data = raw[off:off + size]
        off += size
        if path == 0xFFFFFFFF:
            gs.flush()
            r = struct.unpack_from('<20Q', data)
            pm, d1, d2, bg = r[0], r[7], r[9], r[15]
            lines.append(f"VSYNC {vs} pmode={pm:x} dispfb1={bits(d1, 0, 9) * 32:#x}/{psm(bits(d1, 15, 5))}"
                         f" dispfb2={bits(d2, 0, 9) * 32:#x}/{psm(bits(d2, 15, 5))} bg={bg:x}")
            vs += 1
        else:
            gs.packet(path, data)
    gs.flush()
    print('\n'.join(lines))


main()
