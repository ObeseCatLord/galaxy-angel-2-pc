#!/usr/bin/env python3
"""Galaxy Angel II translation workbook: export every story-script text of
all five discs to one .xlsx, and import the English column back into
.gasm dumps (and a language-pack mod).

  script_xlsx.py export <private-root> <out.xlsx> [--discs zettai,mugen-1,...]
      Reads <private>/discs/<disc>/*.iso directly (the same parser as
      ga2scripts.py), the per-disc tables (adv/bg.tbl, adv/charNNN.tbl,
      bgm/bgm.tbl) and the image dump index
      <private>/research/assets-dump/<game>/index.csv, and writes one sheet
      per game (Zettai, Mugen, Eigou) plus README and Names sheets.
  script_xlsx.py check <in.xlsx> <private-root>
      Compares a (possibly edited) workbook with the discs: every ID still
      exists, its Source column is unchanged, and every English cell
      converts to the game's text encoding.
  script_xlsx.py import <in.xlsx> <private-root> <work-dir> [--build <mods-dir>]
                 [--mod-id ga2-en] [--wrap 372] [--font-metrics metrics.json]
      Writes <work-dir>/<disc>/*.gasm: a copy of each dump script that has
      English text, with those .string lines replaced.  With --build it then
      runs `ga2scripts.py build --target strm --wrap ...` per disc into
      <mods-dir>/<mod-id>/files/ and writes <mods-dir>/<mod-id>/mod.json
      ("language": "en").

Row model: one row per string-table entry that the game shows as text
(argument 0 of msg, every argument of menu/choice), in script file order
and first-use order inside the script.  The ID `<disc>:<script>:s<N>` is the
`.string sN` label of the .gasm dump (string-table index), so it is stable
across re-exports and maps back exactly.  Standard library only: the xlsx
is written and read as zip + XML.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import glob
import io
import json
import re
import struct
import sys
import unicodedata
import urllib.parse
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ga2arc  # noqa: E402
import ga2asb  # noqa: E402
import ga2font  # noqa: E402
import ga2scripts  # noqa: E402
import text_buttons  # noqa: E402

DISCS = ["zettai", "mugen-1", "mugen-2", "eigou-1", "eigou-2"]
GAME_OF = {"zettai": "zettai", "mugen-1": "mugen", "mugen-2": "mugen",
           "eigou-1": "eigou", "eigou-2": "eigou"}
SHEET_OF = {"zettai": "Zettai", "mugen": "Mugen", "eigou": "Eigou"}
GAME_TITLE = {"zettai": "Zettai Ryouiki no Tobira", "mugen": "Mugen Kairou no Kagi",
              "eigou": "Eigou Kaiki no Toki"}
FONT = "Noto Sans CJK JP"
SCN = "adv/scn/"

# builtin ids used by the state tracker (see ga2asb.BUILTINS)
B_MSG, B_MENU, B_CHOICE = 0x00, 0x01, 0x02
B_BG, B_BGM, B_BGM_STOP = 0x10, 0x11, 0x12
B_SAY, B_THINK, B_NARR_BOX, B_SAY_VOICE = 0x13, 0x14, 0x15, 0x56
B_CHARS, B_CENTER, B_LEFT, B_RIGHT, B_HIDE, B_CLEAR = 0x19, 0x1a, 0x1b, 0x1c, 0x1d, 0x1e
B_BG_TRANS, B_TEXT_SLOT = 0x36, 0x4b
SLOT_OF = {B_LEFT: 0, B_CENTER: 1, B_RIGHT: 2}

COLUMNS = [
    # (header, width, kind)  kind: text | wrap | link | edit
    ("ID", 26, "text"),
    ("Seq", 7, "text"),
    ("Disc", 8, "text"),
    ("Script", 15, "text"),
    ("Scene (dev label)", 18, "wrap"),
    ("Type", 10, "text"),
    ("Name tag (game)", 14, "wrap"),
    ("Speaker", 14, "edit"),
    ("Japanese", 48, "wrap"),
    ("Ruby", 18, "wrap"),
    ("Codes", 12, "wrap"),
    ("English", 56, "edit"),
    ("Notes", 24, "edit"),
    ("Face icon", 22, "link"),
    ("Background", 26, "link"),
    ("CG", 26, "link"),
    ("Sprite left", 24, "link"),
    ("Sprite centre", 24, "link"),
    ("Sprite right", 24, "link"),
    ("BGM", 22, "wrap"),
    ("Voice", 7, "text"),
    ("Speaker code", 16, "text"),
    ("Flags", 18, "wrap"),
    ("Source", 30, "text"),
]
COL = {h: i for i, (h, _, _) in enumerate(COLUMNS)}


# ---------------------------------------------------------------- tables

def tbl_entries(text: str, section: str) -> dict:
    """[SECTION] '#NNN =file ; comment' -> {int: (file, comment)}."""
    out = {}
    cur = None
    for line in text.splitlines():
        m = re.match(r"\s*\[(\w+)\]", line)
        if m:
            cur = m.group(1)
            continue
        if cur != section:
            continue
        m = re.match(r"\s*#(\d+)\s*=\s*([^\s;/]*)\s*(?:(?:;|//)\s*(.*))?$", line)
        if m:
            out[int(m.group(1))] = (m.group(2), (m.group(3) or "").strip())
    return out


@dataclass
class Character:
    num: int
    names: dict           # NAME index -> Japanese
    face_l: dict          # face id -> (file, label)
    face_s: dict


@dataclass
class DiscData:
    disc: str
    program: str
    bg: dict
    chars: dict
    bgm: dict
    scripts: list         # [(name, Script)]
    assets: dict          # archive path -> absolute png path
    folder_of: dict       # char num -> romanised folder


def read_all(src, allp, path: str) -> bytes:
    rec = allp.files()[path]
    return ga2arc.raic_decode(src.read("ALL.DAT", rec.b, rec.stored))


def load_assets(private: Path, disc: str) -> tuple[dict, dict]:
    game = GAME_OF[disc]
    root = private / "research" / "assets-dump" / game
    assets: dict = {}
    idx = root / "index.csv"
    if idx.exists():
        with open(idx, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["disc"] != disc or "#" in row["archive_path"]:
                    continue
                out = row["output"]
                if out:
                    assets.setdefault(row["archive_path"], str((root / out).resolve()))
    folder_of: dict = {}
    names = root / "sprites" / "names.json"
    if names.exists():
        for folder, info in json.loads(names.read_text(encoding="utf-8")).items():
            if disc in info.get("ids", {}):
                folder_of.setdefault(info["ids"][disc], folder)
    return assets, folder_of


def load_disc(private: Path, disc: str) -> DiscData:
    isos = sorted(glob.glob(str(private / "discs" / disc / "*.iso")))
    if not isos:
        raise SystemExit(f"no ISO in {private / 'discs' / disc}")
    src = ga2arc.DiscSource(isos[0])
    allp = ga2scripts.load_archive(src, "ALL.DAT")
    files = allp.files()
    bg_text = read_all(src, allp, "adv/bg.tbl").decode("cp932", "replace")
    bg = tbl_entries(bg_text, "BG")
    chars = {}
    for path in files:
        m = re.fullmatch(r"adv/char(\d+)\.tbl", path)
        if not m:
            continue
        t = read_all(src, allp, path).decode("cp932", "replace")
        names = {}
        for k, v in re.findall(r"^NAME(\d+)\s*=\s*(.*?)\s*$", t, re.M):
            names[int(k)] = v.split("//")[0].strip()
        num = int(m.group(1))
        chars[num] = Character(num, names, tbl_entries(t, "FACE_L"), tbl_entries(t, "FACE_S"))
    bgm = {}
    idx = ga2arc.Pidx.parse(src.read("IDX.DAT"))
    rec = idx.files().get("bgm/bgm.tbl")
    if rec:
        blob = src.read(idx.string(rec.a).upper(), rec.b, rec.stored or rec.size)
        data = ga2arc.raic_decode(blob) if rec.stored else blob
        bgm = tbl_entries(data.decode("cp932", "replace"), "BGMS")
    scripts = []
    for path in sorted(files):
        if path.startswith(SCN) and path.endswith(".asb"):
            scripts.append((path[len(SCN):-4], ga2asb.parse(read_all(src, allp, path))))
    assets, folder_of = load_assets(private, disc)
    return DiscData(disc, src.program(), bg, chars, bgm, scripts, assets, folder_of)


# ---------------------------------------------------------------- text helpers

KANJI = r"[\u4e00-\u9fff\u3400-\u4dbf々〆ヶ０-９Ａ-Ｚａ-ｚ]"
RUBY_RE = re.compile(rf"({KANJI}+)（([ぁ-ゖァ-ヺー・]+)）")
CODE_RE = re.compile(r"\\(s\d{1,2}|c[0-9A-Fa-f]{1,8}|v\d{1,4})")


def readable(raw: bytes) -> str:
    """cp932 bytes -> cell text: real line breaks, control codes as \\s.. etc."""
    out = []
    i = 0
    while i < len(raw):
        c = raw[i]
        if c in (0x0A, 0x09):
            out.append(chr(c))
            i += 1
            continue
        if c < 0x20 or c == 0x7F:
            out.append(f"\\x{c:02x}")
            i += 1
            continue
        n = 2 if (0x81 <= c <= 0x9F or 0xE0 <= c <= 0xFC) and i + 1 < len(raw) else 1
        try:
            out.append(raw[i:i + n].decode("cp932"))
        except UnicodeDecodeError:
            out.append("".join(f"\\x{x:02x}" for x in raw[i:i + n]))
        i += n
    return "".join(out)


def ruby_of(text: str) -> str:
    return "; ".join(f"{m.group(1)}《{m.group(2)}》" for m in RUBY_RE.finditer(text))


def codes_of(text: str) -> str:
    return " ".join("\\" + m.group(1) for m in CODE_RE.finditer(text))


TYPO = {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2026": "...",
        "\u2013": "-", "\u2014": "-", "\u00a0": " ", "\u3000": "\u3000"}


def fold_latin(ch: str) -> str:
    """Accented Latin letters have no Shift-JIS code: é -> e, â -> a. Others
    (and letters Shift-JIS has) are returned unchanged."""
    if ch.isascii() or not "\u00c0" <= ch <= "\u024f":
        return ch
    try:
        ga2asb.text_to_sjis(ch)
        return ch
    except ga2asb.AsbError:
        base = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
        return base if base.isascii() and base else ch


# Message layout (docs/modding/text.md): the dialogue box holds 3 lines of
# size 18 (62 pixels of height) in about 372 pixels; a line is spacing 4 +
# its tallest glyph, so smaller \\sNN text fits more per line and more lines.
BOX_TYPES = ("dialogue", "thought", "narration", "message")
BOX_HEIGHT = 62
MIN_SIZE = 12


def box_lines(size: int) -> int:
    return (BOX_HEIGHT + 4) // (size + 4)


def reflow(text: str) -> str:
    """Cell line breaks follow the Japanese layout; Latin text is re-wrapped
    instead.  A blank line stays a paragraph break."""
    paras = re.split(r"\n\s*\n", text)
    return "\n".join(" ".join(part.strip() for part in p.split("\n") if part.strip()) for p in paras)


def fit_message(kind: str, text: str, budget: float, metrics: dict) -> str:
    """Box messages with Latin text: reflow, then the largest size (down to
    12) whose word wrap fits the box.  Other types are only reflowed (the
    narration balloon) or kept (choices, menus, captions)."""
    if not re.search(r"[A-Za-z]{2}", text) or kind in ("choice", "menu", "caption"):
        return text
    text = reflow(text)
    if kind not in BOX_TYPES:
        return text
    base = 18
    m = re.match(r"\\s(\d{1,2})", text)
    if m:                       # an authored size: start from it
        base, text = int(m.group(1)), text[m.end():]
    best = text
    for size in range(max(base, MIN_SIZE), MIN_SIZE - 1, -1):
        candidate = text if size == 18 else f"\\s{size}{text}"
        wrapped, n = ga2font.wrap_text(candidate, budget, metrics)
        best = wrapped
        if n <= box_lines(size):
            return wrapped
    return best


def expand_buttons(text: str, japanese: str = "") -> str:
    """{circle}, {l1}, ... -> the reserved characters the engine draws as the
    input device's button icon (text_buttons.py).  Done before the line fit,
    so an icon counts as the full-width characters it is.  With the row's
    Japanese, PlayStation button names the translation kept where the
    Japanese names a button (Press the × button) become placeholders first
    (text_buttons.auto)."""
    try:
        return text_buttons.expand(text_buttons.auto(text, japanese))
    except ValueError as e:
        raise ga2asb.AsbError(str(e)) from None


def english_to_gasm(text: str) -> str:
    """English cell -> .gasm quoted-string body (raises ga2asb.AsbError when a
    character has no Shift-JIS code)."""
    text = expand_buttons(text.replace("\r\n", "\n").replace("\r", "\n"))
    text = "".join(fold_latin(TYPO.get(ch, ch)) for ch in text)
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            nxt = text[i + 1:i + 2]
            if nxt and nxt in ga2asb.CONTROL_LETTERS:
                out.append("\\")
            elif nxt == "x" and re.fullmatch(r"[0-9a-fA-F]{2}", text[i + 2:i + 4]):
                out.append(text[i:i + 4])
                i += 4
                continue
            else:
                out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == '"':
            out.append('\\"')
        else:
            out.append(ch)
        i += 1
    body = "".join(out)
    ga2asb.text_to_sjis(body)     # validate
    return body


# ---------------------------------------------------------------- script walk

@dataclass
class Row:
    sidx: int
    order: tuple
    kind: str
    func: str
    speaker: tuple | None
    state: dict
    uses: list = field(default_factory=list)
    dead: bool = False
    voice: int | None = None


def reachable(f: ga2asb.Function) -> set:
    index = {pc: i for i, (pc, _, _) in enumerate(f.ins)}
    seen: set = set()
    work = [0] if f.ins else []
    while work:
        i = work.pop()
        while i < len(f.ins) and i not in seen:
            seen.add(i)
            pc, op, a = f.ins[i]
            if op in ("B", "BZ", "BNZ"):
                t = index.get(struct.unpack_from("<I", a)[0])
                if t is not None:
                    work.append(t)
                if op == "B":
                    break
            elif op == "BTBL":
                work += [index[t] for t in struct.unpack_from(f"<{a[0]}I", a, 1) if t in index]
                break
            elif op in ("JMPL", "JMPG", "RET", "RETN"):
                break
            i += 1
    return {f.ins[i][0] for i in seen}


class State:
    def __init__(self):
        self.bg = None          # (bg id, file, label) or None
        self.slots = [None, None, None]   # (char, face)
        self.bgm = None
        self.narr_box = False

    def copy(self) -> "State":
        s = State()
        s.bg, s.slots, s.bgm, s.narr_box = self.bg, list(self.slots), self.bgm, self.narr_box
        return s


def walk_script(name: str, sc: ga2asb.Script, dd: DiscData, st: State) -> list:
    """Interpret the scene commands of one script in order.  Returns rows
    (one per displayed string, first use) in first-use order."""
    strings = sc.string_at
    sid = {o: i for i, (o, _) in enumerate(sc.strings)}
    rows: dict = {}
    seq = 0
    for fi, f in enumerate(sc.funcs):
        live = reachable(f)
        fname = readable(strings.get(f.name, b""))
        stack: list = []
        speaker = None          # (kind, char, face, voice) pending for the next msg
        text_slot = False
        for pc, op, a in f.ins:
            if op == "PUSHI":
                stack.append((pc, struct.unpack("<i", a)[0]))
                continue
            if op in ("PUSHL", "PUSHG", "PUSHF", "PUSHR"):
                stack.append(None)
                continue
            if op != "CALLB":
                if op in ("POP", "POPL", "POPG", "POPF", "BZ", "BNZ", "BTBL", "RET",
                          "NEG", "NOT"):
                    if stack:
                        stack.pop()
                    if op in ("NEG", "NOT"):
                        stack.append(None)
                elif op in ("ADD", "SUB", "MUL", "DIV", "MOD", "AND", "OR", "EQ", "NEQ",
                            "LT", "GT", "LEQ", "GEQ"):
                    del stack[-2:]
                    stack.append(None)
                elif op == "EXCH" and len(stack) >= 2:
                    stack[-1], stack[-2] = stack[-2], stack[-1]
                elif op in ("CALLL", "CALLG"):
                    n = a[-1]
                    if n:
                        del stack[-n:]
                continue
            bid, n = struct.unpack_from("<I", a)[0], a[4]
            args = stack[-n:] if n else []
            if n:
                del stack[-n:]
            args = [None] * (n - len(args)) + args
            vals = [x[1] if x else None for x in args]

            def v(k, default=None):
                return vals[k] if k < len(vals) and vals[k] is not None else default

            if bid in (B_BG, B_BG_TRANS) and n:
                bgid = v(0)
                if bgid is None:
                    st.bg = ("?", "(computed)", "")
                elif bgid < 0:
                    st.bg = None
                else:
                    fn, lab = dd.bg.get(bgid, (f"bg #{bgid}", "not in bg.tbl"))
                    st.bg = (bgid, fn, lab)
            elif bid == B_BGM and n:
                t = v(0)
                if t is None:
                    st.bgm = "(computed)"
                elif not dd.bgm:
                    st.bgm = f"track {t}"        # no bgm.tbl on this disc (Mugen, Eigou)
                else:
                    fn, lab = dd.bgm.get(t * 10, ("?", "not in bgm.tbl"))
                    st.bgm = f"#{t * 10:03d} {fn}" + (f" ({lab})" if lab else "")
            elif bid == B_BGM_STOP:
                st.bgm = None
            elif bid in (B_SAY, B_THINK, B_SAY_VOICE) and n:
                o = 1 if bid == B_SAY_VOICE else 0
                speaker = ("think" if bid == B_THINK else "say", v(o), v(o + 1), v(o + 2))
            elif bid == B_NARR_BOX:
                st.narr_box = bool(v(0, 0))
            elif bid == B_TEXT_SLOT:
                text_slot = True
            elif bid == B_CHARS and n >= 6:
                st.slots = [None, None, None]
                for k in range(3):
                    c, fc = v(2 * k), v(2 * k + 1)
                    if c is not None and c >= 0:
                        st.slots[k] = (c & 0xFFFF, (fc or 0) & 0xFFFF)
            elif bid in SLOT_OF and n:
                c = v(0)
                if c is not None and c >= 0:
                    c &= 0xFFFF
                    st.slots = [None if s and s[0] == c else s for s in st.slots]
                    st.slots[SLOT_OF[bid]] = (c, (v(1) or 0) & 0xFFFF)
            elif bid == B_HIDE and n:
                c = v(0)
                if c is not None:
                    st.slots = [None if s and s[0] == (c & 0xFFFF) else s for s in st.slots]
            elif bid == B_CLEAR:
                st.slots = [None, None, None]
            elif bid in (B_MSG, B_MENU, B_CHOICE):
                pos = (0,) if bid == B_MSG else range(n)
                for k in pos:
                    if k >= len(args) or args[k] is None:
                        continue
                    spc, off = args[k]
                    if sc.string_refs.get(spc) != bid:
                        continue
                    i = sid[off]
                    kind = {B_MENU: "menu", B_CHOICE: "choice"}.get(bid)
                    if kind is None:
                        if speaker is None:
                            kind = "message"
                        elif speaker[0] == "think":
                            kind = "thought"
                        elif speaker[1] is not None and (speaker[1] >> 16) & 0x20:
                            kind = "narration"
                        else:
                            kind = "dialogue"
                        if st.narr_box:
                            kind = "narration (box)"
                        elif text_slot:
                            kind = "caption"
                    if i not in rows:
                        rows[i] = Row(i, (seq,), kind, fname,
                                      speaker if bid == B_MSG else None,
                                      snapshot(st), dead=pc not in live and spc not in live,
                                      voice=v(1) if bid == B_MSG and n > 1 else None)
                        seq += 1
                    else:
                        r = rows[i]
                        if pc in live or spc in live:
                            r.dead = False
                    rows[i].uses.append(kind)
                if bid == B_MSG:
                    speaker = None
                    text_slot = False
            stack = []
    # strings the linear walk could not tie to a call (should not happen)
    for pc, bid in sc.string_refs.items():
        f = next(f for f in sc.funcs if f.off <= pc < f.off + f.size)
        for p2, op, a in f.ins:
            if p2 == pc:
                i = sid[struct.unpack("<i", a)[0]]
                if i not in rows:
                    rows[i] = Row(i, (seq,), {B_MENU: "menu", B_CHOICE: "choice"}.get(bid, "message"),
                                  readable(strings.get(f.name, b"")), None, snapshot(st),
                                  uses=["position unknown"])
                    seq += 1
    return [rows[k] for k in sorted(rows, key=lambda k: rows[k].order)]


def snapshot(st: State) -> dict:
    return {"bg": st.bg, "slots": tuple(st.slots), "bgm": st.bgm}


# ---------------------------------------------------------------- rows -> cells

def link(path: str | None, label: str):
    if path:
        return ("link", "file://" + urllib.parse.quote(path), label)
    return label


def char_label(dd: DiscData, c: int) -> str:
    ch = dd.chars.get(c)
    folder = dd.folder_of.get(c)
    jp = ch.names.get(0, "") if ch else ""
    return folder or jp or f"char {c}"


def sprite_cell(dd: DiscData, slot):
    if not slot:
        return ""
    c, face = slot
    ch = dd.chars.get(c)
    if not ch:
        return f"char {c} face {face} (no table)"
    fn, lab = ch.face_l.get(face, (None, ""))
    if not fn:
        return f"{char_label(dd, c)}: face #{face} (not in FACE_L)"
    stem = fn.rsplit(".", 1)[0]
    return link(dd.assets.get("adv/face/" + fn), f"{char_label(dd, c)}: {stem} {lab}".strip())


def face_cell(dd: DiscData, speaker):
    if not speaker or speaker[1] is None or speaker[2] is None or speaker[2] < 0:
        return ""
    c = speaker[1] & 0xFFFF
    face = speaker[2] & 0xFFFF
    ch = dd.chars.get(c)
    if not ch:
        return ""
    fn, lab = ch.face_s.get(face, (None, ""))
    if not fn:
        # Eigou's tables have an empty [FACE_S]; the icons follow the
        # facNNN_xxxx numbering of the portrait id
        guess = f"fac{c:03d}_{face:04d}.agi"
        if "adv/face/" + guess not in dd.assets:
            return ""
        fn, lab = guess, ch.face_l.get(face, ("", ""))[1]
    stem = fn.rsplit(".", 1)[0]
    return link(dd.assets.get("adv/face/" + fn), f"{char_label(dd, c)}: {stem} {lab}".strip())


def name_tag(dd: DiscData, speaker) -> str:
    if not speaker or speaker[1] is None:
        return "" if not speaker else "(computed)"
    raw = speaker[1]
    if raw < 0:
        return ""
    c, hi = raw & 0xFFFF, (raw >> 16) & 0xFF
    if hi & 0x20:
        return ""
    ch = dd.chars.get(c)
    if not ch:
        return f"char {c}"
    nm = ch.names.get(hi & 0x0F) or ch.names.get(0, f"char {c}")
    return nm


def speaker_code(speaker) -> str:
    if not speaker:
        return ""
    kind, c, face, _voice = speaker
    fmt = lambda x: "?" if x is None else (hex(x) if x >= 0x10000 else str(x))
    return f"{kind} {fmt(c)}/{fmt(face)}"


def msg_arg(r) -> str:
    """Second msg argument (Mugen/Eigou), shown next to the speaker code."""
    return "" if r.voice is None else f"; msg {r.voice}"


def bg_cells(dd: DiscData, bg):
    if not bg:
        return "", ""
    bgid, fn, lab = bg
    stem = fn.rsplit(".", 1)[0]
    cell = link(dd.assets.get("adv/bg/" + fn), f"{stem} {lab}".strip())
    if stem.startswith("iv"):
        return "", cell
    return cell, ""


def build_rows(dd: DiscData, start_seq: int) -> tuple[list, dict]:
    """All rows of one disc as cell lists."""
    out = []
    seq = start_seq
    st = State()
    prev_externs: set = set()
    stats = {"scripts": 0, "rows": 0, "dead": 0}
    for name, sc in dd.scripts:
        # carry the scene state over only when the previous script jumps here
        if (name + ".asb") not in prev_externs:
            st = State()
        rows = walk_script(name, sc, dd, st)
        prev_externs = {e.decode("cp932", "replace").lower() for _, e in sc.externs}
        strings = sc.strings
        func_names = {f.name for f in sc.funcs}
        ext_names = {struct.unpack_from("<I", a, 4)[0] for f in sc.funcs for _, op, a in f.ins
                     if op in ("JMPG", "CALLG")}
        stats["scripts"] += 1
        for r in rows:
            off, raw = strings[r.sidx]
            text = readable(raw)
            flags = []
            if r.dead:
                flags.append("unreachable code")
                stats["dead"] += 1
            if "position unknown" in r.uses:
                flags.append("scene state unknown")
            if len(r.uses) > 1:
                kinds = sorted(set(r.uses))
                flags.append(f"shown {len(r.uses)}x" + (f" ({'/'.join(kinds)})" if len(kinds) > 1 else ""))
            if off in func_names or off in ext_names:
                flags.append("also a function name: do not translate")
            if name.startswith("test") or "debug" in name:
                flags.append("debug script")
            bgc, cgc = bg_cells(dd, r.state["bg"])
            sl = r.state["slots"]
            voice = r.speaker[3] if r.speaker else None
            cells = [""] * len(COLUMNS)
            cells[COL["ID"]] = f"{dd.disc}:{name}:s{r.sidx}"
            cells[COL["Seq"]] = seq
            cells[COL["Disc"]] = dd.disc
            cells[COL["Script"]] = name
            cells[COL["Scene (dev label)"]] = r.func
            cells[COL["Type"]] = r.kind
            cells[COL["Name tag (game)"]] = name_tag(dd, r.speaker)
            cells[COL["Japanese"]] = text
            cells[COL["Ruby"]] = ruby_of(text)
            cells[COL["Codes"]] = codes_of(text)
            cells[COL["Face icon"]] = face_cell(dd, r.speaker)
            cells[COL["Background"]] = bgc
            cells[COL["CG"]] = cgc
            cells[COL["Sprite left"]] = sprite_cell(dd, sl[0])
            cells[COL["Sprite centre"]] = sprite_cell(dd, sl[1])
            cells[COL["Sprite right"]] = sprite_cell(dd, sl[2])
            cells[COL["BGM"]] = r.state["bgm"] or ""
            cells[COL["Voice"]] = "" if voice is None or voice < 0 else voice
            cells[COL["Speaker code"]] = (speaker_code(r.speaker) + msg_arg(r)).lstrip("; ")
            cells[COL["Flags"]] = "; ".join(flags)
            cells[COL["Source"]] = ga2asb.sjis_to_text(raw)
            out.append(cells)
            seq += 1
            stats["rows"] += 1
    return out, stats


def names_rows(dd: DiscData, speaker_counts: dict) -> list:
    out = []
    for num in sorted(dd.chars):
        ch = dd.chars[num]
        for k in sorted(ch.names):
            out.append([f"{dd.disc}:char{num:03d}:NAME{k:02d}", dd.disc, num, f"NAME{k:02d}",
                        ch.names[k], dd.folder_of.get(num, ""), speaker_counts.get((num, k), 0),
                        "", ""])
    return out


# ---------------------------------------------------------------- xlsx writer

def col_letter(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


_BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def x(s: str) -> str:
    return escape(_BAD_XML.sub(lambda m: f"\\x{ord(m.group()):02x}", s), {'"': "&quot;"})


# style ids (cellXfs order in STYLES)
S_DEFAULT, S_WRAP, S_HEAD, S_LINK, S_EDIT, S_TITLE = 0, 1, 2, 3, 4, 5

STYLES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="4">
<font><sz val="10"/><name val="{FONT}"/><family val="2"/></font>
<font><b/><sz val="10"/><name val="{FONT}"/><family val="2"/></font>
<font><u/><sz val="10"/><color rgb="FF0563C1"/><name val="{FONT}"/><family val="2"/></font>
<font><b/><sz val="14"/><name val="{FONT}"/><family val="2"/></font>
</fonts>
<fills count="4">
<fill><patternFill patternType="none"/></fill>
<fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFD9E1F2"/><bgColor indexed="64"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFFFF7D6"/><bgColor indexed="64"/></patternFill></fill>
</fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="6">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top"/></xf>
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
<xf numFmtId="0" fontId="0" fillId="3" borderId="0" xfId="0" applyFill="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
<xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1"/>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>
"""


def cell_xml(ref: str, value, style: int) -> str:
    if value is None or value == "":
        return f'<c r="{ref}" s="{style}"/>' if style in (S_EDIT, S_HEAD) else ""
    if isinstance(value, tuple) and value[0] == "link":
        _, url, label = value
        f = 'HYPERLINK("{}","{}")'.format(url.replace('"', '""'), label.replace('"', '""'))
        return f'<c r="{ref}" s="{S_LINK}" t="str"><f>{x(f)}</f><v>{x(label)}</v></c>'
    if isinstance(value, int):
        return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{x(str(value))}</t></is></c>'


def sheet_xml(columns: list, rows: list, freeze: bool = True, autofilter: bool = True,
              row_height: bool = True) -> str:
    kinds = [k for _, _, k in columns]
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
             'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">']
    last = col_letter(len(columns) - 1)
    parts.append(f'<dimension ref="A1:{last}{len(rows) + 1}"/>')
    if freeze:
        parts.append('<sheetViews><sheetView workbookViewId="0" zoomScale="100">'
                     '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
                     '<selection pane="bottomLeft" activeCell="A2" sqref="A2"/></sheetView></sheetViews>')
    else:
        parts.append('<sheetViews><sheetView workbookViewId="0"/></sheetViews>')
    parts.append('<sheetFormatPr defaultRowHeight="15"/>')
    parts.append("<cols>" + "".join(
        f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>'
        for i, (_, w, _) in enumerate(columns)) + "</cols>")
    parts.append("<sheetData>")
    head = "".join(cell_xml(f"{col_letter(i)}1", h, S_HEAD) for i, (h, _, _) in enumerate(columns))
    parts.append(f'<row r="1">{head}</row>')
    style_of = {"text": S_DEFAULT, "wrap": S_WRAP, "link": S_LINK, "edit": S_EDIT}
    for rn, row in enumerate(rows, 2):
        lines = 1
        cells = []
        for i, val in enumerate(row):
            if isinstance(val, str) and kinds[i] in ("wrap", "edit") and val:
                lines = max(lines, val.count("\n") + 1)
            cells.append(cell_xml(f"{col_letter(i)}{rn}", val, style_of[kinds[i]]))
        ht = f' ht="{min(lines, 8) * 14 + 3}" customHeight="1"' if row_height else ""
        parts.append(f'<row r="{rn}"{ht}>' + "".join(cells) + "</row>")
    parts.append("</sheetData>")
    if autofilter and rows:
        parts.append(f'<autoFilter ref="A1:{last}{len(rows) + 1}"/>')
    parts.append("</worksheet>")
    return "".join(parts)


def write_xlsx(path: Path, sheets: list) -> None:
    """sheets: [(name, sheet_xml, filter_ref or None)]"""
    ct = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
          '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
          '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
          '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>']
    for i in range(len(sheets)):
        ct.append(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
                  'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
    ct.append("</Types>")
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
            '</Relationships>')
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            '<dc:title>Galaxy Angel II script</dc:title><dc:creator>script_xlsx.py</dc:creator>'
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created></cp:coreProperties>')
    app = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
           '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
           '<Application>script_xlsx.py</Application></Properties>')
    wb = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
          '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
          '<bookViews><workbookView activeTab="0"/></bookViews><sheets>']
    for i, (name, _, _) in enumerate(sheets):
        wb.append(f'<sheet name="{x(name)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>')
    wb.append("</sheets>")
    dn = [f'<definedName name="_xlnm._FilterDatabase" localSheetId="{i}" hidden="1">'
          f"'{x(name)}'!{ref}</definedName>"
          for i, (name, _, ref) in enumerate(sheets) if ref]
    if dn:
        wb.append("<definedNames>" + "".join(dn) + "</definedNames>")
    wb.append("</workbook>")
    wrels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
    for i in range(len(sheets)):
        wrels.append(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>')
    wrels.append(f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>')
    wrels.append("</Relationships>")
    tmp = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr("[Content_Types].xml", "".join(ct))
        z.writestr("_rels/.rels", rels)
        z.writestr("docProps/core.xml", core)
        z.writestr("docProps/app.xml", app)
        z.writestr("xl/workbook.xml", "".join(wb))
        z.writestr("xl/_rels/workbook.xml.rels", "".join(wrels))
        z.writestr("xl/styles.xml", STYLES)
        for i, (_, data, _) in enumerate(sheets):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", data)
    tmp.replace(path)


# ---------------------------------------------------------------- xlsx reader

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
RELNS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _col_index(ref: str) -> int:
    n = 0
    for ch in ref:
        if ch.isalpha():
            n = n * 26 + ord(ch.upper()) - 64
        else:
            break
    return n - 1


def _text_of(el) -> str:
    """Text of a <si> or <is> element (plain or rich-text runs; phonetic
    runs <rPh> are skipped)."""
    out = []
    for node in el:
        tag = node.tag.split("}")[-1]
        if tag == "t":
            out.append(node.text or "")
        elif tag == "r":
            t = node.find("m:t", NS)
            if t is not None:
                out.append(t.text or "")
    return "".join(out)


def read_xlsx(path: Path) -> dict:
    """{sheet name: [row lists of str]} for any xlsx (Excel, LibreOffice or
    this tool)."""
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        shared = [_text_of(si) for si in root.findall("m:si", NS)]
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    target = {r.get("Id"): r.get("Target") for r in rels.iter(RELNS + "Relationship")}
    out = {}
    for sh in wb.find("m:sheets", NS):
        rid = sh.get("{%s}id" % NS["r"])
        t = target[rid].lstrip("/")
        part = t if t.startswith("xl/") else "xl/" + t
        rows = []
        for _, el in ET.iterparse(io.BytesIO(z.read(part))):
            if el.tag != "{%s}row" % NS["m"]:
                continue
            row: list = []
            for c in el.findall("m:c", NS):
                i = _col_index(c.get("r", "")) if c.get("r") else len(row)
                t_ = c.get("t")
                if t_ == "inlineStr":
                    is_ = c.find("m:is", NS)
                    val = _text_of(is_) if is_ is not None else ""
                else:
                    vv = c.find("m:v", NS)
                    val = vv.text if vv is not None and vv.text is not None else ""
                    if t_ == "s" and val != "":
                        val = shared[int(val)]
                while len(row) <= i:
                    row.append("")
                row[i] = val
            rows.append(row)
            el.clear()
        out[sh.get("name")] = rows
    return out


# ---------------------------------------------------------------- README

README = [
    ("Galaxy Angel II trilogy: script workbook for translation", S_TITLE),
    ("", 0),
    ("Made by tools/modding/script_xlsx.py from the disc images (export). Re-running export rebuilds the "
     "workbook from the discs and overwrites it: keep your translated copy under another name, or merge "
     "by ID.", 0),
    ("One sheet per game (Zettai, Mugen, Eigou). Two-disc games keep both discs in one sheet (Disc "
     "column), disc 1 first. Rows are in script file order, and in order of first use inside each script; "
     "Seq restores that order after sorting or filtering.", 0),
    ("", 0),
    ("Columns", S_HEAD),
    ("ID: <disc>:<script>:s<N>. N is the string's index in that script's string table, the .string sN "
     "label of the .gasm dump. It is the key the importer uses. Never edit, delete or duplicate it.", 0),
    ("Seq, Disc, Script: position. Script is the adv/scn/<script>.asb file.", 0),
    ("Scene (dev label): the developers' name of the script function (not shown in the game).", 0),
    ("Type: from the engine call: dialogue (say), thought (think balloon), narration (say with the "
     "no-name flag 0x20), narration (box) (large narration balloon), caption (free text position), "
     "message (msg with no speaker call before it), choice (story choice option), menu (system or "
     "debug menu option).", 0),
    ("Name tag (game): the name the game puts in the name box, from NAMEnn of adv/charNNN.tbl for the "
     "character id of the say/think call. Empty for narration.", 0),
    ("Speaker: EMPTY, for you. Fill in who really speaks (the game's tag can be a group name or hidden).", 0),
    ("Japanese: the text exactly as stored and shown. Cell line breaks are the game's line breaks. "
     "Control codes are kept as written: \\sNN glyph size, \\cAABBGGRR colour, \\vNNN typing speed.", 0),
    ("Ruby: the game has no ruby (furigana) code; the text parser only knows \\s \\c \\v. A few lines "
     "print a reading in full-width brackets after a word; those are listed here as base《reading》. "
     "They stay in the Japanese column because the game prints them.", 0),
    ("Codes: the control codes found in the Japanese text. Put the same codes in the English text where "
     "they apply (for example a leading \\s20).", 0),
    ("English: EMPTY, for you (the only column the importer reads, besides ID and Source).", 0),
    ("Button names: where the game prints a controller button in the text (the Japanese shows ○ × △ □ L1 R1 "
     "...), write a placeholder in the English: {circle} {cross} {square} {triangle} {l1} {r1} {l2} {r2} "
     "{l3} {r3} {start} {select} {dpad} {lstick} {rstick} {up} {down} {left} {right}. The game draws the "
     "icon of the player's input device there (PlayStation, Xbox, keyboard, VR), at the text size. Leave "
     "a space around it like a word; {l1}, {r1}, {l2}, {r2}, {start}, {select} are two characters wide. Any "
     "other {word} is an error.", 0),
    ("Notes: EMPTY, for you. Not imported.", 0),
    ("Face icon: the 88x88 face shown in the message window for this line (FACE_S of the speaker's "
     "table), with a link to the dumped PNG.", 0),
    ("Background / CG: what the bg / bg_trans calls last put on screen before this line. Ids below 100 "
     "in bg.tbl are backgrounds (ad_bk_*), event CGs are iv*. Linked to the PNG of the image dump.", 0),
    ("Sprite left / centre / right: standing portraits placed by chars / char_left / char_center / "
     "char_right and removed by char_hide / chars_clear; character, file (adv/face/chaNNN_xxxx) and the "
     "expression from the table, linked to the PNG.", 0),
    ("BGM: last bgm call, cleared by bgm_stop. On Zettai track N is shown as bgm.tbl entry #N*10 "
     "(inferred from the character themes); Mugen and Eigou have no bgm.tbl, so only the track "
     "number is shown.", 0),
    ("Voice, Speaker code: voice clip number of the say/think call, and the raw say/think arguments "
     "(char/face; values above 0xffff carry flag bits) plus the second msg argument on Mugen/Eigou, "
     "for reference.", 0),
    ("Flags: unreachable code (dead text the game never shows), shown Nx (the same string is used "
     "several times, e.g. a choice option that the next line repeats), debug script.", 0),
    ("Source: the exact stored text in .gasm notation. The importer refuses a row whose Source no longer "
     "matches the disc. Do not edit.", 0),
    ("", 0),
    ("Writing the English", S_HEAD),
    ("Write each message as one paragraph. The build tool word-wraps at spaces to the dialogue box width "
     "(372 pixels, 3 lines of size 18, about 40-45 characters per line) with the game's own font metrics. "
     "A line break you type in the cell is kept as a forced break.", 0),
    ("A message box holds 3 lines; longer text is reported by the build and should be split by the script "
     "editor, not here (one row = one message).", 0),
    ("Only characters that exist in Shift-JIS can be shown: plain ASCII is drawn proportionally; there "
     "are no accented letters. Curly quotes, the ellipsis character and dashes are converted to ASCII "
     "by the importer; other characters are reported as errors.", 0),
    ("Leave English empty to keep the Japanese line. A choice option and a message that repeats it can "
     "be one shared string (Flags: shown Nx): one translation serves both.", 0),
    ("Names in the name box come from the character tables, not from this sheet: the Names sheet lists "
     "them for reference (not imported yet).", 0),
    ("", 0),
    ("Tracking limits", S_HEAD),
    ("Scene state (background, CG, sprites, BGM) is interpreted in code order inside each script. "
     "Branches after a choice are read one after the other, so the second branch starts with the state "
     "left by the first. The state is carried into the next script only when the previous script jumps "
     "to it; otherwise it starts empty until the script's first bg / char call.", 0),
    ("Values computed at run time (variables) show as (computed). The expression shown for a sprite is "
     "the one its char_* call set; say/think can change the face icon, and they may also change the "
     "standing sprite's expression in the game, which is not modelled.", 0),
    ("", 0),
    ("Rebuilding and importing", S_HEAD),
    ("python3 tools/modding/script_xlsx.py export <private-root> ga2_script.xlsx", 0),
    ("python3 tools/modding/script_xlsx.py check ga2_script.xlsx <private-root>", 0),
    ("python3 tools/modding/script_xlsx.py import ga2_script.xlsx <private-root> <work-dir> --build <mods-dir>", 0),
    ("See docs/modding/translation.md in the source repository.", 0),
]


# ---------------------------------------------------------------- commands

def cmd_export(args) -> int:
    private = Path(args.private).resolve()
    discs = args.discs.split(",") if args.discs else DISCS
    by_game: dict = {}
    names: list = []
    report = []
    for disc in discs:
        print(f"{disc}: reading disc and tables", flush=True)
        dd = load_disc(private, disc)
        game = GAME_OF[disc]
        rows = by_game.setdefault(game, [])
        cells, stats = build_rows(dd, len(rows) + 1)
        rows.extend(cells)
        counts: dict = {}
        for _, sc in dd.scripts:
            for f in sc.funcs:
                st = []
                for _, op, a in f.ins:
                    if op == "PUSHI":
                        st.append(struct.unpack("<i", a)[0])
                        continue
                    if op == "CALLB" and struct.unpack_from("<I", a)[0] in (B_SAY, B_THINK) and a[4] and len(st) >= a[4]:
                        c = st[-a[4]]
                        if c >= 0:
                            key = (c & 0xFFFF, (c >> 16) & 0x0F)
                            counts[key] = counts.get(key, 0) + 1
                    st = [] if op != "PUSHI" else st
        names.extend(names_rows(dd, counts))
        report.append((disc, stats))
        print(f"{disc}: {stats['scripts']} scripts, {stats['rows']} rows ({stats['dead']} unreachable)",
              flush=True)
    sheets = [("README", readme_sheet(report), None)]
    for game in ("zettai", "mugen", "eigou"):
        if game in by_game:
            rows = by_game[game]
            ref = f"$A$1:${col_letter(len(COLUMNS) - 1)}${len(rows) + 1}"
            sheets.append((SHEET_OF[game], sheet_xml(COLUMNS, rows), ref))
    ncols = [("ID", 26, "text"), ("Disc", 8, "text"), ("Char id", 8, "text"), ("Key", 8, "text"),
             ("Japanese", 22, "wrap"), ("Romanised (asset dump)", 22, "text"),
             ("Lines (say/think)", 10, "text"), ("English", 24, "edit"), ("Notes", 30, "edit")]
    ref = f"$A$1:${col_letter(len(ncols) - 1)}${len(names) + 1}"
    sheets.append(("Names", sheet_xml(ncols, names, row_height=False), ref))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_xlsx(out, sheets)
    total = sum(len(v) for v in by_game.values())
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB): " +
          ", ".join(f"{SHEET_OF[g]} {len(v)}" for g, v in by_game.items()) +
          f", total {total} rows, Names {len(names)}")
    return 0


def readme_sheet(report) -> str:
    lines = list(README)
    lines.append(("", 0))
    lines.append(("Rows per disc in this export", S_HEAD))
    for disc, st in report:
        lines.append((f"{disc}: {st['scripts']} scripts, {st['rows']} rows "
                      f"({st['dead']} in unreachable code)", 0))
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<sheetViews><sheetView workbookViewId="0" tabSelected="1"/></sheetViews>'
             '<sheetFormatPr defaultRowHeight="15"/>'
             '<cols><col min="1" max="1" width="150" customWidth="1"/></cols><sheetData>']
    for i, (t, s) in enumerate(lines, 1):
        style = s if s else S_WRAP
        parts.append(f'<row r="{i}">' + cell_xml(f"A{i}", t, style) + "</row>")
    parts.append("</sheetData></worksheet>")
    return "".join(parts)


def workbook_rows(sheets: dict, path: Path) -> list:
    """[(sheet, row number, ID, English, Source, Type)] from the game sheets
    of read_xlsx(path)."""
    out = []
    for name, rows in sheets.items():
        if name not in SHEET_OF.values() or not rows:
            continue
        head = rows[0]
        try:
            ci, ce, cs = head.index("ID"), head.index("English"), head.index("Source")
        except ValueError:
            raise SystemExit(f"{path}: sheet {name} lacks an ID, English or Source column")
        ct = head.index("Type") if "Type" in head else len(head) + 1
        for rn, row in enumerate(rows[1:], 2):
            get = lambda k: row[k] if k < len(row) else ""
            if get(ci):
                out.append((name, rn, get(ci), get(ce), get(cs), get(ct)))
    return out


def check_rows(wb_rows: list, private: Path, fit=None) -> tuple[dict, list, list]:
    """Validate IDs, Source and English.  Returns ({disc: {script: {sidx:
    english body}}}, errors, IDs marked OMITTED).  fit(disc, type, text)
    lays out the English for the message box before it is encoded."""
    errors = []
    omitted = []
    seen = set()
    edits: dict = {}
    cache: dict = {}
    dumps = private / "research" / "modding" / "dumps"
    for sheet, rn, rid, eng, source, kind in wb_rows:
        where = f"{sheet}!row {rn} ({rid})"
        m = re.fullmatch(r"([a-z0-9-]+):([A-Za-z0-9_.-]+):s(\d+)", rid)
        if not m or m.group(1) not in DISCS:
            errors.append(f"{where}: malformed ID")
            continue
        if rid in seen:
            errors.append(f"{where}: duplicate ID")
            continue
        seen.add(rid)
        disc, script, sidx = m.group(1), m.group(2), int(m.group(3))
        key = (disc, script)
        if key not in cache:
            g = dumps / disc / f"{script}.gasm"
            cache[key] = gasm_strings(g.read_text(encoding="utf-8")) if g.exists() else None
        table = cache[key]
        if table is None:
            errors.append(f"{where}: no dump file {dumps / disc / (script + '.gasm')}")
            continue
        if sidx not in table:
            errors.append(f"{where}: string s{sidx} not in the dump")
            continue
        if table[sidx] != source:
            errors.append(f"{where}: Source differs from the dump (stale workbook or edited dump)")
            continue
        if eng.strip() == OMITTED:
            omitted.append(rid)
            continue
        if eng.strip():
            try:
                text = expand_buttons(eng.strip("\n"), source)
                if fit:
                    text = fit(disc, kind, text)
                body = english_to_gasm(text)
            except ga2asb.AsbError as e:
                errors.append(f"{where}: {e}")
                continue
            edits.setdefault(disc, {}).setdefault(script, {})[sidx] = body
    return edits, errors, omitted


NAME_ID = re.compile(r"([a-z0-9-]+):char(\d+):NAME(\d+)")


def name_edits(sheets: dict) -> tuple[dict, list]:
    """Names sheet -> ({disc: {char: {NAME index: English}}}, errors)."""
    rows = sheets.get("Names") or []
    if not rows:
        return {}, []
    head = rows[0]
    try:
        ci, ce = head.index("ID"), head.index("English")
    except ValueError:
        return {}, ["Names: no ID or English column"]
    edits: dict = {}
    errors = []
    for rn, row in enumerate(rows[1:], 2):
        get = lambda k: row[k].strip() if k < len(row) else ""
        rid, eng = get(ci), get(ce)
        if not rid or not eng or eng == OMITTED:
            continue
        m = NAME_ID.fullmatch(rid)
        if not m or m.group(1) not in DISCS:
            errors.append(f"Names!row {rn} ({rid}): malformed ID")
            continue
        eng = "".join(fold_latin(TYPO.get(ch, ch)) for ch in eng)
        if "\n" in eng or "\\" in eng or ";" in eng or "//" in eng:
            errors.append(f"Names!row {rn} ({rid}): a name cannot contain a line break, backslash, ';' or '//'")
            continue
        try:
            ga2asb.text_to_sjis(eng)
        except ga2asb.AsbError as e:
            errors.append(f"Names!row {rn} ({rid}): {e}")
            continue
        edits.setdefault(m.group(1), {}).setdefault(int(m.group(2)), {})[int(m.group(3))] = eng
    return edits, errors


def write_name_tables(private: Path, disc: str, names: dict, out: Path) -> int:
    """Writes out/adv/charNNN.tbl (the disc's table with the NAMEnn values
    replaced) for every character with English names; returns the count."""
    iso = sorted(glob.glob(str(private / "discs" / disc / "*.iso")))[0]
    src = ga2arc.DiscSource(iso)
    allp = ga2scripts.load_archive(src, "ALL.DAT")
    n = 0
    for char, table in sorted(names.items()):
        path = f"adv/char{char:03d}.tbl"
        if path not in allp.files():
            raise SystemExit(f"{disc}: no {path} on the disc")
        data = read_all(src, allp, path)

        def repl(m):
            idx = int(m.group(2))
            if idx not in table:
                return m.group(0)
            return m.group(1) + ga2asb.text_to_sjis(table[idx]) + m.group(4)

        new, count = re.subn(rb"(?m)^(NAME(\d+)\s*=)([^\r\n]*?)(\s*(?://[^\r\n]*)?\r?)$", repl, data)
        if not count:
            raise SystemExit(f"{disc}: {path} has no NAME lines")
        dst = out / path
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(new)
        n += 1
    return n


# An English cell holding only this marker was left for a human translator:
# the line keeps its Japanese text.
OMITTED = "<omitted>"

STRING_LINE = re.compile(r'^\.string\s+s(\d+)\s+"((?:[^"\\]|\\.)*)"')


def gasm_strings(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        m = STRING_LINE.match(line)
        if m:
            out[int(m.group(1))] = m.group(2)
    return out


def cmd_check(args) -> int:
    sheets = read_xlsx(Path(args.xlsx))
    edits, errors, omitted = check_rows(workbook_rows(sheets, Path(args.xlsx)), Path(args.private))
    names, name_errors = name_edits(sheets)
    errors += name_errors
    for e in errors[:200]:
        print(e)
    n = sum(len(s) for d in edits.values() for s in d.values())
    nn = sum(len(t) for d in names.values() for t in d.values())
    print(f"{n} translated rows, {nn} character names, {len(omitted)} marked {OMITTED} (kept in Japanese), "
          f"{len(errors)} problem(s)")
    return 1 if errors else 0


def cmd_import(args) -> int:
    private = Path(args.private).resolve()
    sheets = read_xlsx(Path(args.xlsx))
    metrics_of: dict = {}

    def fit(disc: str, kind: str, text: str) -> str:
        if disc not in metrics_of:
            if args.font_metrics:
                metrics_of[disc] = ga2font.load_metrics(args.font_metrics)
            else:
                iso = sorted(glob.glob(str(private / "discs" / disc / "*.iso")))[0]
                src = ga2arc.DiscSource(iso)
                metrics_of[disc] = ga2font.metrics_dict(ga2font.find_metrics(src.read(src.program()))[1])
        return fit_message(kind, text, args.wrap, metrics_of[disc])

    edits, errors, omitted = check_rows(workbook_rows(sheets, Path(args.xlsx)), private,
                                        None if args.no_fit else fit)
    names, name_errors = name_edits(sheets)
    errors += name_errors
    if errors:
        for e in errors[:200]:
            print(e)
        raise SystemExit(f"{len(errors)} problem(s); nothing written")
    work = Path(args.work)
    dumps = private / "research" / "modding" / "dumps"
    total = 0
    for disc, scripts in sorted(edits.items()):
        dst = work / disc
        dst.mkdir(parents=True, exist_ok=True)
        for script, table in sorted(scripts.items()):
            src = (dumps / disc / f"{script}.gasm").read_text(encoding="utf-8")
            out_lines = []
            for line in src.split("\n"):
                m = STRING_LINE.match(line)
                if m and int(m.group(1)) in table:
                    rest = line[m.end():]
                    line = f'.string s{m.group(1)} "{table[int(m.group(1))]}"{rest}'
                out_lines.append(line)
            text = "\n".join(out_lines)
            ga2asb.assemble(text)          # must still assemble
            (dst / f"{script}.gasm").write_text(text, encoding="utf-8")
            total += len(table)
        print(f"{disc}: {len(scripts)} script(s), {sum(len(t) for t in scripts.values())} line(s) -> {dst}")
    for disc, table in sorted(names.items()):
        n = write_name_tables(private, disc, table, work / disc / "extra")
        print(f"{disc}: {n} character table(s) with English names -> {work / disc / 'extra'}")
    extra_discs = set()
    if args.extra_xlsx:
        import extra_text
        written: dict = {}
        program_patches: dict = {}
        for book in args.extra_xlsx:
            files, extra_errors, used = extra_text.edits(Path(book), private)
            patches, perrors, pused = extra_text.program_patches(Path(book), private)
            extra_errors += perrors
            used += pused
            for disc, items in patches.items():
                program_patches.setdefault(disc, []).extend(items)
            if extra_errors:
                for e in extra_errors[:200]:
                    print(e)
                raise SystemExit(f"{book}: {len(extra_errors)} problem(s); nothing written")
            for disc, paths in sorted(files.items()):
                for path, data in paths.items():
                    if (disc, path) in written:
                        raise SystemExit(f"{disc}:{path} is edited by both {written[disc, path]} and {book}")
                    written[disc, path] = book
                    dst = work / disc / "extra" / path
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_bytes(data)
                extra_discs.add(disc)
                print(f"{disc}: {len(paths)} battle/UI/credits file(s) -> {work / disc / 'extra'}")
            print(f"{used} row(s) imported from {book}")
    if not edits and not names and not extra_discs and not (args.extra_xlsx and program_patches):
        print("no English text in the workbook; nothing written")
        return 0
    if args.build:
        mod = Path(args.build) / args.mod_id
        files = mod / "files"
        for disc in sorted(set(edits) | set(names) | extra_discs):
            iso = sorted(glob.glob(str(private / "discs" / disc / "*.iso")))[0]
            (work / disc).mkdir(parents=True, exist_ok=True)
            argv = ["build", iso, str(work / disc), str(files), "--target", "strm",
                    "--wrap", str(args.wrap)]
            if (work / disc / "extra").is_dir():
                argv += ["--extra", str(work / disc / "extra")]
            if args.font_metrics:
                argv += ["--font-metrics", args.font_metrics]
            print(f"{disc}: ga2scripts.py " + " ".join(argv[:1] + argv[2:]))
            rc = ga2scripts.main(argv)
            if rc:
                return rc
        meta = mod / "mod.json"
        if not meta.exists():
            meta.write_text(json.dumps({
                "name": "Galaxy Angel II English", "version": "0.1", "author": "",
                "description": "English script translation (built from ga2_script.xlsx)",
                "language": "en"}, indent=1) + "\n")
        if args.extra_xlsx and program_patches:
            n = extra_text.write_program_patches(program_patches, private, mod)
            print(f"{n} program string patch(es) -> {mod / 'exe'}")
        # The message a save brings back in the language it was made in (restore_text.py).
        import restore_text
        restore_text.main([str(private), str(mod)])
        print(f"mod written to {mod}")
    print(f"{total} translated line(s) imported; {len(omitted)} marked {OMITTED} kept in Japanese")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("export")
    p.add_argument("private")
    p.add_argument("out")
    p.add_argument("--discs", help="comma-separated subset of " + ",".join(DISCS))
    p = sub.add_parser("check")
    p.add_argument("xlsx")
    p.add_argument("private")
    p = sub.add_parser("import")
    p.add_argument("xlsx")
    p.add_argument("private")
    p.add_argument("work")
    p.add_argument("--build", metavar="MODS_DIR")
    p.add_argument("--mod-id", default="ga2-en")
    p.add_argument("--wrap", type=float, default=372)
    p.add_argument("--font-metrics")
    p.add_argument("--extra-xlsx", action="append", default=[],
                   help="battle/UI or credits workbook from extra_text.py export / export-credits (repeatable)")
    p.add_argument("--no-fit", action="store_true",
                   help="keep the cells' line breaks and size (no reflow or size fitting)")
    args = ap.parse_args(argv)
    return {"export": cmd_export, "check": cmd_check, "import": cmd_import}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
