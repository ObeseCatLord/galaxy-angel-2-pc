#!/usr/bin/env python3
"""Extract the Galaxy Angel II space skies from the player's own disc.

The three games draw their 3D space backgrounds (ADV narration and title
backgrounds, every space battle stage) as additive "sky models": nested shells
of tri-strips around the origin (radius ~1,000-70,000) that carry tiled star
textures, vertex-coloured haze and clouds of textured nebula quads.  This tool
reads the disc (ISO image or a directory holding the disc's files), decodes the
sky models and their textures, and writes

* the textures as PNG (``textures/<pack>/<name>.png``),
* an equirectangular panorama per sky (``<sky>/<sky>_equirect.png``),
* six cubemap faces per sky (``<sky>/<sky>_cube_{px,nx,py,ny,pz,nz}.png``),
* ``<sky>/<sky>.json`` (layers, textures, blend and camera data),
* the 2D "space" backdrop pictures of the ADV mode (``backdrops/``).

Panorama mapping (game coordinates are X right, Y down, Z forward; the ADV
camera ``CAMERA01`` looks along +Z):  column u in [0, 1) maps to longitude
(u - 0.5) * 360 deg, with u = 0.5 looking along +Z and u = 0.75 along +X; row
v in [0, 1) maps to latitude (0.5 - v) * 180 deg, top = up = -Y.  Cube faces
use a Y-up right-handed frame: +x = game +X, +y = game -Y (up), +z = game -Z
(behind the ADV camera), so the front face nz looks along game +Z; each face is
the unmirrored view from inside (see CUBE_FACES).

Only the Python standard library and Pillow are required.  Run with ``-h`` for
usage; ``docs/assets/skybox.md`` describes the formats.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import struct
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # pragma: no cover - reported at run time
    Image = None

SECTOR = 2048

# ---------------------------------------------------------------------------
# Disc access: ISO9660 image or a directory with the disc's files
# ---------------------------------------------------------------------------


class Disc:
    """Read-only access to the top-level files of a PS2 disc."""

    def __init__(self, source: Path):
        self.source = Path(source)
        self.files: dict[str, tuple] = {}
        if self.source.is_dir():
            for entry in self.source.iterdir():
                if entry.is_file():
                    self.files[entry.name.upper()] = ('dir', entry)
            self._fh = None
        else:
            self._fh = open(self.source, 'rb')
            self._read_iso_root()
        self.program = next((n for n in self.files if re.fullmatch(r'S[LC][PEU][MSA]_\d{3}\.\d{2}', n)), None)

    def _read_iso_root(self):
        fh = self._fh
        for sector in range(16, 116):
            fh.seek(sector * SECTOR)
            vd = fh.read(SECTOR)
            if vd[1:6] != b'CD001':
                raise SystemExit(f'{self.source}: not an ISO9660 image')
            if vd[0] == 1:
                break
            if vd[0] == 255:
                raise SystemExit(f'{self.source}: no primary volume descriptor')
        root = vd[156:156 + 34]
        lba, size = struct.unpack_from('<I', root, 2)[0], struct.unpack_from('<I', root, 10)[0]
        fh.seek(lba * SECTOR)
        data = fh.read(size)
        pos = 0
        while pos < len(data):
            n = data[pos]
            if n == 0:  # records do not cross sectors
                pos = (pos // SECTOR + 1) * SECTOR
                continue
            rec = data[pos:pos + n]
            flen = rec[32]
            name = rec[33:33 + flen].decode('ascii', 'replace')
            if not rec[25] & 2 and name not in ('\0', '\1'):
                name = name.split(';')[0].upper()
                elba, esize = struct.unpack_from('<I', rec, 2)[0], struct.unpack_from('<I', rec, 10)[0]
                prev = self.files.get(name)
                if prev and rec[25] & 0x80 == 0 and prev[0] == 'iso':  # multi-extent tail
                    self.files[name] = ('iso', prev[1], prev[2] + esize)
                else:
                    self.files[name] = ('iso', elba, esize)
            pos += n

    def has(self, name: str) -> bool:
        return name.upper() in self.files

    def size(self, name: str) -> int:
        f = self.files[name.upper()]
        return f[1].stat().st_size if f[0] == 'dir' else f[2]

    def read(self, name: str, offset: int = 0, length: int | None = None) -> bytes:
        f = self.files[name.upper()]
        total = self.size(name)
        if length is None:
            length = total - offset
        if f[0] == 'dir':
            with open(f[1], 'rb') as fh:
                fh.seek(offset)
                return fh.read(length)
        self._fh.seek(f[1] * SECTOR + offset)
        return self._fh.read(length)


# ---------------------------------------------------------------------------
# Archives and compression
# ---------------------------------------------------------------------------


def raic_decompress(data: bytes) -> bytes:
    """'RAIC' container: 4-byte magic and the payload XOR 0x72, u32 size in
    clear, then Okumura LZSS (4 KiB zero-filled window, start 0xFEE, flag byte
    LSB first, 1 = literal, reference = 12-bit position + 4-bit length + 3).
    'RAIB' is the stored variant (payload only XOR 0x72)."""
    magic = bytes(c ^ 0x72 for c in data[:4])
    usize = struct.unpack_from('<I', data, 4)[0]
    if magic == b'RAIB':  # stored variant: XOR 0x72 only
        return bytes(c ^ 0x72 for c in data[8:8 + usize])
    if magic != b'RAIC':
        raise ValueError('not a RAIC/RAIB stream')
    src = bytes(c ^ 0x72 for c in data[8:])
    win = bytearray(4096)
    r = 0xFEE
    out = bytearray()
    i = 0
    n = len(src)
    flags = 0
    while len(out) < usize and i < n:
        flags >>= 1
        if not flags & 0x100:
            flags = src[i] | 0xFF00
            i += 1
            if i >= n:
                break
        if flags & 1:
            c = src[i]
            i += 1
            out.append(c)
            win[r] = c
            r = (r + 1) & 0xFFF
        else:
            if i + 1 >= n:
                break
            a, b = src[i], src[i + 1]
            i += 2
            p = a | ((b & 0xF0) << 4)
            for k in range((b & 0x0F) + 3):
                c = win[(p + k) & 0xFFF]
                out.append(c)
                win[r] = c
                r = (r + 1) & 0xFFF
    if len(out) != usize:
        raise ValueError('truncated RAIC stream')
    return bytes(out)


def unpack_entry(blob: bytes, usize: int, csize: int) -> bytes:
    """Entries with 0 < csize < usize are RAIC-compressed."""
    if csize and csize != usize:
        return raic_decompress(blob[:csize])
    return blob[:usize]


def _cstr(data: bytes, off: int) -> str:
    return data[off:data.index(b'\0', off)].decode('latin1')


def pidx_tree(header: bytes) -> list[tuple[str, int, int, int]]:
    """Directory-style PIDX (ALL.DAT): 24-byte records (dir: 1, name, count,
    first) / (file: 0, name, archive-name, offset, size, packed size)."""
    recoff, nrec = struct.unpack_from('<II', header, 0x0C)
    stroff = struct.unpack_from('<I', header, 0x20)[0]
    recs = [struct.unpack_from('<6I', header, recoff + 24 * i) for i in range(nrec)]
    out, seen = [], set()

    def walk(i, prefix):
        if i in seen:
            return
        seen.add(i)
        r = recs[i]
        name = _cstr(header, stroff + r[1])
        if r[0] == 1:
            for j in range(r[3], r[3] + r[2]):
                walk(j, prefix + name + '/')
        else:
            out.append((prefix + name, r[3], r[4], r[5]))

    for i in range(nrec):
        walk(i, '')
    return out


def pidx_packs(header: bytes) -> list[tuple[str, int, int]]:
    """Pack-style PIDX (ADV.DAT, ADVMAT.DAT, SLG.DAT): table at 0x50 of
    (name, 0, offset, size, entry count) records, each pack an FSTS file."""
    stroff = struct.unpack_from('<I', header, 0x20)[0]
    n = struct.unpack_from('<I', header, 0x50)[0]
    out = []
    for rel in struct.unpack_from('<%dI' % n, header, 0x54):
        name_off, _, off, size, _ = struct.unpack_from('<5I', header, 0x50 + rel)
        out.append((_cstr(header, stroff + name_off), off, size))
    return out


def fsts_entries(blob: bytes) -> list[tuple[str, int, int, int]]:
    """FSTS pack: count, entry table (name, offset, size, packed size),
    string table offset/size."""
    if blob[:4] != b'FSTS':
        raise ValueError('not an FSTS pack')
    n, eo, so, _ = struct.unpack_from('<4I', blob, 4)
    out = []
    for i in range(n):
        no, off, us, cs = struct.unpack_from('<4I', blob, eo + 16 * i)
        out.append((_cstr(blob, so + no), off, us, cs))
    return out


class Pack:
    """One FSTS pack read lazily from a PIDX pack archive."""

    def __init__(self, disc: Disc, dat: str, name: str, offset: int, size: int):
        self.disc, self.dat, self.name, self.offset, self.size = disc, dat, name, offset, size
        head = disc.read(dat, offset, 0x20)
        _, _, so, ss = struct.unpack_from('<4I', head, 4)
        self.entries = {e[0]: e[1:] for e in fsts_entries(disc.read(dat, offset, so + ss))}

    def get(self, path: str) -> bytes:
        off, us, cs = self.entries[path]
        return unpack_entry(self.disc.read(self.dat, self.offset + off, cs if cs and cs != us else us), us, cs)


def list_packs(disc: Disc, dat: str) -> list[Pack]:
    if not disc.has(dat):
        return []
    head = disc.read(dat, 0, 0x60)
    stoff, stsize = struct.unpack_from('<II', head, 0x20)
    header = disc.read(dat, 0, stoff + stsize)
    packs = []
    for name, off, size in pidx_packs(header):
        if disc.read(dat, off, 4) == b'FSTS':
            packs.append(Pack(disc, dat, name, off, size))
    return packs


def read_all_dat(disc: Disc) -> dict[str, tuple[int, int, int]]:
    head = disc.read('ALL.DAT', 0, 0x60)
    stoff, stsize = struct.unpack_from('<II', head, 0x20)
    return {e[0]: e[1:] for e in pidx_tree(disc.read('ALL.DAT', 0, stoff + stsize))}


def all_dat_get(disc: Disc, tree, path: str) -> bytes:
    off, us, cs = tree[path]
    return unpack_entry(disc.read('ALL.DAT', off, cs if cs and cs != us else us), us, cs)


# ---------------------------------------------------------------------------
# AGI textures
# ---------------------------------------------------------------------------


def _clut_index(i: int) -> int:
    # PSMCT32 CLUTs of 8-bit textures are stored in CSM1 order: swap bits 3, 4.
    return (i & 0xE7) | ((i & 0x08) << 1) | ((i & 0x10) >> 1)


class Texture:
    """Decoded AGI texture: RGBA bytes with PS2 alpha (0x80 = opaque)."""

    def __init__(self, data: bytes, name: str = ''):
        self.name = name
        pix_off = struct.unpack_from('<I', data, 0x08)[0]
        self.psm = struct.unpack_from('<H', data, 0x0E)[0]
        self.w, self.h = struct.unpack_from('<HH', data, 0x18)
        clut_off = struct.unpack_from('<I', data, 0x1C)[0]
        w, h = self.w, self.h
        if self.psm == 0x00:  # PSMCT32
            self.rgba = bytes(data[pix_off:pix_off + w * h * 4])
            return
        if self.psm == 0x14:  # PSMT4, low nibble first
            ncol = 16
            raw = data[pix_off:pix_off + w * h // 2]
            idx = bytearray(w * h)
            idx[0::2] = bytes(b & 15 for b in raw)
            idx[1::2] = bytes(b >> 4 for b in raw)
        elif self.psm == 0x13:  # PSMT8
            ncol = 256
            idx = data[pix_off:pix_off + w * h]
        else:
            raise ValueError(f'{name}: unsupported AGI pixel format {self.psm:#x}')
        clut = data[clut_off:clut_off + 4 * ncol]
        if ncol == 256:
            pal = [clut[4 * _clut_index(i):4 * _clut_index(i) + 4] for i in range(256)]
        else:
            pal = [clut[4 * i:4 * i + 4] for i in range(16)]
        self.rgba = b''.join(pal[i] for i in idx)

    def image(self):
        """Pillow RGBA image with alpha expanded from 0..128 to 0..255."""
        im = Image.frombytes('RGBA', (self.w, self.h), self.rgba)
        r, g, b, a = im.split()
        return Image.merge('RGBA', (r, g, b, a.point(lambda v: min(255, v * 2))))

    def sampler(self):
        """Premultiplied float texels: rgb * (a / 128) for additive drawing."""
        if not hasattr(self, '_tex'):
            px = self.rgba
            tex = []
            for i in range(0, len(px), 4):
                k = px[i + 3] / 128.0
                tex.append((px[i] * k, px[i + 1] * k, px[i + 2] * k))
            self._tex = tex
        return self._tex


# ---------------------------------------------------------------------------
# AXO models
# ---------------------------------------------------------------------------


def axo_chunks(d: bytes, o: int = 0, end: int | None = None):
    """AXO chunks: 4cc, u32 payload size, u32 count, u32 record size."""
    end = len(d) if end is None else end
    while o + 16 <= end:
        tag = d[o:o + 4]
        if not all(32 <= c < 127 for c in tag):
            break
        size, cnt, es = struct.unpack_from('<3I', d, o + 4)
        yield o, tag.decode('ascii'), size, cnt, es
        o += 16 + size


def dma_vif(d: bytes, o: int, end: int) -> bytes:
    out = bytearray()
    while o + 16 <= end:
        tag = struct.unpack_from('<I', d, o)[0]
        qwc, tid = tag & 0xFFFF, (tag >> 28) & 7
        out += d[o + 8:o + 16] + d[o + 16:o + 16 + 16 * qwc]
        o += 16 + 16 * qwc
        if tid in (0, 6, 7):
            break
    return bytes(out)


def vif_batches(v: bytes):
    """Split a VIF stream at MSCAL/MSCNT into {vu_address: [vectors]}."""
    o, cur, out = 0, {}, []
    while o + 4 <= len(v):
        code = struct.unpack_from('<I', v, o)[0]
        o += 4
        cmd, num, imm = (code >> 24) & 0x7F, (code >> 16) & 0xFF, code & 0xFFFF
        if cmd & 0x60 == 0x60:  # UNPACK
            vn, vl, n = (cmd >> 2) & 3, cmd & 3, num or 256
            comps = vn + 1
            nbytes = ((32 >> vl) * comps * n + 31) // 32 * 4
            data = v[o:o + nbytes]
            o += nbytes
            addr, usn = imm & 0x3FF, (imm >> 14) & 1
            fmt = {0: 'f', 1: 'H' if usn else 'h', 2: 'B' if usn else 'b'}.get(vl)
            if fmt is None:
                continue
            if vl == 0 and addr == 0:
                fmt = 'I'
            vals = struct.unpack_from('<%d%s' % (n * comps, fmt), data)
            cur[addr] = [vals[i * comps:(i + 1) * comps] for i in range(n)]
        elif cmd in (0x14, 0x15, 0x17):
            if cur:
                out.append(cur)
            cur = {}
        elif cmd == 0x20:
            o += 4
        elif cmd in (0x30, 0x31):
            o += 16
        elif cmd == 0x4A:
            o += 8 * (num or 256)
        elif cmd in (0x50, 0x51):
            o += 16 * (imm or 65536)
    if cur:
        out.append(cur)
    return out


def euler_matrix(rx, ry, rz, order):
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    mats = {'x': [[1, 0, 0], [0, cx, -sx], [0, sx, cx]],
            'y': [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]],
            'z': [[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]]}
    m = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    for axis in order:  # first letter applied first
        m = matmul(mats[axis], m)
    return m


def matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def matvec(m, v):
    return (m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
            m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
            m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2])


ROT_ORDER = 'xyz'  # rotation about X first, then Y, then Z (verified against screenshots, see docs)


class Model:
    """Sky model: atoms (frame, material, geometry) drawn in file order."""

    def __init__(self, data: bytes, name: str = ''):
        self.name = name
        ch = {c[1]: c for c in axo_chunks(data)}
        for need in ('ATOM', 'FRAM', 'MTRL', 'TEX ', 'GEOG'):
            if need not in ch:
                raise ValueError(f'{name}: AXO without {need.strip()} chunk')

        def recs(tag):
            o, _, _, cnt, es = ch[tag]
            return [data[o + 16 + es * i:o + 16 + es * (i + 1)] for i in range(cnt)]

        self.frames = {}
        for r in recs('FRAM'):
            fid = struct.unpack_from('<i', r, 0)[0]
            t = struct.unpack_from('<3f', r, 12)
            rot = struct.unpack_from('<3f', r, 24)
            s = struct.unpack_from('<3f', r, 36)
            parent = struct.unpack_from('<i', r, 48)[0]
            self.frames[fid] = dict(t=t, r=rot, s=s, parent=parent)
        self.textures = [r[4:].split(b'\0')[0].decode('latin1') for r in recs('TEX ')]
        self.material_tex = [struct.unpack_from('<i', r, 60)[0] for r in recs('MTRL')]
        self.atoms = []
        for r in recs('ATOM'):
            w = struct.unpack_from('<10i', r)
            self.atoms.append({r[i * 8:i * 8 + 4].decode('latin1'): w[i * 2 + 1] for i in range(5)})
        o, _, size, _, _ = ch['GEOG']
        self.geoms = []
        for c in axo_chunks(data, o + 16, o + 16 + size):
            strips = []
            for b in vif_batches(dma_vif(data, c[0] + 48, c[0] + 16 + c[2])):
                if not all(k in b for k in (0, 1, 3, 4)):
                    continue
                prim = b[0][0][0]
                strips.append(dict(prim=prim, pos=[p[:3] for p in b[1]], uv=[u[:2] for u in b[3]],
                                   col=[tuple(c) for c in b[4]]))
            self.geoms.append(strips)

    def frame_matrix(self, fid, order=ROT_ORDER):
        m = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        t = (0.0, 0.0, 0.0)
        chain = []
        while fid in self.frames and fid not in chain:
            chain.append(fid)
            fid = self.frames[fid]['parent']
        for f in reversed(chain):  # root first
            fr = self.frames[f]
            local = euler_matrix(*fr['r'], order)
            local = [[local[i][j] * fr['s'][j] for j in range(3)] for i in range(3)]
            t = tuple(a + b for a, b in zip(matvec(m, fr['t']), t))
            m = matmul(m, local)
        return m, t

    def layers(self, order=ROT_ORDER):
        """Yield (texture name, triangles) per atom; triangles are tuples of
        three (position, uv, rgba) vertices in model space."""
        for atom in self.atoms:
            m, t = self.frame_matrix(atom['FRAM'], order)
            tex = self.textures[self.material_tex[atom['MTRL']]]
            tris = []
            for strip in self.geoms[atom['GEOM']]:
                if strip['prim'] & 7 != 4:  # only triangle strips occur in sky models
                    continue
                # Some haze shells (white 16x16 si_abg_0000) carry NaN or huge
                # UVs; the texture is uniform, so any finite coordinate works.
                verts = [(tuple(a + b for a, b in zip(matvec(m, p), t)),
                          tuple(x if math.isfinite(x) else 0.0 for x in uv), c)
                         for p, uv, c in zip(strip['pos'], strip['uv'], strip['col'])
                         if all(math.isfinite(x) for x in p)]
                for i in range(len(verts) - 2):
                    tris.append((verts[i], verts[i + 1], verts[i + 2]))
            yield tex, tris


# ---------------------------------------------------------------------------
# Renderer: additive textured triangles seen from the origin
# ---------------------------------------------------------------------------


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a):
    n = math.sqrt(_dot(a, a)) or 1.0
    return (a[0] / n, a[1] / n, a[2] / n)


def equirect_dir(lon, lat):
    cl = math.cos(lat)
    return (cl * math.sin(lon), -math.sin(lat), cl * math.cos(lon))


class Canvas:
    """Equirectangular float RGB accumulation buffer."""

    def __init__(self, width):
        self.w, self.h = width, width // 2
        self.buf = [0.0] * (self.w * self.h * 3)
        w, h = self.w, self.h
        self.lon = [((x + 0.5) / w - 0.5) * 2 * math.pi for x in range(w)]
        self.sinlon = [math.sin(a) for a in self.lon]
        self.coslon = [math.cos(a) for a in self.lon]
        lat = [(0.5 - (y + 0.5) / h) * math.pi for y in range(h)]
        self.sinlat = [math.sin(a) for a in lat]
        self.coslat = [math.cos(a) for a in lat]

    def _bbox(self, vs):
        """Pixel bounding box of a spherical triangle (edges sampled)."""
        w, h = self.w, self.h
        pts = []
        for i in range(3):
            a, b = vs[i], vs[(i + 1) % 3]
            for k in range(17):
                f = k / 16
                pts.append(_norm(tuple(a[j] * (1 - f) + b[j] * f for j in range(3))))
        lats = [math.asin(max(-1.0, min(1.0, -p[1]))) for p in pts]
        lons = [math.atan2(p[0], p[2]) for p in pts]
        n01, n12, n20 = _cross(vs[0], vs[1]), _cross(vs[1], vs[2]), _cross(vs[2], vs[0])
        s = 1.0 if _dot(vs[0], n12) > 0 else -1.0
        pole = None
        for up in ((0.0, -1.0, 0.0), (0.0, 1.0, 0.0)):
            if all(s * _dot(up, n) >= 0 for n in (n01, n12, n20)):
                pole = up
        y0 = int((0.5 - max(lats) / math.pi) * h) - 2
        y1 = int((0.5 - min(lats) / math.pi) * h) + 2
        if pole is not None:
            if pole[1] < 0:
                y0 = 0
            else:
                y1 = h - 1
            return [(0, w - 1)], max(0, y0), min(h - 1, y1)
        base = lons[0]
        un = [base + ((l - base + math.pi) % (2 * math.pi)) - math.pi for l in lons]
        x0 = int(((min(un) / (2 * math.pi)) + 0.5) * w) - 2
        x1 = int(((max(un) / (2 * math.pi)) + 0.5) * w) + 2
        if x1 - x0 >= w:
            spans = [(0, w - 1)]
        elif x0 < 0:
            spans = [(0, x1), (x0 + w, w - 1)]
        elif x1 >= w:
            spans = [(x0, w - 1), (0, x1 - w)]
        else:
            spans = [(x0, x1)]
        return spans, max(0, y0), min(h - 1, y1)

    def draw(self, tris, tex: Texture):
        texels = tex.sampler()
        tw, th = tex.w, tex.h
        buf, w = self.buf, self.w
        sinlon, coslon, sinlat, coslat = self.sinlon, self.coslon, self.sinlat, self.coslat
        for v0, v1, v2 in tris:
            p0, p1, p2 = v0[0], v1[0], v2[0]
            n12, n20, n01 = _cross(p1, p2), _cross(p2, p0), _cross(p0, p1)
            det = _dot(p0, n12)
            if abs(det) < 1e-9:
                continue
            if det < 0:  # orient so that inside means all three dots >= 0
                n12, n20, n01 = (tuple(-c for c in n12), tuple(-c for c in n20), tuple(-c for c in n01))
            (u0, t0), (u1, t1), (u2, t2) = v0[1], v1[1], v2[1]
            c0, c1, c2 = v0[2], v1[2], v2[2]
            # Vertex colour factor: rgb/128 * a/128 (GS MODULATE + additive As blend).
            k0 = [c0[i] * c0[3] / 16384.0 for i in range(3)]
            k1 = [c1[i] * c1[3] / 16384.0 for i in range(3)]
            k2 = [c2[i] * c2[3] / 16384.0 for i in range(3)]
            if max(k0 + k1 + k2) <= 0:
                continue
            spans, y0, y1 = self._bbox((p0, p1, p2))
            ax, ay, az = n12
            bx, by, bz = n20
            cx, cy, cz = n01
            for y in range(y0, y1 + 1):
                sl, cl = -sinlat[y], coslat[y]
                ay_ = ay * sl
                by_ = by * sl
                cy_ = cy * sl
                row = y * w
                for xa, xb in spans:
                    for x in range(xa, xb + 1):
                        dx = cl * sinlon[x]
                        dz = cl * coslon[x]
                        b0 = ax * dx + ay_ + az * dz
                        if b0 < 0:
                            continue
                        b1 = bx * dx + by_ + bz * dz
                        if b1 < 0:
                            continue
                        b2 = cx * dx + cy_ + cz * dz
                        if b2 < 0:
                            continue
                        inv = 1.0 / (b0 + b1 + b2)
                        b0 *= inv
                        b1 *= inv
                        b2 *= inv
                        # bilinear REPEAT sample, texel centres at +0.5
                        fu = (b0 * u0 + b1 * u1 + b2 * u2) * tw - 0.5
                        fv = (b0 * t0 + b1 * t1 + b2 * t2) * th - 0.5
                        iu = math.floor(fu)
                        iv = math.floor(fv)
                        au = fu - iu
                        av = fv - iv
                        iu %= tw
                        iv %= th
                        ju = (iu + 1) % tw
                        jv = (iv + 1) % th
                        r0 = iv * tw
                        r1 = jv * tw
                        s00 = texels[r0 + iu]
                        s01 = texels[r0 + ju]
                        s10 = texels[r1 + iu]
                        s11 = texels[r1 + ju]
                        w00 = (1 - au) * (1 - av)
                        w01 = au * (1 - av)
                        w10 = (1 - au) * av
                        w11 = au * av
                        o = (row + x) * 3
                        for ch in range(3):
                            t = s00[ch] * w00 + s01[ch] * w01 + s10[ch] * w10 + s11[ch] * w11
                            buf[o + ch] += t * (b0 * k0[ch] + b1 * k1[ch] + b2 * k2[ch])

    def image(self):
        data = bytes(min(255, int(v + 0.5)) for v in self.buf)
        return Image.frombytes('RGB', (self.w, self.h), data)


# ---------------------------------------------------------------------------
# Panorama resampling: cubemap faces and perspective views
# ---------------------------------------------------------------------------


def sample_equirect(pano, px, w, h, d):
    """Bilinear sample of an equirect image (pixel access px) in direction d."""
    lon = math.atan2(d[0], d[2])
    lat = math.asin(max(-1.0, min(1.0, -d[1] / math.sqrt(_dot(d, d)))))
    fx = (lon / (2 * math.pi) + 0.5) * w - 0.5
    fy = (0.5 - lat / math.pi) * h - 0.5
    x0, y0 = math.floor(fx), math.floor(fy)
    ax, ay = fx - x0, fy - y0
    out = [0.0, 0.0, 0.0]
    for yy, wy in ((y0, 1 - ay), (y0 + 1, ay)):
        yy = min(h - 1, max(0, yy))
        for xx, wx in ((x0, 1 - ax), (x0 + 1, ax)):
            p = px[xx % w, yy]
            for c in range(3):
                out[c] += p[c] * wx * wy
    return tuple(int(v + 0.5) for v in out)


# Face -> (forward, right, up) in game coordinates (Y down).  Cube axes: +x =
# game +X, +y = game -Y (up), +z = game -Z.  Every face is the unmirrored
# view from inside the cube; the front face is nz (game +Z), the side faces
# have +y up, py has +z (back) at its top and ny has -z (front) at its top, so
# the faces join without seams in the usual cross (nx nz px pz in a row, py
# above nz, ny below nz).
CUBE_FACES = {
    'px': ((1, 0, 0), (0, 0, -1), (0, -1, 0)),
    'nx': ((-1, 0, 0), (0, 0, 1), (0, -1, 0)),
    'py': ((0, -1, 0), (1, 0, 0), (0, 0, -1)),
    'ny': ((0, 1, 0), (1, 0, 0), (0, 0, 1)),
    'pz': ((0, 0, -1), (-1, 0, 0), (0, -1, 0)),
    'nz': ((0, 0, 1), (1, 0, 0), (0, -1, 0)),
}


def perspective_view(pano, width, height, fov_v_deg, forward, right, up, aspect=None):
    """Pinhole view of an equirect panorama; aspect = display aspect ratio
    (defaults to width/height; the PS2 shows its 640x448 frame at 4:3)."""
    w, h = pano.size
    px = pano.load()
    out = Image.new('RGB', (width, height))
    op = out.load()
    tv = math.tan(math.radians(fov_v_deg) / 2)
    th = tv * (aspect or width / height)
    for y in range(height):
        sy = (1 - 2 * (y + 0.5) / height) * tv
        for x in range(width):
            sx = (2 * (x + 0.5) / width - 1) * th
            d = tuple(forward[i] + sx * right[i] + sy * up[i] for i in range(3))
            op[x, y] = sample_equirect(pano, px, w, h, d)
    return out


def camera_basis(yaw_deg=0.0, pitch_deg=0.0):
    """Camera looking along game +Z, turned by yaw (towards +X) and pitch (up)."""
    cy, sy = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    cp, sp = math.cos(math.radians(pitch_deg)), math.sin(math.radians(pitch_deg))
    forward = (sy * cp, -sp, cy * cp)
    right = (cy, 0.0, -sy)
    up = _cross(right, forward)
    return forward, right, up


# ---------------------------------------------------------------------------
# Catalogue of skies on a disc
# ---------------------------------------------------------------------------


def parse_ini(text: str) -> dict[str, dict[str, str]]:
    out, cur = {}, None
    for line in text.splitlines():
        line = line.split('//')[0].split(';')[0].strip()
        m = re.match(r'\[(\w+)\]', line)
        if m:
            cur = out.setdefault(m.group(1), {})
        elif cur is not None and '=' in line:
            k, v = line.split('=', 1)
            cur[k.strip()] = v.strip()
    return out


def bgmodel_table(disc: Disc, tree) -> list[dict]:
    """adv/bgmodel.tbl: MODELnn (file, BLEND, CAMERA, ROT_ANIM) + comments."""
    raw = all_dat_get(disc, tree, 'adv/bgmodel.tbl').decode('cp932', 'replace')
    comments = dict(re.findall(r'\[(MODEL\d+)\][ \t]*//[ \t]*(.*)', raw))
    ini = parse_ini(raw)
    models = []
    for sec, kv in ini.items():
        if sec.startswith('MODEL') and 'MODEL' in kv:
            models.append(dict(section=sec, file=kv['MODEL'], blend=int(kv.get('BLEND', '0')),
                               camera=int(kv.get('CAMERA', '0')), rot_anim=kv.get('ROT_ANIM'),
                               comment=comments.get(sec, '').strip()))
    cams = {sec: kv for sec, kv in ini.items() if sec.startswith('CAMERA')}
    return models, cams


class Sky:
    def __init__(self, name, models, textures, info):
        self.name, self.models, self.textures, self.info = name, models, textures, info


def find_skies(disc: Disc, verbose=False) -> list[Sky]:
    skies = []
    # ADV backgrounds (bgmodel.tbl, models + textures in ADV.DAT pack advinit).
    tree = read_all_dat(disc) if disc.has('ALL.DAT') else {}
    adv_packs = {p.name: p for p in list_packs(disc, 'ADV.DAT')}
    if 'adv/bgmodel.tbl' in tree and 'advinit' in adv_packs:
        pack = adv_packs['advinit']
        models, cams = bgmodel_table(disc, tree)
        groups = {}
        for m in models:
            if m['blend'] != 1 or not m['file'].startswith('common/model/'):
                continue  # BLEND 1 = additive space background; MODEL00 is the bridge monitor
            key = re.sub(r'\s*\d+\s*$', '', m['comment']) or m['section']
            groups.setdefault(key, []).append(m)
        seen = {}
        for key, ms in groups.items():
            files = tuple(dict.fromkeys(m['file'] for m in ms))
            if files in seen:
                seen[files]['uses'].append(key)
                continue
            name = 'adv_op_sbg_' + '+'.join(Path(f).stem.replace('op_sbg_', '') for f in files)
            loaded, textures = [], {}
            for f in files:
                if f not in pack.entries:
                    continue
                model = Model(pack.get(f), f)
                loaded.append(model)
                texdir = str(Path(f).parent)
                for t in model.textures:
                    p = f'{texdir}/{t}.agi'
                    if p in pack.entries and t not in textures:
                        textures[t] = (Texture(pack.get(p), t), f'ADV.DAT:{pack.name}:{p}')
            if not loaded:
                continue
            cam = cams.get('CAMERA%02d' % ms[0]['camera'], {})
            info = dict(kind='adv', uses=[key], tables=ms, camera=cam, blend='additive')
            seen[files] = info
            skies.append(Sky(name, loaded, textures, info))
    # Battle stages: SLG.DAT pack slginit<stage> holds slg/stage/<stage>/stage.tbl,
    # whose [BG] #000 (天球ファイル = celestial sphere) names an animation table
    # with TEXDIR and MODELFILE #000 = the sky model.  [GAS], [METEORITE] and
    # [BGOBJECT] place extra objects in the battle field; they are not sky.
    by_hash = {}
    for pack in list_packs(disc, 'SLG.DAT'):
        if not pack.name.startswith('slginit'):
            continue
        stage = pack.name[len('slginit'):]
        groups = []  # [[(model path, texdir), ...]]: the stage's [BG] tables
        stage_tbl = f'slg/stage/{stage}/stage.tbl'
        if stage_tbl in pack.entries:
            bg = parse_ini(pack.get(stage_tbl).decode('cp932', 'replace')).get('BG', {})
            for key in sorted(k for k in bg if k.startswith('#')):
                if bg[key] in pack.entries:
                    ini = parse_ini(pack.get(bg[key]).decode('cp932', 'replace'))
                    mf = ini.get('MODELFILE', {})
                    texdir = ini.get('HEADER', {}).get('TEXDIR', str(Path(bg[key]).parent))
                    models_of_tbl = [(mf[mk], texdir) for mk in sorted(k for k in mf if k.startswith('#'))]
                    # every [BG] table of a stage is drawn: one sky per stage
                    groups = [(groups[0] if groups else []) + models_of_tbl]
        else:  # no stage table: fall back to the naming convention
            for path in sorted(pack.entries):
                if path.endswith('.axo') and re.match(r'si_sbg_\d+$', Path(path).stem):
                    groups.append([(path, str(Path(path).parent))])
        for group in groups:
            group = [(path, texdir) for path, texdir in group if path in pack.entries]
            if not group:
                continue
            models, textures = [], {}
            h = hashlib.sha1()
            for path, texdir in group:
                data = pack.get(path)
                h.update(data)
                model = Model(data, path)
                models.append(model)
                for t in model.textures:
                    # TEXDIR first; some stages reuse another stage's table
                    # whose TEXDIR is absent from this pack, so fall back to
                    # the model's own directory.
                    for p in (f'{texdir}/{t}.agi', f'{Path(path).parent}/{t}.agi'):
                        if p in pack.entries and t not in textures:
                            blob = pack.get(p)
                            h.update(blob)
                            textures[t] = (Texture(blob, t), f'SLG.DAT:{pack.name}:{p}')
            key = h.hexdigest()
            if key in by_hash:
                by_hash[key].info['stages'].append(stage)
                continue
            name = f'slg_{Path(group[0][0]).stem}_{stage}'
            sky = Sky(name, models, textures, dict(kind='slg', stages=[stage], models=[g[0] for g in group],
                                                   blend='additive'))
            by_hash[key] = sky
            skies.append(sky)
    return skies


def space_backdrops(disc: Disc):
    """2D ADV backgrounds listed as empty space in adv/bg.tbl."""
    tree = read_all_dat(disc)
    if 'adv/bg.tbl' not in tree:
        return []
    raw = all_dat_get(disc, tree, 'adv/bg.tbl').decode('cp932', 'replace')
    out = []
    section = raw.split('[BG]', 1)[-1]
    base = re.search(r'BASEDIR\s*=\s*(\S+)', section)
    base = base.group(1) if base else 'adv/bg/'
    for m in re.finditer(r'#(\d+)\s*=\s*(\S+\.agi)\s*;\s*(.*)', section.split('\n[')[0]):
        idx, fname, comment = m.groups()
        # 何もない宇宙 = "empty space".  Zettai's 汎用宇宙空間 ("generic space",
        # iv01_00a) is a placeholder with its caption drawn into the picture.
        if re.search(r'何もない宇宙', comment):
            for path in (base + fname, 'adv/bg/' + fname):
                if path in tree:
                    out.append((fname, comment.strip(), all_dat_get(disc, tree, path)))
                    break
    return out


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def render_sky(sky: Sky, width: int, order=ROT_ORDER, log=None):
    canvas = Canvas(width)
    layers = []
    for model in sky.models:
        for i, (tex, tris) in enumerate(model.layers(order)):
            if tex not in sky.textures:
                if log:
                    log(f'  {sky.name}: texture {tex} missing, layer skipped')
                continue
            canvas.draw(tris, sky.textures[tex][0])
            rads = [math.sqrt(_dot(v[0], v[0])) for t in tris for v in t]
            layers.append(dict(model=model.name, atom=i, texture=tex, triangles=len(tris),
                               radius=[round(min(rads)), round(max(rads))] if rads else None))
    return canvas.image(), layers


def write_sky(sky: Sky, out: Path, width: int, cube: int, log):
    d = out / sky.name
    d.mkdir(parents=True, exist_ok=True)
    pano, layers = render_sky(sky, width, log=log)
    pano.save(d / f'{sky.name}_equirect.png')
    faces = {}
    if cube:
        for face, (f, r, u) in CUBE_FACES.items():
            img = perspective_view(pano, cube, cube, 90.0, f, r, u)
            img.save(d / f'{sky.name}_cube_{face}.png')
            faces[face] = f'{sky.name}_cube_{face}.png'
    meta = dict(name=sky.name, info=sky.info, layers=layers,
                textures={k: dict(source=v[1], width=v[0].w, height=v[0].h, psm=v[0].psm,
                                  png=f'../textures/{Path(v[1].split(":")[-1]).parent.name}/{k}.png')
                          for k, v in sky.textures.items()},
                equirect=f'{sky.name}_equirect.png', cube=faces,
                mapping='equirect: u=0.5 -> game +Z, u=0.75 -> +X, top -> game -Y (up); '
                        'cube: OpenGL axes, +x=game +X, +y=game -Y, +z=game -Z',
                rotation_order=ROT_ORDER)
    (d / f'{sky.name}.json').write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    for k, (tex, src) in sky.textures.items():
        tdir = out / 'textures' / Path(src.split(':')[-1]).parent.name
        tdir.mkdir(parents=True, exist_ok=True)
        tex.image().save(tdir / f'{k}.png')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('source', type=Path, help='disc ISO image or directory with the disc files (ALL.DAT, ADV.DAT, SLG.DAT, ...)')
    ap.add_argument('-o', '--out', type=Path, default=Path('skybox-out'), help='output directory')
    ap.add_argument('--list', action='store_true', help='only list the skies found')
    ap.add_argument('--sky', action='append', default=[], help='only skies whose name contains this (repeatable)')
    ap.add_argument('--width', type=int, default=2048, help='equirect width (height = width/2), default 2048')
    ap.add_argument('--cube', type=int, default=512, help='cube face size, 0 = no cubemap (default 512)')
    ap.add_argument('--no-backdrops', action='store_true', help='skip the 2D space backdrops')
    ap.add_argument('--view', nargs=5, metavar=('SKY', 'YAW', 'PITCH', 'FOVV', 'OUT'),
                    help='reproject an existing equirect PNG (SKY = its path) to a 640x448 4:3 game camera view: '
                         'YAW towards +X and PITCH up in degrees from game +Z, FOVV = vertical field of view')
    args = ap.parse_args(argv)
    if Image is None:
        raise SystemExit('Pillow is required (pip install Pillow)')
    if args.view:
        pano = Image.open(args.view[0]).convert('RGB')
        f, r, u = camera_basis(float(args.view[1]), float(args.view[2]))
        perspective_view(pano, 640, 448, float(args.view[3]), f, r, u, aspect=4 / 3).save(args.view[4])
        return 0
    disc = Disc(args.source)
    log = lambda s: print(s, file=sys.stderr)
    skies = find_skies(disc)
    if args.sky:
        skies = [s for s in skies if any(k in s.name for k in args.sky)]
    for s in skies:
        extra = s.info.get('stages') or s.info.get('uses')
        print(f'{s.name}: {len(s.models)} model(s), textures {", ".join(s.textures)}; {extra}')
    if args.list:
        return 0
    out = args.out / (disc.program or args.source.stem)
    out.mkdir(parents=True, exist_ok=True)
    for s in skies:
        log(f'rendering {s.name} ...')
        write_sky(s, out, args.width, args.cube, log)
    if not args.no_backdrops:
        bd = out / 'backdrops'
        for fname, comment, data in space_backdrops(disc):
            bd.mkdir(parents=True, exist_ok=True)
            try:
                Texture(data, fname).image().convert('RGB').save(bd / (Path(fname).stem + '.png'))
                print(f'backdrop {fname}: {comment}')
            except ValueError as e:
                log(f'backdrop {fname}: {e}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
