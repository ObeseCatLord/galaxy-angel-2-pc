#!/usr/bin/env python3
"""Message lookup for lines a save brings back in the language it was made in.

A save keeps the message that was on screen (its Shift-JIS bytes), and loading
it shows that text again, not the script's: a save made in Japanese shows its
first line in Japanese under a language pack (and the reverse).  This writes,
for a built language pack, ``<pack>/text/<program>.msg``: every message string
of the pack's scripts that differs from the disc's, as (original bytes, pack
bytes).  The engine (``native/compat/ga2_text.cpp``) swaps such a text for
its translation when the game lays out a message.

    python3 tools/modding/restore_text.py <private> <pack-dir> [--discs ...]

File format: ``GA2MSG1\\0``, u32 count, then per entry u16 length + original
bytes, u16 length + translated bytes (little endian).  Needs only the
standard library (reads the pack like ``audit_pack.py``).
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ga2arc  # noqa: E402
import ga2asb  # noqa: E402
from audit_pack import DISCS, SegReader  # noqa: E402


def message_bytes(data: bytes) -> dict:
    """string index -> bytes of every message (`msg`) string of a script."""
    sc = ga2asb.parse(data)
    used = set()
    for f in sc.funcs:
        for pc, _op, a in f.ins:
            if sc.string_refs.get(pc) == 0x00:
                used.add(struct.unpack("<i", a)[0])
    index = {o: i for i, (o, _) in enumerate(sc.strings)}
    return {index[o]: b for o, b in sc.strings if o in used and o in index}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("private", type=Path)
    ap.add_argument("pack", type=Path)
    ap.add_argument("--discs", nargs="*", default=list(DISCS))
    a = ap.parse_args(argv)
    (a.pack / "text").mkdir(exist_ok=True)
    for disc in a.discs:
        iso = next((a.private / "discs" / disc).glob("*.iso"))
        src = ga2arc.DiscSource(iso)
        prog = src.program()
        d_idx = ga2arc.Pidx.parse(src.read("IDX.DAT"))
        d_files = d_idx.files()
        p_idx = ga2arc.Pidx.parse((a.pack / "files" / prog / "IDX.DAT").read_bytes())
        strm = SegReader(a.pack / "files" / prog / "STRM.DAT.ga2seg", iso)
        table: dict = {}
        clashes = 0
        for path, prec in sorted(p_idx.files().items()):
            if not (path.startswith("adv/scn/") and path.endswith(".asb")):
                continue
            d = d_files.get(path)
            if d is None or (d.a, d.b, d.size, d.stored) == (prec.a, prec.b, prec.size, prec.stored):
                continue
            arch = p_idx.string(prec.a).lower()
            blob = strm.read(prec.b, prec.stored) if arch == "strm.dat" else src.read(arch.upper(), prec.b, prec.stored)
            new = message_bytes(ga2arc.raic_decode(blob))
            old = message_bytes(ga2arc.raic_decode(src.read(d_idx.string(d.a).upper(), d.b, d.stored)))
            for i, ob in old.items():
                nb = new.get(i)
                if nb is None or nb == ob or len(ob) > 0xFFFF or len(nb) > 0xFFFF:
                    continue
                if ob in table and table[ob] != nb:
                    clashes += 1   # the same Japanese line translated two ways: the first one wins
                    continue
                table.setdefault(ob, nb)
        out = bytearray(b"GA2MSG1\0" + struct.pack("<I", len(table)))
        for ob, nb in table.items():
            out += struct.pack("<H", len(ob)) + ob + struct.pack("<H", len(nb)) + nb
        (a.pack / "text" / f"{prog}.msg").write_bytes(bytes(out))
        print(f"{disc}: {len(table)} message(s), {clashes} translated differently elsewhere (first kept) -> "
              f"{a.pack / 'text' / (prog + '.msg')}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
