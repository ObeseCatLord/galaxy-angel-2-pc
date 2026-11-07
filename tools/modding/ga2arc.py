#!/usr/bin/env python3
"""Galaxy Angel II (PS2) disc archive formats: ISO9660 root files, PIDX0
indexes (IDX.DAT, ALL.DAT, ADV.DAT, ...), FSTS packs and RAIC compression.

Standard library only.  See docs/modding/scripts.md for the format notes.

PIDX0 header (little endian, 0x50 bytes; every archive and IDX.DAT):
  0x00 "PIDX0\\0\\0\\0"
  0x08 u32 archive_count   (1 in an archive, 7 in IDX.DAT)
  0x0c u32 records_offset  (24-byte tree records; 0x50 in archives)
  0x10 u32 record_count    (tree archives: ALL.DAT, IDX.DAT; 0 in flat ones)
  0x14 u32 root_children   (records [0, root_children) are the root)
  0x18 u32 group_offset    (flat "group" table: u32 n, n u32 offsets to
                            20-byte entries {name, 0, offset, size, files})
  0x1c u32 group_size
  0x20 u32 strings_offset  (NUL-terminated names; archive names first)
  0x24 u32 strings_size
  0x30 32-byte archive descriptors: u32 name, 12 zero bytes, 16-byte id
       (IDX.DAT has 7, starting at 0x30; an archive has its own at 0x30)

Tree record (24 bytes): directory {1, name, child_count, first_child, 0, 0};
file {0, name, archive_name, offset, size, stored_size}.  In an archive the
archive_name field is 0; in IDX.DAT it names the archive (e.g. "all.dat").
Offsets are bytes from the start of the archive file, 2048-aligned.

Stored file ("RAIC" blob): 4 bytes "RAIC" ^ 0x72 (b" 3;" + b"1" LZSS or
b"0" stored), u32 size, then the payload XOR 0x72.  LZSS: 4096-byte ring
(zero filled, write position 4078), flag byte LSB first, 1 = literal,
0 = two bytes: offset = b0 | (b1 & 0xf0) << 4, length = (b1 & 0x0f) + 3.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

SECTOR = 2048
XOR = 0x72


# ---------------------------------------------------------------- ISO 9660

class Iso:
    """Reads root-directory files of a PS2 ISO9660 image (GA2 keeps all
    archives in the root)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.f = open(self.path, "rb")
        pvd = self._read(16 * SECTOR, SECTOR)
        if pvd[1:6] != b"CD001" or pvd[0] != 1:
            raise ValueError(f"{path}: not an ISO9660 image")
        root = pvd[156:156 + 34]
        lba, size = struct.unpack_from("<I", root, 2)[0], struct.unpack_from("<I", root, 10)[0]
        self.files: dict[str, tuple[int, int]] = {}
        data = self._read(lba * SECTOR, size)
        pos = 0
        while pos < len(data):
            n = data[pos]
            if n == 0:
                pos = (pos // SECTOR + 1) * SECTOR
                continue
            rec = data[pos:pos + n]
            flen = rec[32]
            name = rec[33:33 + flen].decode("ascii", "replace")
            if not (rec[25] & 2) and name not in ("\0", "\1"):
                name = name.split(";")[0]
                self.files[name.upper()] = (struct.unpack_from("<I", rec, 2)[0],
                                            struct.unpack_from("<I", rec, 10)[0])
            pos += n

    def _read(self, off: int, n: int) -> bytes:
        self.f.seek(off)
        return self.f.read(n)

    def size(self, name: str) -> int:
        return self.files[name.upper()][1]

    def read(self, name: str, off: int = 0, n: int | None = None) -> bytes:
        lba, size = self.files[name.upper()]
        if n is None:
            n = size - off
        return self._read(lba * SECTOR + off, n)

    def program(self) -> str:
        """Boot program name, e.g. SLPM_662.43 (the engine's mods folder)."""
        cnf = self.read("SYSTEM.CNF").decode("ascii", "replace")
        for line in cnf.splitlines():
            if line.strip().upper().startswith("BOOT2"):
                return line.split("\\")[-1].split(";")[0].strip()
        raise ValueError("no BOOT2 line in SYSTEM.CNF")


class DiscSource:
    """A disc file source: an ISO image or a directory of extracted files."""

    def __init__(self, path: str | Path):
        p = Path(path)
        self.iso = Iso(p) if p.is_file() else None
        self.dir = None if self.iso else p

    def read(self, name: str, off: int = 0, n: int | None = None) -> bytes:
        if self.iso:
            return self.iso.read(name, off, n)
        with open(self.dir / name, "rb") as f:
            f.seek(off)
            return f.read() if n is None else f.read(n)

    def size(self, name: str) -> int:
        if self.iso:
            return self.iso.size(name)
        return (self.dir / name).stat().st_size

    def program(self) -> str | None:
        try:
            if self.iso:
                return self.iso.program()
            cnf = (self.dir / "SYSTEM.CNF").read_text("ascii", "replace")
            for line in cnf.splitlines():
                if line.strip().upper().startswith("BOOT2"):
                    return line.split("\\")[-1].split(";")[0].strip()
        except (KeyError, FileNotFoundError):
            pass
        return None

    def image_offset(self, name: str) -> int | None:
        """Byte offset of a file in the ISO image (None for a directory)."""
        return self.iso.files[name.upper()][0] * SECTOR if self.iso else None

    def copy_to(self, name: str, dst: Path, chunk: int = 16 << 20) -> None:
        size = self.size(name)
        with open(dst, "wb") as out:
            off = 0
            while off < size:
                b = self.read(name, off, min(chunk, size - off))
                out.write(b)
                off += len(b)


# ---------------------------------------------------------------- RAIC/LZSS

def lzss_decode(src: bytes, size: int) -> bytes:
    ring = bytearray(4096)
    r = 4078
    out = bytearray()
    i = 0
    n = len(src)
    flags = 0
    while len(out) < size and i < n:
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
            ring[r] = c
            r = (r + 1) & 4095
        else:
            a, b = src[i], src[i + 1]
            i += 2
            pos = a | ((b & 0xF0) << 4)
            for k in range((b & 0x0F) + 3):
                c = ring[(pos + k) & 4095]
                out.append(c)
                ring[r] = c
                r = (r + 1) & 4095
    if len(out) != size:
        raise ValueError(f"LZSS stream ended at {len(out)} of {size} bytes")
    return bytes(out)


def lzss_encode(data: bytes) -> bytes:
    """Haruhiko Okumura's LZSS encoder (binary search tree, N=4096, F=18,
    THRESHOLD=2) with a zero-filled ring.  This reproduces the stored
    streams on the GA2 discs byte for byte."""
    N, F, TH, NIL = 4096, 18, 2, 4096
    text = bytearray(N + F - 1)
    lson = [NIL] * (N + 1)
    rson = [NIL] * (N + 257)
    dad = [NIL] * (N + 1)
    ml = mp = 0

    def insert(r: int) -> None:
        nonlocal ml, mp
        cmp = 1
        p = N + 1 + text[r]
        rson[r] = lson[r] = NIL
        ml = 0
        while True:
            if cmp >= 0:
                if rson[p] != NIL:
                    p = rson[p]
                else:
                    rson[p] = r
                    dad[r] = p
                    return
            else:
                if lson[p] != NIL:
                    p = lson[p]
                else:
                    lson[p] = r
                    dad[r] = p
                    return
            i = 1
            while i < F:
                cmp = text[r + i] - text[p + i]
                if cmp:
                    break
                i += 1
            if i > ml:
                mp, ml = p, i
                if ml >= F:
                    break
        dad[r], lson[r], rson[r] = dad[p], lson[p], rson[p]
        dad[lson[p]] = r
        dad[rson[p]] = r
        if rson[dad[p]] == p:
            rson[dad[p]] = r
        else:
            lson[dad[p]] = r
        dad[p] = NIL

    def delete(p: int) -> None:
        if dad[p] == NIL:
            return
        if rson[p] == NIL:
            q = lson[p]
        elif lson[p] == NIL:
            q = rson[p]
        else:
            q = lson[p]
            if rson[q] != NIL:
                while rson[q] != NIL:
                    q = rson[q]
                rson[dad[q]] = lson[q]
                dad[lson[q]] = dad[q]
                lson[q] = lson[p]
                dad[lson[p]] = q
            rson[q] = rson[p]
            dad[rson[p]] = q
        dad[q] = dad[p]
        if rson[dad[p]] == p:
            rson[dad[p]] = q
        else:
            lson[dad[p]] = q
        dad[p] = NIL

    out = bytearray()
    code = bytearray([0])
    mask = 1
    s, r = 0, N - F
    pos, n, ln = 0, len(data), 0
    while ln < F and pos < n:
        text[r + ln] = data[pos]
        pos += 1
        ln += 1
    if ln == 0:
        return b""
    for i in range(1, F + 1):
        insert(r - i)
    insert(r)
    while True:
        if ml > ln:
            ml = ln
        if ml <= TH:
            ml = 1
            code[0] |= mask
            code.append(text[r])
        else:
            code.append(mp & 0xFF)
            code.append(((mp >> 4) & 0xF0) | (ml - (TH + 1)))
        mask = (mask << 1) & 0xFF
        if mask == 0:
            out += code
            code = bytearray([0])
            mask = 1
        last, i = ml, 0
        while i < last and pos < n:
            c = data[pos]
            pos += 1
            delete(s)
            text[s] = c
            if s < F - 1:
                text[s + N] = c
            s = (s + 1) & (N - 1)
            r = (r + 1) & (N - 1)
            insert(r)
            i += 1
        while i < last:
            i += 1
            delete(s)
            s = (s + 1) & (N - 1)
            r = (r + 1) & (N - 1)
            ln -= 1
            if ln:
                insert(r)
        if ln <= 0:
            break
    if len(code) > 1:
        out += code
    return bytes(out)


def raic_decode(blob: bytes) -> bytes:
    magic = bytes(c ^ XOR for c in blob[:4])
    if magic[:3] != b"RAI":
        raise ValueError(f"not a RAIC blob: {blob[:4]!r}")
    size = struct.unpack_from("<I", blob, 4)[0]
    payload = bytes(c ^ XOR for c in blob[8:])
    if magic == b"RAIC":
        return lzss_decode(payload, size)
    if magic == b"RAIB":
        return payload[:size]
    raise ValueError(f"unknown RAIC method {magic!r}")


def raic_encode(data: bytes, compress: bool = True) -> bytes:
    if compress:
        payload, magic = lzss_encode(data), b"RAIC"
    else:
        payload, magic = data, b"RAIB"
    return (bytes(c ^ XOR for c in magic) + struct.pack("<I", len(data))
            + bytes(c ^ XOR for c in payload))


# ---------------------------------------------------------------- PIDX0

@dataclass
class Record:
    index: int
    kind: int          # 0 file, 1 directory
    name: int          # string offset
    a: int             # file: archive name offset (IDX.DAT); dir: child count
    b: int             # file: offset in archive; dir: first child index
    size: int          # file: unpacked size
    stored: int        # file: stored (RAIC blob) size
    path: str = ""


@dataclass
class Pidx:
    data: bytearray
    records: list[Record] = field(default_factory=list)

    @classmethod
    def parse(cls, data: bytes) -> "Pidx":
        if data[:5] != b"PIDX0":
            raise ValueError("not a PIDX0 file")
        p = cls(bytearray(data))
        n = p.u32(0x10)
        ro = p.u32(0x0C)
        for i in range(n):
            kind, name, a, b, size, stored = struct.unpack_from("<6I", data, ro + 24 * i)
            p.records.append(Record(i, kind, name, a, b, size, stored))

        def walk(first: int, count: int, prefix: str, depth: int) -> None:
            if depth > 32:
                raise ValueError("PIDX tree too deep")
            for i in range(first, first + count):
                r = p.records[i]
                r.path = prefix + p.string(r.name)
                if r.kind == 1:
                    walk(r.b, r.a, r.path + "/", depth + 1)

        if n:
            walk(0, p.u32(0x14), "", 0)
        return p

    def u32(self, off: int) -> int:
        return struct.unpack_from("<I", self.data, off)[0]

    def string(self, off: int) -> str:
        so = self.u32(0x20)
        end = self.data.index(b"\0", so + off)
        return self.data[so + off:end].decode("ascii", "replace")

    def files(self) -> dict[str, Record]:
        return {r.path: r for r in self.records if r.kind == 0}

    def archive_names(self) -> list[str]:
        return [self.string(self.u32(0x30 + 32 * i)) for i in range(self.u32(0x08))]

    def set_file(self, rec: Record, offset: int, size: int, stored: int,
                 archive: int | None = None) -> None:
        """Point a file record at new data (and optionally another archive,
        by the archive name's string offset)."""
        if archive is not None:
            rec.a = archive
        rec.b, rec.size, rec.stored = offset, size, stored
        struct.pack_into("<4I", self.data, self.u32(0x0C) + 24 * rec.index + 8,
                         rec.a, offset, size, stored)

    def find_string(self, s: str) -> int | None:
        so, ss = self.u32(0x20), self.u32(0x24)
        key = s.encode("ascii") + b"\0"
        pos = 0
        while True:
            i = self.data.find(key, so + pos, so + ss)
            if i < 0:
                return None
            if i == so or self.data[i - 1] == 0:
                return i - so
            pos = i - so + 1

    def insert_file(self, path: str, archive: int, offset: int, size: int, stored: int,
                    limit: int | None = None) -> Record:
        """Add a file record.  The game looks names up by binary search over
        each directory's contiguous block of records (FUN_0010a848 in
        SLPM_662.43, byte order, lower case), so the record goes to its
        sorted place in the parent's block; every later block moves down one
        record.  The record table, group table and string table are rebuilt
        in place; `limit` is the byte size the header may not exceed (an
        archive's first data offset)."""
        if path in self.files():
            raise ValueError(f"{path} already exists")
        parent_path, _, name = path.rpartition("/")
        if name != name.lower() or not name:
            raise ValueError("file names must be lower case")
        if parent_path:
            dirs = [r for r in self.records if r.kind == 1 and r.path == parent_path]
            if not dirs:
                raise ValueError(f"directory {parent_path} does not exist")
            parent = dirs[0]
            first, count = parent.b, parent.a
        else:
            parent = None
            first, count = 0, self.u32(0x14)
        names = [self.string(self.records[i].name) for i in range(first, first + count)]
        pos = first + sum(1 for n in names if n.encode() < name.encode())
        ro, n = self.u32(0x0C), self.u32(0x10)
        go, gs, so, ss = self.u32(0x18), self.u32(0x1C), self.u32(0x20), self.u32(0x24)
        tail_start = ro + 24 * n
        if not (tail_start <= go or gs == 0) or so < tail_start:
            raise ValueError("unexpected PIDX layout (tables before the records)")
        name_off = self.find_string(name)
        strings = bytes(self.data[so:so + ss])
        if name_off is None:
            name_off = ss
            strings += name.encode("ascii") + b"\0"
        recs = []
        for r in self.records:
            kind, nm, a, b, sz, st = r.kind, r.name, r.a, r.b, r.size, r.stored
            if kind == 1:
                if r is parent:
                    a += 1
                elif b >= pos:
                    b += 1
            recs.append((kind, nm, a, b, sz, st))
        recs.insert(pos, (0, name_off, archive, offset, size, stored))
        head = bytearray(self.data[:ro])
        table = b"".join(struct.pack("<6I", *r) for r in recs)
        mid = bytes(self.data[tail_start:so])  # group table and padding
        out = head + table + mid + strings
        shift = 24
        struct.pack_into("<I", out, 0x10, n + 1)
        if parent is None:
            struct.pack_into("<I", out, 0x14, self.u32(0x14) + 1)
        if gs:
            struct.pack_into("<I", out, 0x18, go + shift)
        struct.pack_into("<II", out, 0x20, so + shift, len(strings))
        if any(self.data[so + ss:]):
            raise ValueError("unexpected data after the string table")
        if limit is not None and len(out) > limit:
            raise ValueError("no room left in the archive header")
        new = Pidx.parse(bytes(out))
        self.data, self.records = new.data, new.records
        return self.files()[path]

    def group_entries(self) -> list[tuple[int, str, int, int, int, int]]:
        """Group table with positions: (entry offset, name, archive name
        offset, offset, size, files).  In IDX.DAT the archive field names the
        archive holding the pack; in an archive's own header it is 0."""
        go = self.u32(0x18)
        if not go or not self.u32(0x1C):
            return []
        out = []
        for k in range(self.u32(go)):
            o = go + self.u32(go + 4 + 4 * k)
            name, arc, off, size, nfiles = struct.unpack_from("<5I", self.data, o)
            out.append((o, self.string(name), arc, off, size, nfiles))
        return out

    def set_group(self, entry_offset: int, archive: int, offset: int, size: int) -> None:
        """Point a group (from group_entries) at a pack elsewhere."""
        struct.pack_into("<3I", self.data, entry_offset + 4, archive, offset, size)

    def groups(self) -> list[tuple[str, int, int, int]]:
        """Flat group table (FSTS packs): (name, offset, size, files)."""
        go = self.u32(0x18)
        if not go or not self.u32(0x1C):
            return []
        out = []
        for k in range(self.u32(go)):
            o = self.u32(go + 4 + 4 * k)
            name, _z, off, size, nfiles = struct.unpack_from("<5I", self.data, go + o)
            out.append((self.string(name), off, size, nfiles))
        return out


def fsts_replace(pack: bytes, replacements: dict) -> bytes:
    """The FSTS pack with the files in `replacements` (path -> bytes)
    replaced.  Names and entry order stay; the data is laid out again in
    the original order, 16-byte aligned, compressed where the original
    entry was."""
    entries = fsts_entries(pack)
    count, eo = struct.unpack_from("<2I", pack, 4)
    start = min(off for _, off, _, _ in entries) if entries else len(pack)
    out = bytearray(pack[:start])
    for i in sorted(range(count), key=lambda k: entries[k][1]):
        path, off, size, stored = entries[i]
        if path in replacements:
            data = replacements[path]
            blob = raic_encode(data) if stored else data
            size, stored = len(data), (len(blob) if stored else 0)
        else:
            blob = pack[off:off + (stored or size)]
        out += b"\0" * ((-len(out)) % 16)
        struct.pack_into("<3I", out, eo + 16 * i + 4, len(out), size, stored)
        out += blob
    out += b"\0" * ((-len(out)) % 16)
    return bytes(out)


def fsts_append(pack: bytes, replacements: dict) -> tuple[bytes, bytes]:
    """Like fsts_replace, but every file keeps its place: the replaced files
    are appended after the pack and their entries point there.  Returns
    (head, tail): the new pack is head + pack[len(head):] + tail, so the
    unchanged data can stay where it is."""
    entries = fsts_entries(pack)
    _count, eo = struct.unpack_from("<2I", pack, 4)
    start = min(off for _, off, _, _ in entries) if entries else len(pack)
    head = bytearray(pack[:start])
    tail = bytearray()
    base = len(pack) + (-len(pack)) % 16
    for i, (path, _off, _size, stored) in enumerate(entries):
        if path not in replacements:
            continue
        data = replacements[path]
        blob = raic_encode(data) if stored else data
        tail += b"\0" * ((-len(tail)) % 16)
        struct.pack_into("<3I", head, eo + 16 * i + 4, base + len(tail), len(data), len(blob) if stored else 0)
        tail += blob
    if tail:
        tail = bytearray(b"\0" * (base - len(pack))) + tail
        tail += b"\0" * ((-len(tail)) % 16)
    return bytes(head), bytes(tail)


def fsts_entries(pack: bytes) -> list[tuple[str, int, int, int]]:
    """Entries of an FSTS pack: (path, offset, size, stored_size)."""
    if pack[:4] != b"FSTS":
        raise ValueError("not an FSTS pack")
    count, eo, so, _ss = struct.unpack_from("<4I", pack, 4)
    out = []
    for i in range(count):
        name, off, size, stored = struct.unpack_from("<4I", pack, eo + 16 * i)
        end = pack.index(b"\0", so + name)
        out.append((pack[so + name:end].decode("ascii", "replace"), off, size, stored))
    return out
