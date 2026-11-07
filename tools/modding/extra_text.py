#!/usr/bin/env python3
"""Battle dialogue and UI table text of all five discs: a translation
workbook, and its import into a language pack.

  extra_text.py export <private> <out.xlsx>
  extra_text.py check  <xlsx> <private>

The import is done by `script_xlsx.py import ... --extra-xlsx <xlsx>`, which
builds everything into one mod (see docs/modding/translation.md).

What is covered (all read from the load-group packs the game uses, FSTS packs
in ADV.DAT and SLG.DAT):

* Battle sheet: the dialogue of the battle opening demos and tutorials,
  `slg/stage/<stage>/slg_opdemo.dat`.  These are bytecode files whose string
  pool follows the code; opcode 0x85D takes the file offset of a string.  The
  import appends the English strings to the file and points those operands
  at them, so nothing else moves.
* UI sheet: table values (`KEY =value` lines of .tbl files in the packs) that
  hold Japanese outside comments: save-slot chapter titles, the extras menu,
  Angel select, the tutorial menu, unit and stage names, ...

One row per distinct Japanese text and sheet; `Where` lists every place it
occurs (disc:path:L<line> for tables, disc:path:@<offset> for battle
strings) and the English is used for all of them.
"""
from __future__ import annotations

import argparse
import glob
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ga2arc  # noqa: E402
import ga2asb  # noqa: E402
import ga2font  # noqa: E402
import script_xlsx as sx  # noqa: E402

DISCS = ["zettai", "mugen-1", "mugen-2", "eigou-1", "eigou-2"]
PACK_ARCHIVES = ("ADV.DAT", "SLG.DAT")
OPDEMO = re.compile(r"slg/stage/\d+/slg_opdemo\.dat")
CREDITS = re.compile(r"adv/roll_s(_[a-f])?\.txt")   # staff roll, in the "ending" load group
JAPANESE = re.compile(r"[぀-ヿ㐀-鿿！-～　-〿]")
VALUE_LINE = re.compile(r"^(\s*[#\w]+\s*=\s*)(.*?)(\s*(?://|;).*)?$")
STRING_OP = 0x85D
# Battle balloon (measured in Zettai stage 1-1): size-13 text from the left
# margin, about 193 pixels to the frame and 180 beside the next-page marker;
# it neither wraps nor clips.  3 lines.
BATTLE_SIZE, BATTLE_WIDTH, BATTLE_LINES = 13, 180, 3
# The balloon's text builder (0x1ce0a0 on Zettai) takes the story codes \sNN,
# \cRRGGBB and \vN; a line is 4 px plus its tallest glyph, and the balloon
# has room for 62 px: 3 lines at size 13, 4 at 12 or 11.
BATTLE_HEIGHT, BATTLE_MIN_SIZE = 62, 11


def battle_lines(size: int) -> int:
    return (BATTLE_HEIGHT + 4) // (size + 4)


def fit_battle(text: str, metrics: dict) -> tuple[str, int, int]:
    """The translator's breaks when they fit; else reflowed; else reflowed
    at a smaller size.  Returns text, its line count and the lines allowed."""
    wrapped, n = ga2font.wrap_text(text, BATTLE_WIDTH, metrics, BATTLE_SIZE)
    if n <= BATTLE_LINES or not re.search(r"[A-Za-z]{2}", text):
        return wrapped, n, BATTLE_LINES
    flowed = sx.reflow(text)
    for size in range(BATTLE_SIZE, BATTLE_MIN_SIZE - 1, -1):
        candidate = flowed if size == BATTLE_SIZE else f"\\s{size}{flowed}"
        wrapped, n = ga2font.wrap_text(candidate, BATTLE_WIDTH, metrics, BATTLE_SIZE)
        if n <= battle_lines(size):
            return wrapped, n, battle_lines(size)
    return wrapped, n, battle_lines(BATTLE_MIN_SIZE)
PATHLIKE = re.compile(r"[/\\]|\.[A-Za-z0-9]{2,4}\b")   # file names and folders are not text
NOTES = {"varmenu.tbl": "developer variable menu (debug), probably never shown to players",
         "saveload.tbl": "TITLE: memory-card save title; #NNN: save-slot chapter title, at most 24 bytes"}


def pack_files(private: Path, disc: str) -> dict:
    """path -> bytes of every file in the disc's load-group packs (first copy)."""
    iso = sorted(glob.glob(str(private / "discs" / disc / "*.iso")))[0]
    src = ga2arc.DiscSource(iso)
    out = {}
    for arc in PACK_ARCHIVES:
        data = src.read(arc)
        for _name, off, size, _n in ga2arc.Pidx.parse(data).groups():
            pack = data[off:off + size]
            for path, o, sz, st in ga2arc.fsts_entries(pack):
                if path not in out and (path.endswith(".tbl") or OPDEMO.fullmatch(path) or CREDITS.fullmatch(path)):
                    out[path] = ga2arc.raic_decode(pack[o:o + st]) if st else pack[o:o + sz]
    return out


# ---------------------------------------------------------------- battle demos

def opdemo_strings(data: bytes) -> list[tuple[int, int]]:
    """(operand position, string offset) of every 0x85D string operand.
    Instructions are a word whose bits 10-13 hold 1 + the operand count;
    parsing stops at the first string, where the pool begins."""
    refs = []
    pc, end = 0, len(data)
    while pc + 4 <= end:
        w = struct.unpack_from("<I", data, pc)[0]
        n = ((w >> 10) & 0xF) - 1
        if n < 0 or pc + 4 + 4 * n > len(data):
            break
        if w & 0xFFF == STRING_OP and n == 1:
            target = struct.unpack_from("<I", data, pc + 4)[0]
            if 0 < target < len(data):
                refs.append((pc + 4, target))
                end = min(end, target)
        pc += 4 + 4 * n
    return refs


def c_string(data: bytes, off: int) -> bytes:
    return data[off:data.index(b"\0", off)]


def opdemo_with(data: bytes, english: dict) -> bytes:
    """english: string offset -> cp932 bytes.  Appends the strings and
    repoints their operands."""
    out = bytearray(data)
    placed = {}
    for pos, target in opdemo_strings(data):
        if target not in english:
            continue
        if target not in placed:
            out += b"\0" * ((-len(out)) % 4)
            placed[target] = len(out)
            out += english[target] + b"\0"
        struct.pack_into("<I", out, pos, placed[target])
    out += b"\0" * ((-len(out)) % 4)
    return bytes(out)


# ---------------------------------------------------------------- tables

def table_values(text: str) -> list[tuple[int, str, str]]:
    """(line number, key, value) of lines whose value shows Japanese."""
    out = []
    for n, line in enumerate(text.split("\n"), 1):
        m = VALUE_LINE.match(line.rstrip("\r"))
        if m and m.group(2) and JAPANESE.search(m.group(2)) and not PATHLIKE.search(m.group(2)):
            out.append((n, m.group(1).split("=")[0].strip(), m.group(2)))
    return out


def table_with(data: bytes, english: dict) -> bytes:
    """english: line number -> new value text (quotes kept as in the original)."""
    lines = data.decode("cp932").split("\n")
    for n, value in english.items():
        line = lines[n - 1]
        cr = "\r" if line.endswith("\r") else ""
        m = VALUE_LINE.match(line.rstrip("\r"))
        old = m.group(2)
        # The game's table parser ends an unquoted value at a space; quoted
        # values keep them (the stock tables have "Wing of Destiny").
        if (len(old) >= 2 and old[0] == old[-1] == '"') or " " in value.strip() or "\t" in value:
            value = '"' + value.strip().strip('"') + '"'
        lines[n - 1] = m.group(1) + value + (m.group(3) or "") + cr
    return "\n".join(lines).encode("cp932")


# ---------------------------------------------------------------- staff roll

# A staff-roll line: tabs, the text (quoted, or bare), tabs, font size,
# alignment L/R/C, x and an optional y (relative +N/-N or absolute).
ROLL_LINE = re.compile(r'^(\t*\s*)("[^"]*"|[^\t"]+?)(\t*)(\d+)(\t+)([LRC])(\t+)(\d+)(.*)$')


def credit_strings(text: str) -> list[tuple[int, str, int, str, int]]:
    """(line number, text, size, alignment, x) of staff-roll lines showing Japanese."""
    out = []
    for n, line in enumerate(text.split("\n"), 1):
        line = line.rstrip("\r")
        if line.lstrip().startswith("//"):
            continue
        m = ROLL_LINE.match(line)
        if m and JAPANESE.search(m.group(2)):
            out.append((n, m.group(2).strip('"'), int(m.group(4)), m.group(6), int(m.group(8))))
    return out


def credit_room(align: str, x: int) -> float:
    """Pixels the text has: a left column (x < 292) up to the name column,
    the name column to the right edge, centred text both ways."""
    if align == "C":
        return 2.0 * min(x - 8, 632 - x)
    if align == "R":
        return x - 8.0
    return (292 - 8 - x) if x < 292 else (632 - x)


def credits_with(data: bytes, english: dict) -> bytes:
    """english: line number -> new text (quoted when the original was)."""
    lines = data.decode("cp932").split("\n")
    for n, value in english.items():
        line = lines[n - 1]
        cr = "\r" if line.endswith("\r") else ""
        m = ROLL_LINE.match(line.rstrip("\r"))
        old = m.group(2)
        new = '"' + value + '"' if old.startswith('"') or " " in value else value
        lines[n - 1] = m.group(1) + new + "".join(m.group(i) for i in range(3, 10)) + cr
    return "\n".join(lines).encode("cp932")


# ---------------------------------------------------------------- executable text

HALFWIDTH_KANA = re.compile(r"[\uff61-\uff9f]")
DEBUG_HINT = re.compile(r"テスト|デバッグ|バグ|変数|PHASE|MAT|ﾃｽﾄ|ダミー|TEST", re.I)
PRINTF = re.compile(r"%[-+ #0]*\d*(?:\.\d+)?[hlL]*[diouxXeEfgGcsp%]")


def program_strings(private: Path, disc: str) -> list[tuple[int, bytes, str, int]]:
    """(address, original bytes, text, room) of the Japanese strings in the
    executable's data sections; room = bytes the new string (without its NUL)
    may take: the original plus the zero padding after it, minus the NUL."""
    iso = sorted(glob.glob(str(private / "discs" / disc / "*.iso")))[0]
    src = ga2arc.DiscSource(iso)
    exe = src.read(src.program())
    shoff = struct.unpack_from("<I", exe, 0x20)[0]
    shn, shstr = struct.unpack_from("<HH", exe, 0x30)
    secs = [struct.unpack_from("<10I", exe, shoff + 40 * i) for i in range(shn)]
    names = secs[shstr][4]
    out = []
    for name_off, kind, flags, addr, off, size, *_ in secs:
        name = exe[names + name_off:exe.index(b"\0", names + name_off)].decode()
        if kind != 1 or flags & 4 or not name.startswith((".data", ".rodata", ".sdata")):
            continue
        blob = exe[off:off + size]
        pos = 0
        while pos < len(blob):
            end = blob.find(b"\0", pos)
            if end < 0:
                break
            raw = blob[pos:end]
            if len(raw) >= 4 and (addr + pos) % 2 == 0:
                try:
                    text = raw.decode("cp932")
                except UnicodeDecodeError:
                    text = ""
                # Strings start 4-aligned; table fields (the extras cast list)
                # 2-aligned, those must be all full-width (packed binary data
                # mixes in ASCII and half-width kana).
                if ((addr + pos) % 4 and any(ord(ch) < 0x80 for ch in text)):
                    text = ""
                if (len(re.findall(r"[ぁ-ヿ一-鿿]", text)) >= 1 and not HALFWIDTH_KANA.search(text)
                        and all(ch.isprintable() or ch in "\n\t" for ch in text)):
                    pad = end
                    while pad < len(blob) and blob[pad] == 0:
                        pad += 1
                    out.append((addr + pos, raw, text, pad - pos - 1))
            pos = end + 1
    # The game's strings are one pool at the start of .rodata, in dense
    # clusters that are mostly kana; past it, binary tables decode to
    # scattered kanji by chance.  Keep the span of the clusters (gap under
    # 8 KiB) holding at least 3 strings, half or more of them with kana.
    clusters: list = []
    for item in out:
        if clusters and item[0] - clusters[-1][-1][0] < 0x2000:
            clusters[-1].append(item)
        else:
            clusters.append([item])
    good = [c for c in clusters
            if len(c) >= 3 and sum(bool(re.search(r"[ぁ-ヿ]", t)) for _a, _r, t, _m in c) * 2 >= len(c)]
    if not good:
        return []
    lo, hi = good[0][0][0], good[-1][-1][0]
    return [item for item in out if lo <= item[0] <= hi]


def program_rows(private: Path) -> list:
    rows: dict = {}
    for disc in DISCS:
        program = ga2arc.DiscSource(sorted(glob.glob(str(private / "discs" / disc / "*.iso")))[0]).program()
        for address, raw, text, room in program_strings(private, disc):
            row = rows.setdefault(text, {"where": [], "room": room, "bytes": len(raw)})
            row["where"].append(f"{disc}:{program}:@{address:#x}")
            row["room"] = min(row["room"], room)
    out = []
    for text, r in rows.items():
        notes = []
        if DEBUG_HINT.search(text):
            notes.append("looks like a developer/debug string; probably never shown")
        if PRINTF.search(text):
            notes.append("keep the % codes (%d, %s, ...) in the same order")
        out.append([r["where"][0], text, "", "; ".join(notes), "program", f"room {r['room']} bytes", r["bytes"],
                    len(r["where"]), "\n".join(r["where"])])
    return out


def program_patches(xlsx: Path, private: Path) -> tuple[dict, list, int]:
    """Program sheet -> ({disc: [(address, original, new)]}, errors, rows used)."""
    sheets = sx.read_xlsx(xlsx)
    rows = sheets.get("Program") or []
    if not rows:
        return {}, [], 0
    head = rows[0]
    ce, cw, cj = head.index("English"), head.index("Where"), head.index("Japanese")
    originals = {disc: {a: raw for a, raw, _t, _r in program_strings(private, disc)} for disc in DISCS}
    rooms = {disc: {a: room for a, _raw, _t, room in program_strings(private, disc)} for disc in DISCS}
    out: dict = {}
    errors = []
    used = 0
    for rn, row in enumerate(rows[1:], 2):
        eng = row[ce].strip("\n") if ce < len(row) else ""
        if not eng.strip() or eng.strip() == sx.OMITTED:
            continue
        try:
            text = sx.expand_buttons("".join(sx.fold_latin(sx.TYPO.get(ch, ch)) for ch in eng.replace("\r\n", "\n")), row[cj])
        except ga2asb.AsbError as e:
            errors.append(f"Program!row {rn}: {e}")
            continue
        if PRINTF.findall(text) != PRINTF.findall(row[cj]):
            errors.append(f"Program!row {rn}: the % codes must be {PRINTF.findall(row[cj])} in this order")
            continue
        try:
            new = text.encode("cp932")
        except UnicodeEncodeError as e:
            errors.append(f"Program!row {rn}: {e}")
            continue
        used += 1
        for where in row[cw].split("\n"):
            m = WHERE.fullmatch(where.strip())
            if not m or not m.group(5):
                errors.append(f"Program!row {rn}: bad Where entry {where!r}")
                continue
            disc, address = m.group(1), int(m.group(5), 16)
            if address not in originals.get(disc, {}):
                errors.append(f"Program!row {rn}: {where} is not a string of the executable")
                continue
            if len(new) > rooms[disc][address]:
                errors.append(f"Program!row {rn}: {len(new)} bytes, room {rooms[disc][address]} at {where}")
                continue
            out.setdefault(disc, []).append((address, originals[disc][address], new))
    return out, errors, used


def write_program_patches(patches: dict, private: Path, mod: Path) -> int:
    """<mod>/exe/<program>.tsv for ga2_exetext.cpp; returns patches written."""
    n = 0
    for disc, items in sorted(patches.items()):
        program = ga2arc.DiscSource(sorted(glob.glob(str(private / "discs" / disc / "*.iso")))[0]).program()
        dst = mod / "exe" / f"{program}.tsv"
        dst.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# address\toriginal\tnew (hex); applied by the runtime when the original matches"]
        lines += [f"{a:#x}\t{o.hex()}\t{w.hex()}" for a, o, w in sorted(items)]
        dst.write_text("\n".join(lines) + "\n")
        n += len(items)
    return n


# ---------------------------------------------------------------- workbook

COLUMNS = [("ID", 34, "text"), ("Japanese", 46, "wrap"), ("English", 46, "edit"), ("Notes", 30, "edit"),
           ("Kind", 12, "text"), ("Key", 16, "text"), ("Japanese bytes", 9, "text"),
           ("Uses", 6, "text"), ("Where", 60, "wrap")]


def collect(private: Path) -> tuple[list, list, list]:
    battle: dict = {}
    ui: dict = {}
    credits: dict = {}
    for disc in DISCS:
        files = pack_files(private, disc)
        for path, data in sorted(files.items()):
            if OPDEMO.fullmatch(path):
                for _pos, off in opdemo_strings(data):
                    raw = c_string(data, off)
                    text = raw.decode("cp932", "replace")
                    if not text.strip() or not JAPANESE.search(text):
                        continue
                    stage = path.split("/")[2]
                    row = battle.setdefault(text, {"where": [], "kind": "battle", "key": f"stage {stage}",
                                                   "bytes": len(raw)})
                    where = f"{disc}:{path}:@{off:#x}"
                    if where not in row["where"]:
                        row["where"].append(where)
            elif CREDITS.fullmatch(path):
                for n, text, size, align, x in credit_strings(data.decode("cp932", "replace")):
                    row = credits.setdefault(text, {"where": [], "kind": "column " + ("left" if align == "L" and x < 292
                                                                                    else "right" if align == "L"
                                                                                    else "centred"),
                                                    "key": f"size {size}, room {credit_room(align, x):.0f} px",
                                                    "bytes": len(text.encode("cp932", "replace"))})
                    row["where"].append(f"{disc}:{path}:L{n}")
            elif path.endswith(".tbl") and not re.fullmatch(r"adv/char\d+\.tbl", path):
                text = data.decode("cp932", "replace")
                for n, key, value in table_values(text):
                    row = ui.setdefault((path.split("/")[-1], key, value),
                                        {"where": [], "kind": path.split("/")[-1], "key": key,
                                         "bytes": len(value.encode("cp932", "replace")), "text": value})
                    row["where"].append(f"{disc}:{path}:L{n}")
    brows = [[r["where"][0], t, "", "", r["kind"], r["key"], r["bytes"], len(r["where"]), "\n".join(r["where"])]
             for t, r in battle.items()]
    urows = [[r["where"][0], r["text"], "", NOTES.get(r["kind"], ""), r["kind"], r["key"], r["bytes"],
              len(r["where"]), "\n".join(r["where"])] for r in ui.values()]
    crows = [[r["where"][0], t, "", "", r["kind"], r["key"], r["bytes"], len(r["where"]), "\n".join(r["where"])]
             for t, r in credits.items()]
    return brows, urows, crows


def names_english(private: Path) -> dict:
    """Japanese name -> English from the story workbook's Names sheet (exact
    names only), to start the Credits sheet with the translator's own names."""
    book = private / "research" / "translation" / "ga2_script.xlsx"
    if not book.exists():
        return {}
    rows = sx.read_xlsx(book).get("Names") or []
    if not rows:
        return {}
    head = rows[0]
    cj, ce = head.index("Japanese"), head.index("English")
    out = {}
    for row in rows[1:]:
        if ce < len(row) and row[ce].strip() and row[ce].strip() != sx.OMITTED:
            out.setdefault(row[cj].strip(), row[ce].strip())
    return out


README = [
    ("Galaxy Angel II: battle and UI text", sx.S_TITLE),
    ("Text that is not in the story workbook (ga2_script.xlsx): the dialogue of the battle opening demos "
     "and tutorials (Battle sheet) and table values shown in menus and battles (UI sheet).", 0),
    ("Fill in the English column only. One row stands for every place listed in Where (the same Japanese "
     "text on several discs or stages); its English is used for all of them. Empty cells or <omitted> keep "
     "the Japanese.", 0),
    ("Battle sheet: line breaks are kept as you write them when the text fits (choices put one option per "
     "line). A line wider than the balloon (about 29 half-width characters, 180 pixels at the battle text "
     "size) is broken at a space automatically. The balloon holds 3 lines; longer text is re-wrapped "
     "ignoring your breaks, and if it still does not fit it is set one or two sizes smaller (4 lines). The "
     "import warns about anything that still overflows.", 0),
    ("UI sheet: Kind is the table file, Key the entry. Save-slot chapter titles (saveload.tbl #NNN) allow "
     "at most 24 bytes (24 half-width characters). Quotes around a value are kept automatically. Japanese "
     "bytes is the length of the original, as a guide for fields with fixed room.", 0),
    ("Button names (Battle, UI and Program text): write {circle} {cross} {square} {triangle} {l1} {r1} {l2} "
     "{r2} {l3} {r3} {start} {select} {dpad} {lstick} {rstick} {up} {down} {left} {right} instead of ○ × △ □ "
     "L1 ...; the game draws the icon of the player's input device. A placeholder is 2 bytes (4 for {l1} "
     "{r1} {l2} {r2} {start} {select}) in the Program sheet's room counts.", 0),
    ("Import: script_xlsx.py import ga2_script.xlsx <private> <work> --build <mods> --extra-xlsx this.xlsx", 0),
]

CREDITS_README = [
    ("Galaxy Angel II: staff roll and program text", sx.S_TITLE),
    ("The staff roll shown at the endings (adv/roll_s*.txt on every disc): role titles, character names "
     "in the cast list, staff and voice-cast names, companies. Fill in the English column only; empty cells "
     "keep the Japanese. One row stands for every place listed in Where.", 0),
    ("Kind says which column the text is in (left: role titles and character names, right: people and "
     "companies, centred: headings). Key gives the font size and the room in pixels; at size 20 a half-width "
     "letter is about 10 pixels, so the left column holds about 25 characters and the import warns about "
     "longer text. One line per cell (no line breaks).", 0),
    ("English cells already filled come from the Names sheet of ga2_script.xlsx (exact name matches only, "
     "marked in Notes); check or change them.", 0),
    ("Program sheet: text stored inside the game programs: battle command names (Stay, Move, Attack, ...), "
     "memory-card and save messages, the save-slot chapter format, the extras cast list, plus developer "
     "strings (marked in Notes; leave those empty). Each English text must fit the room in Key, counted in "
     "bytes (a half-width letter is 1 byte, a full-width character 2); the import refuses longer text. "
     "Keep % codes such as %d and %s, in the same order.", 0),
    ("Import: script_xlsx.py import ga2_script.xlsx <private> <work> --build <mods> --extra-xlsx "
     "ga2_battle_ui.xlsx --extra-xlsx this.xlsx", 0),
]


def cmd_export_more(args) -> int:
    private = Path(args.private)
    _b, _u, crows = collect(private)
    names = names_english(private)
    for r in crows:
        if r[1] in names:
            r[2], r[3] = names[r[1]], "from the Names sheet"
    sheets = [("README", sx.sheet_xml([("Notes", 110, "wrap")], [[t] for t, _ in CREDITS_README], freeze=False,
                                      autofilter=False), None),
              ("Credits", sx.sheet_xml(COLUMNS, crows), f"$A$1:${sx.col_letter(len(COLUMNS) - 1)}${len(crows) + 1}")]
    prows = program_rows(private)
    sheets.append(("Program", sx.sheet_xml(COLUMNS, prows), f"$A$1:${sx.col_letter(len(COLUMNS) - 1)}${len(prows) + 1}"))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sx.write_xlsx(out, sheets)
    print(f"wrote {out}: Credits {len(crows)} rows ({sum(r[7] for r in crows)} uses, "
          f"{sum(1 for r in crows if r[2])} pre-filled from the Names sheet), Program {len(prows)} rows "
          f"({sum(1 for r in prows if r[3].startswith('looks like'))} marked as developer strings)")
    return 0


def cmd_export(args) -> int:
    private = Path(args.private)
    brows, urows, _c = collect(private)
    sheets = [("README", sx.sheet_xml([("Notes", 110, "wrap")], [[t] for t, _ in README], freeze=False,
                                      autofilter=False), None)]
    for name, rows in (("Battle", brows), ("UI", urows)):
        ref = f"$A$1:${sx.col_letter(len(COLUMNS) - 1)}${len(rows) + 1}"
        sheets.append((name, sx.sheet_xml(COLUMNS, rows), ref))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sx.write_xlsx(out, sheets)
    print(f"wrote {out}: Battle {len(brows)} rows ({sum(r[7] for r in brows)} uses), "
          f"UI {len(urows)} rows ({sum(r[7] for r in urows)} uses)")
    return 0


WHERE = re.compile(r"([a-z0-9-]+):(.+):(L(\d+)|@0x([0-9a-f]+))")


def battle_metrics(private: Path) -> dict:
    iso = sorted(glob.glob(str(private / "discs" / "zettai" / "*.iso")))[0]
    src = ga2arc.DiscSource(iso)
    return ga2font.metrics_dict(ga2font.find_metrics(src.read(src.program()))[1])


def edits(xlsx: Path, private: Path) -> tuple[dict, list, int]:
    """{disc: {path: new bytes}}, errors, rows used.  Battle lines keep their
    breaks; a line wider than the balloon is broken at spaces."""
    sheets = sx.read_xlsx(xlsx)
    metrics = battle_metrics(private)
    by_file: dict = {}
    errors = []
    used = 0
    for sheet in ("Battle", "UI", "Credits"):
        rows = sheets.get(sheet) or []
        if not rows:
            continue
        head = rows[0]
        try:
            ce, cw = head.index("English"), head.index("Where")
            cj = head.index("Japanese") if "Japanese" in head else None
        except ValueError:
            errors.append(f"{sheet}: no English or Where column")
            continue
        for rn, row in enumerate(rows[1:], 2):
            eng = row[ce].strip("\n") if ce < len(row) else ""
            if not eng.strip() or eng.strip() == sx.OMITTED:
                continue
            # The staff roll quotes its fields with ASCII '"': curly quotes
            # stay as their full-width Shift-JIS forms there.
            keep = "“”‘’" if sheet == "Credits" else ""
            text = "".join(ch if ch in keep else sx.fold_latin(sx.TYPO.get(ch, ch))
                           for ch in eng.replace("\r\n", "\n"))
            try:
                text = sx.expand_buttons(text, row[cj] if cj is not None and cj < len(row) else "")
            except ga2asb.AsbError as e:
                errors.append(f"{sheet}!row {rn}: {e}")
                continue
            if sheet == "Credits":
                text = text.replace('"', "”")
            if sheet == "Battle":
                text, lines, room = fit_battle(text, metrics)
                if lines > room:
                    print(f"warning: {sheet}!row {rn}: {lines} lines in the battle balloon (holds {room}): "
                          f"{text[:50]!r}")
            try:
                raw = ga2asb.text_to_sjis(text)
            except ga2asb.AsbError as e:
                errors.append(f"{sheet}!row {rn}: {e}")
                continue
            if sheet in ("UI", "Credits") and "\n" in text:
                errors.append(f"{sheet}!row {rn}: a {'table value' if sheet == 'UI' else 'staff-roll entry'} "
                              f"cannot contain a line break")
                continue
            if sheet == "Credits":
                if '"' in text:
                    errors.append(f"{sheet}!row {rn}: a staff-roll entry cannot contain a double quote")
                    continue
                key = row[head.index("Key")] if "Key" in head else ""
                km = re.match(r"size (\d+), room (\d+) px", key)
                if km:
                    width = ga2font.text_width(text, int(km.group(1)), metrics)
                    if width > int(km.group(2)):
                        print(f"warning: {sheet}!row {rn}: {width:.0f} px, room {km.group(2)} px: {text!r}")
            used += 1
            for where in (row[cw] if cw < len(row) else "").split("\n"):
                m = WHERE.fullmatch(where.strip())
                if not m:
                    if where.strip():
                        errors.append(f"{sheet}!row {rn}: bad Where entry {where!r}")
                    continue
                disc, path = m.group(1), m.group(2)
                slot = int(m.group(4)) if m.group(4) else int(m.group(5), 16)
                by_file.setdefault(disc, {}).setdefault(path, {})[slot] = raw if sheet == "Battle" else text
    out: dict = {}
    for disc, files in by_file.items():
        originals = pack_files(private, disc)
        for path, slots in files.items():
            if path not in originals:
                errors.append(f"{disc}: {path} is not in the disc's packs")
                continue
            data = originals[path]
            out.setdefault(disc, {})[path] = opdemo_with(data, slots) if OPDEMO.fullmatch(path) \
                else credits_with(data, slots) if CREDITS.fullmatch(path) else table_with(data, slots)
    return out, errors, used


def cmd_check(args) -> int:
    files, errors, used = edits(Path(args.xlsx), Path(args.private))
    patches, perrors, pused = program_patches(Path(args.xlsx), Path(args.private))
    errors += perrors
    used += pused
    if patches:
        print(f"{sum(len(v) for v in patches.values())} program string(s) to patch")
    for e in errors[:200]:
        print(e)
    print(f"{used} translated rows, {sum(len(f) for f in files.values())} file(s) to rebuild, "
          f"{len(errors)} problem(s)")
    return 1 if errors else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("export")
    p.add_argument("private")
    p.add_argument("out")
    p = sub.add_parser("export-more", help="staff roll (Credits) and program text (Program) workbook")
    p.add_argument("private")
    p.add_argument("out")
    p = sub.add_parser("check")
    p.add_argument("xlsx")
    p.add_argument("private")
    args = ap.parse_args(argv)
    return {"export": cmd_export, "export-more": cmd_export_more, "check": cmd_check}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
