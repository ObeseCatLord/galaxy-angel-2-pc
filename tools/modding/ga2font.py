#!/usr/bin/env python3
"""Galaxy Angel II half-width (ASCII) font: metrics, export and text widths.

The games draw half-width characters 0x21..0x7e from a built-in 128x144
4-bit texture ("asciipfont", 18-pixel glyphs) with a per-character table
of 12-byte entries {u, v, width} (int32 each, characters 0x20..0x7e).  The
advance of a half-width character is (width + 1) * size / 18, a space or
any other single-byte code advances 10 * size / 18, a full-width (Shift-JIS)
character advances size.  Text is drawn proportionally already; see
docs/modding/text.md.

  ga2font.py dump <SLPM elf> <out-dir>   asciipfont.png + metrics.json
  ga2font.py tight <SLPM elf> <out.json> ink-derived metrics (u, width
                                         trimmed to the glyph's ink)
  ga2font.py measure <metrics.json|elf> <size> "text"   pixel width
  ga2font.py pack <metrics.json> <mod>/font/metrics.bin  the game's table
                                         for a language pack (the port loads
                                         it from the active mod)

Standard library only.
"""

from __future__ import annotations

import json
import re
import struct
import sys
import zlib
from pathlib import Path

# The metrics table is identical on all five discs; its first entries
# (space, '!', '"') identify it.
TABLE_HEAD = struct.pack("<9i", 0, 0, 9, 11, 0, 5, 20, 0, 5)
TABLE_ENTRIES = 0x7F - 0x20
TEX_W, TEX_H = 128, 144
GLYPH_H = 18
SPACE_ADVANCE = 10   # hard-coded for 0x20 and unmapped single bytes


def elf_segments(d: bytes) -> list:
    phoff = struct.unpack_from("<I", d, 28)[0]
    n = struct.unpack_from("<H", d, 44)[0]
    out = []
    for i in range(n):
        t, off, va, _pa, fs, _ms, _fl, _al = struct.unpack_from("<8I", d, phoff + 32 * i)
        if t == 1:
            out.append((off, va, fs))
    return out


def file_to_va(d: bytes, o: int) -> int | None:
    for off, va, fs in elf_segments(d):
        if off <= o < off + fs:
            return o - off + va
    return None


def find_metrics(d: bytes) -> tuple[int, list]:
    o = d.find(TABLE_HEAD)
    if o < 0 or d.find(TABLE_HEAD, o + 1) >= 0:
        raise ValueError("ASCII metrics table not found (or not unique)")
    table = [struct.unpack_from("<3i", d, o + 12 * i) for i in range(TABLE_ENTRIES)]
    return o, table


def find_texture(d: bytes) -> int:
    """File offset of the asciipfont texture header (128x144 PSMT4)."""
    hdr = struct.pack("<4I", 0x20, 0x10001, 0x30, 0x140000) + struct.pack("<I", 0x900008)
    pos = 0
    while True:
        o = d.find(hdr, pos)
        if o < 0:
            raise ValueError("asciipfont texture not found")
        if struct.unpack_from("<I", d, o + 0x1C)[0] == 0x2430:  # 0x30 + 128*144/2
            return o
        pos = o + 1


def texture_pixels(d: bytes) -> list:
    o = find_texture(d) + 0x30
    rows = []
    for y in range(TEX_H):
        row = []
        for x in range(TEX_W // 2):
            b = d[o + y * (TEX_W // 2) + x]
            row += [b & 15, b >> 4]
        rows.append(row)
    return rows


def write_png(path: Path, rows: list, scale: int = 1) -> None:
    h, w = len(rows) * scale, len(rows[0]) * scale
    raw = bytearray()
    for row in rows:
        line = bytearray([0])
        for v in row:
            line += bytes([v * 17]) * scale
        raw += bytes(line) * scale
    def chunk(t, b):
        return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b) & 0xFFFFFFFF)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")
    path.write_bytes(png)


def metrics_dict(table: list) -> dict:
    return {"space_advance": SPACE_ADVANCE, "glyph_height": GLYPH_H,
            "glyphs": {chr(0x20 + i): list(e) for i, e in enumerate(table)}}


def load_metrics(src: str) -> dict:
    p = Path(src)
    if p.suffix == ".json":
        return json.loads(p.read_text())
    return metrics_dict(find_metrics(p.read_bytes())[1])


def advance(ch: str, size: int, m: dict) -> float:
    """Pixel advance of one character at glyph size `size` (default 18;
    \\sNN sets it), as the game's message drawer computes it."""
    try:
        b = ch.encode("cp932")
    except UnicodeEncodeError:
        return float(size)
    if len(b) == 2:
        return float(size)
    c = b[0]
    if 0x21 <= c <= 0x7E:
        return (m["glyphs"][chr(c)][2] + 1) * size / 18.0
    return m.get("space_advance", SPACE_ADVANCE) * size / 18.0


def text_width(s: str, size: int, m: dict) -> float:
    return sum(advance(ch, size, m) for ch in s)


_CODE = re.compile(r"\\(s\d{1,2}|c[0-9A-Fa-f]{1,8}|v\d{1,3})")


def _segments(s: str):
    """Split into (is_code, text) pieces; codes are \\sNN, \\cXXXXXXXX, \\vNNN."""
    pos = 0
    for m in _CODE.finditer(s):
        if m.start() > pos:
            yield False, s[pos:m.start()]
        yield True, m.group()
        pos = m.end()
    if pos < len(s):
        yield False, s[pos:]


def wrap_text(s: str, budget: float, m: dict, size: int = 18) -> tuple[str, int]:
    """Greedy word wrap of half-width text to a pixel budget.  Existing
    line breaks are kept; only lines that hold Latin letters and are wider
    than `budget` are broken, at spaces.  Control codes take no width;
    \\sNN changes the size of what follows (as in the game).  Returns the
    text and its number of lines."""
    out_lines = []
    for para in s.split("\n"):
        if not re.search(r"[A-Za-z]", para):
            out_lines.append(para)
            # keep the size state in step
            for is_code, t in _segments(para):
                if is_code and t[1] == "s":
                    size = int(t[2:])
            continue
        # tokens: words and the spaces between them; codes stay attached
        words = para.split(" ")
        line, width = "", 0.0
        for wi, word in enumerate(words):
            w = 0.0
            sz = size
            for is_code, t in _segments(word):
                if is_code:
                    if t[1] == "s":
                        sz = int(t[2:])
                else:
                    w += text_width(t, sz, m)
            space = advance(" ", size, m) if wi else 0.0
            if wi and line and width + space + w > budget:
                out_lines.append(line)
                line, width = word, w
            else:
                line = line + (" " if wi else "") + word
                width += space + w
            size = sz
        out_lines.append(line)
    return "\n".join(out_lines), len(out_lines)


def tight(d: bytes, table: list) -> list:
    """Trim each glyph cell to the columns that carry ink (alpha > 2)."""
    px = texture_pixels(d)
    out = [list(table[0])]
    for i, (u, v, w) in enumerate(table[1:], 1):
        cols = [x for x in range(u, min(TEX_W, u + w))
                if any(px[y][x] > 2 for y in range(v, min(TEX_H, v + GLYPH_H)))]
        if not cols:
            out.append([u, v, w])
            continue
        out.append([cols[0], v, cols[-1] - cols[0] + 1])
    return out


def main(argv: list) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    cmd = argv[0]
    if cmd == "dump":
        d = Path(argv[1]).read_bytes()
        out = Path(argv[2])
        out.mkdir(parents=True, exist_ok=True)
        to, table = find_metrics(d)
        xo = find_texture(d)
        write_png(out / "asciipfont.png", texture_pixels(d))
        write_png(out / "asciipfont-4x.png", texture_pixels(d), 4)
        m = metrics_dict(table)
        m["metrics_va"] = hex(file_to_va(d, to))
        m["texture_va"] = hex(file_to_va(d, xo))
        (out / "metrics.json").write_text(json.dumps(m, indent=1) + "\n")
        print(f"metrics at {m['metrics_va']}, texture at {m['texture_va']} -> {out}")
    elif cmd == "tight":
        d = Path(argv[1]).read_bytes()
        _, table = find_metrics(d)
        m = metrics_dict(tight(d, table))
        Path(argv[2]).write_text(json.dumps(m, indent=1) + "\n")
        print(f"wrote {argv[2]}")
    elif cmd == "pack":
        m = load_metrics(argv[1])
        out = bytearray()
        for i in range(TABLE_ENTRIES):
            ch = chr(0x20 + i)
            if ch not in m["glyphs"]:
                raise ValueError(f"metrics: no entry for {ch!r}")
            u, v, w = (int(x) for x in m["glyphs"][ch])
            if i and (u < 0 or v < 0 or w < 0 or u + w > TEX_W or v + GLYPH_H > TEX_H):
                raise ValueError(f"metrics: {ch!r} cell ({u}, {v}, {w}) is outside the {TEX_W}x{TEX_H} font")
            out += struct.pack("<3i", u, v, w)
        Path(argv[2]).parent.mkdir(parents=True, exist_ok=True)
        Path(argv[2]).write_bytes(bytes(out))
        print(f"wrote {argv[2]} ({len(out)} bytes)")
    elif cmd == "measure":
        m = load_metrics(argv[1])
        print(f"{text_width(argv[3], int(argv[2]), m):.1f}")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
