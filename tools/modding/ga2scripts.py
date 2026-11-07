#!/usr/bin/env python3
"""Extract, verify and rebuild Galaxy Angel II ADV scripts.

  ga2scripts.py extract <disc.iso|dir> <dump-dir>
      Writes <dump-dir>/<file>.gasm for every adv/scn/*.asb in ALL.DAT
      (UTF-8 text, one file per script) and manifest.json.
  ga2scripts.py verify <disc.iso|dir> [<dump-dir>]
      Proves the round trip: every script disassembles and reassembles to
      the identical .asb, every stored blob recompresses to the identical
      bytes, and (with a dump dir) every .gasm assembles to the original.
  ga2scripts.py build <disc.iso|dir> <dump-dir> <mods-root> [--target all|strm]
      Assembles the dump, finds scripts whose bytes changed and writes
      replacement archives to <mods-root>/<disc program>/ (the engine's
      $GA2_MODS layout): IDX.DAT plus ALL.DAT (default) or STRM.DAT
      (--target strm: changed scripts are appended to the small STRM.DAT
      archive and IDX.DAT points at them there; ALL.DAT stays untouched).
      A .gasm whose name is not on the disc becomes a new script file
      (inserted into the index in sorted order).  --wrap PIXELS word-wraps
      Latin message text with the game's half-width metrics (dialogue box:
      372); --max-lines warns about overlong messages.  --extra DIR adds
      other replacement archive files named by path (DIR/adv/char002.tbl).

<disc.iso|dir> is a disc image or a directory holding the disc's root files
(ALL.DAT, IDX.DAT, SYSTEM.CNF, ...).  Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ga2arc  # noqa: E402
import ga2asb  # noqa: E402
import ga2font  # noqa: E402

SCN = "adv/scn/"


def load_archive(src: ga2arc.DiscSource, name: str) -> ga2arc.Pidx:
    head = src.read(name, 0, 0x60)
    end = struct.unpack_from("<I", head, 0x20)[0] + struct.unpack_from("<I", head, 0x24)[0]
    return ga2arc.Pidx.parse(src.read(name, 0, end))


def speakers(src: ga2arc.DiscSource, allp: ga2arc.Pidx) -> dict:
    """Character id -> name from adv/char*.tbl (ID / NAME00 keys)."""
    out = {}
    for path, rec in allp.files().items():
        if re.fullmatch(r"adv/char\d+\.tbl", path):
            text = ga2arc.raic_decode(src.read("ALL.DAT", rec.b, rec.stored)).decode("cp932", "replace")
            cid = re.search(r"^ID\s*=\s*(\d+)", text, re.M)
            name = re.search(r"^NAME00\s*=\s*(.+?)\s*$", text, re.M)
            if cid and name:
                out[int(cid.group(1))] = name.group(1)
    return out


def cmd_extract(args) -> int:
    src = ga2arc.DiscSource(args.disc)
    allp = load_archive(src, "ALL.DAT")
    who = speakers(src, allp)
    out = Path(args.dump)
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"program": src.program(), "format": "ga2-gasm-1", "scripts": {}}
    n = 0
    for path, rec in sorted(allp.files().items()):
        if not (path.startswith(SCN) and path.endswith(".asb")):
            continue
        data = ga2arc.raic_decode(src.read("ALL.DAT", rec.b, rec.stored))
        sc = ga2asb.parse(data)
        name = path[len(SCN):]
        text = ga2asb.disassemble(sc, f"{manifest['program']} {path}", who)
        (out / (name[:-4] + ".gasm")).write_text(text + "\n", encoding="utf-8")
        manifest["scripts"][name] = {"sha1": hashlib.sha1(data).hexdigest(), "size": len(data)}
        n += 1
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n")
    print(f"extracted {n} scripts from {args.disc} ({manifest['program']}) to {out}")
    return 0


def cmd_verify(args) -> int:
    src = ga2arc.DiscSource(args.disc)
    allp = load_archive(src, "ALL.DAT")
    ok = fail = blobs = 0
    for path, rec in sorted(allp.files().items()):
        if not (path.startswith(SCN) and path.endswith(".asb")):
            continue
        blob = src.read("ALL.DAT", rec.b, rec.stored)
        data = ga2arc.raic_decode(blob)
        sc = ga2asb.parse(data)
        again = ga2asb.assemble(ga2asb.disassemble(sc))
        packed = ga2arc.raic_encode(data, compress=blob[3:4] == b"1")
        good = again == data and len(data) == rec.size
        blobs += packed == blob
        if args.dump:
            g = Path(args.dump) / (path[len(SCN):-4] + ".gasm")
            good = good and ga2asb.assemble(g.read_text(encoding="utf-8")) == data
        if good and packed == blob:
            ok += 1
        else:
            fail += 1
            print(f"MISMATCH {path}")
    print(f"{ok} scripts byte-identical (text round trip{' + dump files' if args.dump else ''}"
          f", recompressed blob {blobs} identical), {fail} mismatches")
    return 1 if fail else 0


def wrap_script(data: bytes, budget: float, metrics: dict, max_lines: int, label: str) -> bytes:
    """Word-wrap every message (builtin 0x00 text) that holds Latin text
    and is wider than `budget` pixels.  Strings are rewritten in place
    and the script is rebuilt through the text form, so references follow."""
    sc = ga2asb.parse(data)
    msgs = ga2asb.message_strings(sc)
    changed = False
    strings = []
    for off, raw in sc.strings:
        if off in msgs:
            text = raw.decode("cp932")
            wrapped, lines = ga2font.wrap_text(text, budget, metrics)
            if lines > max_lines and re.search(r"[A-Za-z]{3}", text):
                print(f"warning: {label}: message has {lines} lines (box holds {max_lines}): "
                      f"{wrapped[:60]!r}")
            if wrapped != text:
                raw = wrapped.encode("cp932")
                changed = True
        strings.append((off, raw))
    if not changed:
        return data
    sc.strings = strings
    return ga2asb.assemble(ga2asb.disassemble(sc))


def changed_scripts(src, allp, dump: Path, wrap=None) -> dict:
    """path -> new .asb bytes for every .gasm whose assembly differs from
    the disc, or that is not on the disc (a new script)."""
    out = {}
    files = allp.files()
    for g in sorted(dump.glob("*.gasm")):
        path = SCN + g.stem + ".asb"
        try:
            data = ga2asb.assemble(g.read_text(encoding="utf-8"))
        except ga2asb.AsbError as e:
            raise SystemExit(f"{g}: {e}")
        if wrap:
            data = wrap(data, g.name)
        sc_name = data[4:0x24].split(b"\0")[0].decode("ascii", "replace")
        if sc_name != g.stem + ".asb":
            raise SystemExit(f"{g}: .name is {sc_name!r}, expected {g.stem + '.asb'!r}")
        if g.stem != g.stem.lower() or len(g.stem) + 4 > 31:
            raise SystemExit(f"{g}: script names must be lower case and at most 27 characters")
        rec = files.get(path)
        if rec is None:
            out[path] = data
            continue
        orig = ga2arc.raic_decode(src.read("ALL.DAT", rec.b, rec.stored))
        if data != orig:
            out[path] = data
    return out


class SegmentedFile:
    """A file built from disc ranges and new bytes, written as NAME.ga2seg
    (see ps2_disc_mount.h); a directory source (no image) gets a plain file."""

    def __init__(self, src):
        self.src = src
        self.pieces = []          # (image offset or None, bytes or length)
        self.size = 0

    def literal(self, data: bytes) -> None:
        if data:
            if self.pieces and self.pieces[-1][0] is None:
                self.pieces[-1] = (None, self.pieces[-1][1] + data)
            else:
                self.pieces.append((None, bytes(data)))
            self.size += len(data)

    def image(self, name: str, off: int, length: int) -> None:
        base = self.src.image_offset(name)
        if base is None:
            self.literal(self.src.read(name, off, length))
            return
        if length:
            self.pieces.append((base + off, length))
            self.size += length

    def align(self, n: int) -> None:
        self.literal(b"\0" * ((-self.size) % n))

    def write(self, path: Path) -> None:
        seg = path.with_name(path.name + ".ga2seg")
        for stale in (path, seg):
            if stale.exists():
                stale.unlink()
        if all(src is None for src, _ in self.pieces):
            path.write_bytes(b"".join(d for _, d in self.pieces))
            return
        head = bytearray(b"GA2SEG1\0" + struct.pack("<II", len(self.pieces), 0))
        data_at = len(head) + 24 * len(self.pieces)
        literals = bytearray()
        for src, d in self.pieces:
            if src is None:
                head += struct.pack("<IIQQ", 0, 0, data_at + len(literals), len(d))
                literals += d
            else:
                head += struct.pack("<IIQQ", 1, 0, src, d)
        seg.write_bytes(bytes(head) + bytes(literals))


def changed_extra(src, allp, extra: Path) -> dict:
    """path -> bytes for every file under extra/ (named by its archive path,
    e.g. adv/char002.tbl) that differs from the disc's ALL.DAT copy."""
    out = {}
    files = allp.files()
    for f in sorted(extra.rglob("*")):
        if not f.is_file():
            continue
        path = f.relative_to(extra).as_posix()
        data = f.read_bytes()
        rec = files.get(path)
        if rec is None or data != ga2arc.raic_decode(src.read("ALL.DAT", rec.b, rec.stored)):
            out[path] = data
    return out


def disc_metrics(src, args) -> dict:
    if args.font_metrics:
        return ga2font.load_metrics(args.font_metrics)
    return ga2font.metrics_dict(ga2font.find_metrics(src.read(src.program()))[1])


def cmd_build(args) -> int:
    src = ga2arc.DiscSource(args.disc)
    program = src.program()
    allp = load_archive(src, "ALL.DAT")
    idx = ga2arc.Pidx.parse(src.read("IDX.DAT"))
    wrap = None
    if args.wrap:
        metrics = disc_metrics(src, args)
        wrap = lambda data, label: wrap_script(data, args.wrap, metrics, args.max_lines, label)
    changed = changed_scripts(src, allp, Path(args.dump), wrap)
    extra_files = {}
    if args.extra:
        extra_files = changed_extra(src, allp, Path(args.extra))
        # Files the index lists are repointed; pack-only files (battle data)
        # are reached through their load groups below.
        listed = idx.files()
        changed.update({p: d for p, d in extra_files.items() if p in listed})
    dest = Path(args.mods) / program
    if not changed:
        print("no script or extra file differs from the disc; nothing written")
        return 0
    dest.mkdir(parents=True, exist_ok=True)
    names = idx.archive_names()
    arc_off = {n: idx.u32(0x30 + 32 * i) for i, n in enumerate(names)}
    files = allp.files()

    if args.target == "all":
        out_name = "ALL.DAT"
        tmp = dest / (out_name + ".tmp")
        src.copy_to(out_name, tmp)
        size = src.size(out_name)
        recs = sorted((r for r in allp.records if r.kind == 0), key=lambda r: r.b)
        first_data = recs[0].b
        slot_end = {}
        for a, b in zip(recs, recs[1:]):
            slot_end[a.index] = b.b
        slot_end[recs[-1].index] = size
        uses = Counter(r.b for r in recs)
        shared = {o for o, k in uses.items() if k > 1}
        with open(tmp, "r+b") as f:
            end = (size + 2047) // 2048 * 2048
            for path, data in sorted(changed.items()):
                rec = files.get(path)
                blob = ga2arc.raic_encode(data)
                if rec and rec.b not in shared and rec.b + len(blob) <= slot_end[rec.index]:
                    off = rec.b
                    pad = slot_end[rec.index] - off - len(blob)
                    f.seek(off)
                    f.write(blob + b"\0" * min(pad, (-len(blob)) % 2048))
                else:
                    off = end
                    f.seek(off)
                    f.write(blob + b"\0" * ((-len(blob)) % 2048))
                    end = off + (len(blob) + 2047) // 2048 * 2048
                if rec:
                    allp.set_file(allp.files()[path], off, len(data), len(blob))
                    irec = idx.files()[path]
                    if idx.string(irec.a) != "all.dat":
                        raise SystemExit(f"IDX.DAT does not place {path} in all.dat")
                    idx.set_file(irec, off, len(data), len(blob))
                    print(f"{path}: {len(data)} bytes, stored {len(blob)} at {off:#x}")
                else:
                    allp.insert_file(path, 0, off, len(data), len(blob), limit=first_data)
                    idx.insert_file(path, arc_off["all.dat"], off, len(data), len(blob))
                    print(f"{path}: NEW, {len(data)} bytes, stored {len(blob)} at {off:#x}")
            f.seek(0)
            f.write(allp.data)
        tmp.replace(dest / out_name)
    else:
        # Append to STRM.DAT and point IDX.DAT at it.  The mod ships
        # STRM.DAT.ga2seg: the disc's own STRM.DAT and the unchanged parts of
        # rebuilt packs are ranges of the disc image, only new bytes are stored.
        strm_off = arc_off["strm.dat"]
        out = SegmentedFile(src)
        out.image("STRM.DAT", 0, src.size("STRM.DAT"))
        out.align(2048)
        for path, data in sorted(changed.items()):
            blob = ga2arc.raic_encode(data)
            off = out.size
            out.literal(blob)
            out.align(2048)
            irec = idx.files().get(path)
            if irec is None:
                idx.insert_file(path, strm_off, off, len(data), len(blob))
                print(f"{path}: NEW, {len(data)} bytes, stored {len(blob)} at STRM.DAT+{off:#x}")
                continue
            idx.set_file(irec, off, len(data), len(blob), archive=strm_off)
            print(f"{path}: {len(data)} bytes, stored {len(blob)} at STRM.DAT+{off:#x}")
        if extra_files:
            # The game also loads tables from FSTS packs (load groups in
            # ADV.DAT, SLG.DAT, ...), located through IDX.DAT's group table:
            # changed packs are rebuilt here (changed files appended, the rest
            # left in place) and their groups repointed.
            packs = {}
            for entry, name, arc, off, size, _n in idx.group_entries():
                archive = idx.string(arc).upper()
                key = (archive, off, size)
                if key not in packs:
                    pack = src.read(archive, off, size)
                    names = {e[0] for e in ga2arc.fsts_entries(pack)}
                    hit = {p: d for p, d in extra_files.items() if p in names}
                    if not hit:
                        packs[key] = None
                        continue
                    head, tail = ga2arc.fsts_append(pack, hit)
                    out.align(2048)
                    at = out.size
                    out.literal(head)
                    out.image(archive, off + len(head), size - len(head))
                    out.literal(tail)
                    packs[key] = (at, out.size - at)
                    print(f"group {name} ({archive}): {len(hit)} file(s) replaced, pack at STRM.DAT+{at:#x}")
                if packs[key]:
                    idx.set_group(entry, strm_off, *packs[key])
        out.align(2048)
        out.write(dest / "STRM.DAT")
    (dest / "IDX.DAT").write_bytes(bytes(idx.data))
    print(f"wrote {len(changed)} changed or new file(s) to {dest}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("extract")
    p.add_argument("disc")
    p.add_argument("dump")
    p = sub.add_parser("verify")
    p.add_argument("disc")
    p.add_argument("dump", nargs="?")
    p = sub.add_parser("build")
    p.add_argument("disc")
    p.add_argument("dump")
    p.add_argument("mods")
    p.add_argument("--target", choices=("all", "strm"), default="all")
    p.add_argument("--extra", help="folder of other replacement archive files by path (e.g. adv/char002.tbl)")
    p.add_argument("--wrap", type=float, metavar="PIXELS",
                   help="word-wrap Latin message text to this width (dialogue box: 396)")
    p.add_argument("--max-lines", type=int, default=3, help="warn above this many lines (3)")
    p.add_argument("--font-metrics", help="metrics.json from ga2font.py (default: the disc's)")
    args = ap.parse_args(argv)
    return {"extract": cmd_extract, "verify": cmd_verify, "build": cmd_build}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
