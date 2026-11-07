#!/usr/bin/env python3
"""Audit a built language pack: which story rows still show Japanese?

  audit_pack.py <private> <pack-dir> [--out DIR] [--xlsx ga2_script.xlsx]
                [--discs zettai mugen-1 ...]

Reads the pack exactly as the engine does (files/<SLPM>/IDX.DAT plus the
STRM.DAT.ga2seg pieces), decodes every adv/scn/*.asb the pack's index
resolves, and lists each message / menu / choice string that still contains
Japanese (hiragana, katakana, kanji, halfwidth kana).  Each such row is
`unchanged` when the pack's text equals the disc's own, else `residue` (the
translation itself holds Japanese characters, e.g. developer test scripts).
With --xlsx each row is also classified against the workbook:
  untranslated  the English cell is empty or <omitted>
  lost          the workbook has English but the shipped script lacks it
  no-row        the workbook has no row for the string
Also reports the scripts whose index entry still equals the disc's.
Writes <out>/audit_<disc>.tsv (ID, kind, change, workbook class, text) and
audit_summary.json.
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ga2arc  # noqa: E402
import ga2asb  # noqa: E402

DISCS = ("zettai", "mugen-1", "mugen-2", "eigou-1", "eigou-2")
JA = re.compile("[぀-ヿ㐀-鿿ｦ-ﾟ]")
KINDS = {0x00: "msg", 0x01: "menu", 0x02: "choice"}


class SegReader:
    """Random access to NAME.ga2seg (see ps2_disc_mount.h)."""

    def __init__(self, seg: Path, iso: Path):
        d = seg.read_bytes()
        if d[:8] != b"GA2SEG1\0":
            raise ValueError(f"{seg}: not a ga2seg file")
        n = struct.unpack_from("<I", d, 8)[0]
        self.data = d
        self.iso = open(iso, "rb")
        self.pieces = []
        pos = 0
        for i in range(n):
            kind, _, off, length = struct.unpack_from("<IIQQ", d, 16 + 24 * i)
            self.pieces.append((pos, kind, off, length))
            pos += length
        self.size = pos

    def read(self, off: int, n: int) -> bytes:
        out = bytearray()
        end = off + n
        for pos, kind, src, length in self.pieces:
            if pos + length <= off or pos >= end:
                continue
            a, b = max(off, pos) - pos, min(end, pos + length) - pos
            if kind == 0:
                out += self.data[src + a:src + b]
            else:
                self.iso.seek(src + a)
                out += self.iso.read(b - a)
        return bytes(out)


def script_rows(data: bytes) -> dict:
    """string index -> (kind, text) for every message/menu/choice string."""
    sc = ga2asb.parse(data)
    used = {}
    for f in sc.funcs:
        for pc, _op, a in f.ins:
            bid = sc.string_refs.get(pc)
            if bid is not None:
                used.setdefault(struct.unpack("<i", a)[0], KINDS[bid])
    index = {o: i for i, (o, _) in enumerate(sc.strings)}
    texts = dict(sc.strings)
    return {index[o]: (k, ga2asb.sjis_to_text(texts[o])) for o, k in used.items() if o in index}


def workbook_english(xlsx: Path) -> dict:
    import script_xlsx
    sheets = script_xlsx.read_xlsx(xlsx)
    return {rid: eng for _s, _rn, rid, eng, _src, _k in script_xlsx.workbook_rows(sheets, xlsx)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("private")
    ap.add_argument("pack")
    ap.add_argument("--out", default=".")
    ap.add_argument("--xlsx")
    ap.add_argument("--discs", nargs="*", default=list(DISCS))
    args = ap.parse_args(argv)
    private, pack, out = Path(args.private), Path(args.pack), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    english = workbook_english(Path(args.xlsx)) if args.xlsx else None
    omitted = "<omitted>"
    summary = {}
    for disc in args.discs:
        iso = next((private / "discs" / disc).glob("*.iso"))
        src = ga2arc.DiscSource(iso)
        prog = src.program()
        d_idx = ga2arc.Pidx.parse(src.read("IDX.DAT"))
        d_files = d_idx.files()
        p_idx = ga2arc.Pidx.parse((pack / "files" / prog / "IDX.DAT").read_bytes())
        strm = SegReader(pack / "files" / prog / "STRM.DAT.ga2seg", iso)
        scripts = replaced = n_rows = 0
        ja_rows = []
        for path, prec in sorted(p_idx.files().items()):
            if not (path.startswith("adv/scn/") and path.endswith(".asb")):
                continue
            scripts += 1
            d = d_files.get(path)
            if d is None or (d.a, d.b, d.size, d.stored) != (prec.a, prec.b, prec.size, prec.stored):
                replaced += 1
            arch = p_idx.string(prec.a).lower()
            blob = strm.read(prec.b, prec.stored) if arch == "strm.dat" else src.read(arch.upper(), prec.b, prec.stored)
            rows = script_rows(ga2arc.raic_decode(blob))
            n_rows += len(rows)
            orig = None
            for i, (kind, text) in sorted(rows.items()):
                if JA.search(text):
                    if orig is None:
                        orig = script_rows(ga2arc.raic_decode(src.read(
                            d_idx.string(d.a).upper(), d.b, d.stored))) if d else {}
                    same = orig.get(i, (None, None))[1] == text
                    ja_rows.append((f"{disc}:{path[8:-4]}:s{i}", kind, "unchanged" if same else "residue", text))
        counts = {}
        with open(out / f"audit_{disc}.tsv", "w", encoding="utf-8") as f:
            for rid, kind, change, text in ja_rows:
                cls = "-"
                if english is not None:
                    e = english.get(rid)
                    cls = "no-row" if e is None else ("untranslated" if not e.strip() or e.strip() == omitted else "lost")
                key = f"{change}/{cls}"
                counts[key] = counts.get(key, 0) + 1
                f.write(f"{rid}\t{kind}\t{change}\t{cls}\t{text}\n")
        summary[disc] = {"scripts": scripts, "scripts_replaced_by_pack": replaced, "rows": n_rows,
                         "japanese_rows": len(ja_rows), "classes": counts,
                         "scripts_with_japanese": len({r[0].rsplit(":", 1)[0] for r in ja_rows})}
        print(disc, summary[disc], flush=True)
    (out / "audit_summary.json").write_text(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
