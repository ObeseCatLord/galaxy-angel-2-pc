#!/usr/bin/env python3
"""Dump every voice clip of the Galaxy Angel II trilogy to WAV, sorted by
speaking character.

Reads ``<private>/discs/<disc>/*.iso`` (zettai, mugen-1, mugen-2, eigou-1,
eigou-2) and writes, by default to ``<private>/research/voices/``:

* ``<English name (Japanese name)>/<game>_d<N>_<bank><id>_<clip name>.wav``
  16-bit mono PCM at the clip's own rate (22050, 32000 or 44100 Hz)
* ``_unknown/<bank>/<code>/...``  clips whose speaker could not be shown
* ``index.csv``  one row per clip occurrence (disc, bank, id); identical
  clips (same ADPCM data and rate) are written once and every occurrence
  points at that file
* ``summary.csv``  clip counts per character and game

Formats (see docs/assets/voices.md): ``voice/<bank>.msh`` is a table of
``{size, id, offset, rate}`` records indexing ``voice/<bank>.msb``, a
string of "MSVp" clips (a VAGp-style 48-byte big-endian header with the
clip's recording name, then PS2 SPU ADPCM).  Banks: ``advvoice`` (story,
the voice argument of the ``say``/``think``/``say_voice`` script calls is
the clip id), ``slgvoice`` (battle), ``misc`` (title / system calls), and
on Zettai ``adv/voice/ranfa22`` in ALL.DAT (raw ADPCM, no names).

The speaker of a clip comes from its recording name, whose first three
letters are a per-character code (``kaz``, ``apr``, ...).  Each code is tied
to a character by the story scripts of the same game: a code is owned by a
character when (almost) all script lines that play clips of that code are
spoken by that character.  Codes no script ties to a character are not
guessed; their clips go to ``_unknown/``.

    python3 tools/assets/dump_voices.py <private> [--out DIR]
            [--discs zettai,mugen-1,...] [--jobs N] [--dry-run]

Needs Python 3.10+ and numpy.
"""

from __future__ import annotations

import argparse
import collections
import csv
import glob
import hashlib
import multiprocessing as mp
import os
import re
import struct
import sys
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modding"))
import ga2arc  # noqa: E402
import script_xlsx as sx  # noqa: E402

DISCS = ["zettai", "mugen-1", "mugen-2", "eigou-1", "eigou-2"]
GAME_OF = sx.GAME_OF
DISC_NO = {"zettai": 1, "mugen-1": 1, "mugen-2": 2, "eigou-1": 1, "eigou-2": 2}
BANK_TAG = {"advvoice": "adv", "slgvoice": "slg", "misc": "misc"}

# script builtins that set the voice of the next msg
B_MSG, B_SAY, B_THINK, B_TEXT_SLOT, B_SAY_VOICE = 0x00, 0x13, 0x14, 0x4B, 0x56

# ownership rule for a recording-name code
OWNER_SHARE = 0.9       # share of the code's script lines spoken by one character
OWNER_MIN_LINES = 1
CROSS_MIN_LINES = 20     # lines needed in another game for a cross-game match

# PS2 SPU ADPCM predictor table (pcsx2 tbl_XA_Factor; ids > 4 decode as 0)
K1 = np.zeros(16, np.int32)
K2 = np.zeros(16, np.int32)
K1[:5] = [0, 60, 115, 98, 122]
K2[:5] = [0, 0, -52, -55, -60]

NAME_RE = re.compile(r"[A-Za-z]{1,3}")


# ---------------------------------------------------------------- banks

@dataclass
class Clip:
    disc: str
    bank: str               # advvoice, slgvoice, misc, adv-ranfa22
    id: int
    archive: str            # disc file holding the bank (STRM_R3.DAT, ALL.DAT, ...)
    abs_off: int            # byte offset of the payload in that disc file (-1: in a RAIC blob)
    msb: str                # archive path of the .msb
    off: int                # offset of the clip in the .msb
    size: int               # clip size in the .msb (header + data)
    rate: int
    name: str               # recording name from the MSVp header ("" if none)
    payload_off: int        # offset of the ADPCM data in the .msb
    payload_size: int
    code: str = ""          # lower-case 3-letter speaker code of the name
    refs: list = field(default_factory=list)   # [(ref id, speaker key, kind, text)]
    battle: collections.Counter = field(default_factory=collections.Counter)  # (slg char, face file char)
    sha1: str = ""
    duration: float = 0.0
    peak_db: float = -999.0
    rms_db: float = -999.0
    character: str = ""     # folder name
    char_id: str = ""
    evidence: str = ""
    file: str = ""


class Disc:
    def __init__(self, private: Path, disc: str):
        isos = sorted(glob.glob(str(private / "discs" / disc / "*.iso")))
        if not isos:
            raise SystemExit(f"no ISO in {private / 'discs' / disc}")
        self.name = disc
        self.iso = isos[0]
        self.src = ga2arc.DiscSource(self.iso)
        self.idx = ga2arc.Pidx.parse(self.src.read("IDX.DAT"))
        self.files = self.idx.files()

    def archive_of(self, path: str) -> str:
        return self.idx.string(self.files[path].a).upper()

    def read(self, path: str, off: int = 0, n: int | None = None) -> bytes:
        """Read a file (RAIC blobs are decoded first)."""
        r = self.files[path]
        arch = self.archive_of(path)
        if r.stored:
            data = ga2arc.raic_decode(self.src.read(arch, r.b, r.stored))
            return data[off:] if n is None else data[off:off + n]
        return self.src.read(arch, r.b + off, (r.size - off) if n is None else n)


def parse_msh(msh: bytes) -> list[tuple[int, int, int, int]]:
    """voice/*.msh: u32 file size, u32 padding, u32 count, then count
    records {u32 size, u32 id, u32 offset, u32 rate} (little endian)."""
    total, _pad, count = struct.unpack_from("<3I", msh, 0)
    if total != len(msh) or 12 + 16 * count > len(msh):
        raise ValueError(f"bad .msh header ({total}, {count}, {len(msh)})")
    return [struct.unpack_from("<4I", msh, 12 + 16 * i) for i in range(count)]


def list_clips(d: Disc) -> list[Clip]:
    clips: list[Clip] = []
    for path in sorted(d.files):
        if not path.endswith(".msh") or "voice/" not in path:
            continue
        msb = path[:-4] + ".msb"
        if msb not in d.files:
            continue
        stem = path.rsplit("/", 1)[1][:-4]
        bank = stem if path.startswith("voice/") else "adv-" + stem
        arch = d.archive_of(msb)
        rec = d.files[msb]
        base = -1 if rec.stored else rec.b
        blob = d.read(msb) if rec.stored else None
        recs = parse_msh(d.read(path))
        unique_ids = len({r[1] for r in recs}) == len(recs)
        for i, (size, cid, off, rate) in enumerate(recs):
            if not unique_ids:          # adv/voice/ranfa22.msh: every id is 0
                cid = i
            if size == 0:
                continue
            head = blob[off:off + 48] if blob is not None else d.read(msb, off, 48)
            if head[:4] == b"MSVp":
                dsz, hrate = struct.unpack_from(">2I", head, 12)
                if dsz + 48 != size or hrate != rate:
                    raise ValueError(f"{d.name} {bank} #{cid}: MSVp header disagrees with .msh")
                name = head[32:48].split(b"\0")[0].decode("ascii", "replace")
                poff, psz = off + 48, dsz
            else:                       # raw ADPCM (adv/voice/ranfa22)
                name, poff, psz = "", off, size
            m = NAME_RE.match(name)
            clips.append(Clip(d.name, bank, cid, arch, -1 if blob is not None else base + poff,
                              msb, off, size, rate, name, poff, psz,
                              code=m.group(0).lower() if m else ""))
    return clips


def battle_tables(d: Disc) -> dict:
    """slg/voicetbl/slgvNNNNNN.tbl (FSTS packs in SLG.DAT): rows
    "tension, flag, char id, face id, filter, probability, voice id" with
    [FACEFILES] face id -> adv/face/facNNN_xxxx.agi.  Returns {slgvoice id:
    Counter((battle char id, face file character number))}.  The char id
    is the unit/pilot the line belongs to, not always the speaker, so this
    is recorded in the index but not used to pick the folder."""
    out: dict = collections.defaultdict(collections.Counter)
    if "SLG.DAT" not in (d.src.iso.files if d.src.iso else {}):
        return out
    head = d.src.read("SLG.DAT", 0, 0x60)
    end = struct.unpack_from("<I", head, 0x20)[0] + struct.unpack_from("<I", head, 0x24)[0]
    px = ga2arc.Pidx.parse(d.src.read("SLG.DAT", 0, end))
    seen = set()
    for _name, off, size, _n in px.groups():
        h = d.src.read("SLG.DAT", off, 20)
        if h[:4] != b"FSTS":
            continue
        count, eo, so, ss = struct.unpack_from("<4I", h, 4)
        hdr = d.src.read("SLG.DAT", off, max(eo + 16 * count, so + ss))
        for path, eoff, esz, est in ga2arc.fsts_entries(hdr):
            if not path.startswith("slg/voicetbl/") or path in seen:
                continue
            seen.add(path)
            blob = d.src.read("SLG.DAT", off + eoff, est or esz)
            text = (ga2arc.raic_decode(blob) if est else blob).decode("cp932", "replace")
            faces = {int(a): int(b) for a, b in
                     re.findall(r"^#(\d+)\s*=\s*fac(\d+)_\S*\.agi", text, re.M)}
            for m in re.finditer(r"^#\d+\s*=\s*(-?\d+),(-?\d+),(-?\d+),(-?\d+),(-?\d+),(-?\d+),(-?\d+)",
                                 text, re.M):
                _t, _f, ch, face, _flt, _p, vid = map(int, m.groups())
                if vid >= 0:
                    out[vid][(ch, faces.get(face))] += 1
    return out


# ---------------------------------------------------------------- ADPCM

def decode_batch(payloads: list[bytes]) -> list[np.ndarray]:
    """Decode PS2 SPU ADPCM clips (16-byte frames: shift/filter byte, flag
    byte, 28 nibbles) to int16, bit-exact with pcsx2's XA_decode_block.
    Vectorised across the clips of the batch."""
    n = len(payloads)
    nfs = [len(p) // 16 for p in payloads]
    nf = max(nfs) if nfs else 0
    buf = np.zeros((n, nf, 16), np.uint8)
    for i, p in enumerate(payloads):
        buf[i, :nfs[i]] = np.frombuffer(p, np.uint8, nfs[i] * 16).reshape(nfs[i], 16)
    hdr = buf[:, :, 0]
    shift = (hdr & 15).astype(np.int32)
    k1 = K1[hdr >> 4].T.copy()          # (nf, n)
    k2 = K2[hdr >> 4].T.copy()
    raw = buf[:, :, 2:]
    nib = np.empty((n, nf, 28), np.int32)
    nib[:, :, 0::2] = raw & 15
    nib[:, :, 1::2] = raw >> 4
    nib = (nib ^ 8) - 8
    base = (nib << 12) >> shift[:, :, None]
    base = np.ascontiguousarray(base.transpose(1, 2, 0))   # (nf, 28, n)
    out = np.empty((nf * 28, n), np.int16)
    h1 = np.zeros(n, np.int32)
    h2 = np.zeros(n, np.int32)
    t = np.empty(n, np.int32)
    for f in range(nf):
        a, b, bf = k1[f], k2[f], base[f]
        for j in range(28):
            np.multiply(h1, a, out=t)
            t += h2 * b
            t += 32
            t >>= 6
            t += bf[j]
            np.clip(t, -32768, 32767, out=t)
            out[f * 28 + j] = t
            h2, h1, t = h1, t, h2
    return [np.ascontiguousarray(out[:nfs[i] * 28, i]) for i in range(n)]


def write_wav(path: Path, pcm: np.ndarray, rate: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.astype("<i2").tobytes())


def level_db(pcm: np.ndarray) -> tuple[float, float]:
    if not len(pcm):
        return -999.0, -999.0
    x = pcm.astype(np.float64)
    peak = float(np.max(np.abs(x)))
    rms = float(np.sqrt(np.mean(x * x)))
    db = lambda v: round(20 * np.log10(v / 32768.0), 1) if v > 0 else -999.0
    return db(peak), db(rms)


# worker: read, hash, claim, decode -------------------------------------------

_W: dict = {}


def _init(work: str, isos: dict) -> None:
    _W["work"] = Path(work)
    _W["isos"] = isos
    _W["src"] = {}


def _src(disc: str):
    if disc not in _W["src"]:
        _W["src"][disc] = Disc.__new__(Disc)
        d = _W["src"][disc]
        d.name, d.iso = disc, _W["isos"][disc]
        d.src = ga2arc.DiscSource(d.iso)
        d.idx = ga2arc.Pidx.parse(d.src.read("IDX.DAT"))
        d.files = d.idx.files()
        d.blobs = {}
    return _W["src"][disc]


def _task(job: tuple) -> list[tuple]:
    """job = (disc, msb path, [(key, payload_off, payload_size, rate)]) with
    the clips in offset order.  Returns [(key, sha1, nsamples, peak, rms)]."""
    disc, msb, items = job
    d = _src(disc)
    lo = items[0][1]
    hi = max(o + s for _, o, s, _ in items)
    if d.files[msb].stored:
        if msb not in d.blobs:
            d.blobs[msb] = d.read(msb)
        region, lo = d.blobs[msb], 0
    else:
        region = d.read(msb, lo, hi - lo)
    work = _W["work"]
    todo, res = [], {}
    for key, o, s, rate in items:
        p = region[o - lo:o - lo + s]
        h = hashlib.sha1(rate.to_bytes(4, "little") + p).hexdigest()
        res[key] = h
        try:
            fd = os.open(work / (h + ".claim"), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            todo.append((key, h, p, rate))
        except FileExistsError:
            pass
    stats = {}
    # decode in batches of similar length (padding costs time)
    todo.sort(key=lambda x: len(x[2]))
    i = 0
    while i < len(todo):
        j = i
        cap = 48 << 20
        while j < len(todo) and (j - i + 1) * len(todo[j][2]) * 2 <= cap and j - i < 4096:
            j += 1
        j = max(j, i + 1)
        batch = todo[i:j]
        for (key, h, p, rate), pcm in zip(batch, decode_batch([b[2] for b in batch])):
            write_wav(work / (h + ".wav.part"), pcm, rate)
            os.replace(work / (h + ".wav.part"), work / (h + ".wav"))
            pk, rm = level_db(pcm)
            stats[h] = (len(pcm), pk, rm)
        i = j
    return [(key, res[key]) + stats.get(res[key], (None, None, None)) for key, _, _, _ in items]


# ---------------------------------------------------------------- scripts

def speaker_key(raw: int | None) -> tuple | None:
    """say/think char argument -> (char id, NAME index).  Bits 16-19 pick
    the NAMEnn of charNNN.tbl (Kahlua/Tequila), bit 0x20 << 16 is the
    narration balloon, 0x10 << 16 a display variant."""
    if raw is None or raw < 0:
        return None
    return (raw & 0xFFFF, (raw >> 16) & 0x0F)


def script_refs(dd) -> dict:
    """{voice id: [(ref, speaker key, kind, text)]} for every msg played with
    a voice (say/think/say_voice argument, or text_slot's voice)."""
    out = collections.defaultdict(list)
    for name, sc in dd.scripts:
        sid = {o: i for i, (o, _) in enumerate(sc.strings)}
        for f in sc.funcs:
            stack: list = []
            pend = None          # (kind, speaker raw, voice)
            for pc, op, a in f.ins:
                if op == "PUSHI":
                    stack.append((pc, struct.unpack("<i", a)[0]))
                    continue
                if op != "CALLB":
                    if op in ("PUSHL", "PUSHG", "PUSHF", "PUSHR"):
                        stack.append(None)
                    elif op in ("POP", "POPL", "POPG", "POPF", "BZ", "BNZ", "BTBL", "RET"):
                        if stack:
                            stack.pop()
                    elif op in ("NEG", "NOT"):
                        if stack:
                            stack.pop()
                        stack.append(None)
                    elif op in ("ADD", "SUB", "MUL", "DIV", "MOD", "AND", "OR", "EQ", "NEQ",
                                "LT", "GT", "LEQ", "GEQ"):
                        del stack[-2:]
                        stack.append(None)
                    elif op == "EXCH" and len(stack) >= 2:
                        stack[-1], stack[-2] = stack[-2], stack[-1]
                    elif op in ("CALLL", "CALLG"):
                        if a[-1]:
                            del stack[-a[-1]:]
                    continue
                bid, n = struct.unpack_from("<I", a)[0], a[4]
                args = stack[-n:] if n else []
                if n:
                    del stack[-n:]
                args = [None] * (n - len(args)) + args
                vals = [x[1] if x else None for x in args]
                stack = []
                if bid in (B_SAY, B_THINK, B_SAY_VOICE):
                    o = 1 if bid == B_SAY_VOICE else 0
                    c = vals[o] if len(vals) > o else None
                    v = vals[o + 2] if len(vals) > o + 2 else None
                    pend = ("think" if bid == B_THINK else "say", c, v)
                elif bid == B_TEXT_SLOT and len(vals) > 5 and vals[5] is not None and vals[5] >= 0:
                    pend = ("caption", pend[1] if pend else None, vals[5])
                elif bid == B_MSG:
                    if pend and pend[2] is not None and pend[2] >= 0:
                        text, ref = "", f"{dd.disc}:{name}"
                        if args and args[0] is not None and args[0][1] in sid:
                            i = sid[args[0][1]]
                            text = sx.readable(sc.strings[i][1])
                            ref = f"{dd.disc}:{name}:s{i}"
                        out[pend[2]].append((ref, speaker_key(pend[1]), pend[0], text))
                    pend = None
    return out


# ---------------------------------------------------------------- names

def safe(s: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", s).strip(" .")
    return s or "_"


class Names:
    """Speaker key -> folder label "English (Japanese)" from the translation
    workbook's Names sheet, falling back to the disc's char tables."""

    def __init__(self, private: Path):
        self.en: dict = {}
        wb = private / "research" / "translation" / "ga2_script.xlsx"
        if wb.exists():
            rows = sx.read_xlsx(wb).get("Names", [])
            if rows:
                h = {k: i for i, k in enumerate(rows[0])}
                for r in rows[1:]:
                    r = r + [""] * (len(h) - len(r))
                    try:
                        key = (r[h["Disc"]], int(r[h["Char id"]]), int(r[h["Key"]][4:]))
                    except (ValueError, KeyError):
                        continue
                    self.en[key] = (r[h["English"]].strip(), r[h["Romanised (asset dump)"]].strip(),
                                    r[h["Japanese"]].strip())

    def label(self, dd, key: tuple) -> str:
        c, k = key
        en, rom, jp = self.en.get((dd.disc, c, k), ("", "", ""))
        ch = dd.chars.get(c)
        if not jp and ch:
            jp = ch.names.get(k) or ch.names.get(0, "")
        if not en:
            en = rom if k == 0 and rom else (dd.folder_of.get(c, "") if k == 0 else "")
        if not en and k:
            en0 = self.en.get((dd.disc, c, 0), ("", "", ""))[0] or dd.folder_of.get(c, "")
            en = f"{en0} NAME{k:02d}" if en0 else ""
        if not en and not jp:
            return f"char {c}" + (f" NAME{k:02d}" if k else "")
        if not en:
            return safe(jp)
        return safe(f"{en} ({jp})" if jp else en)


# ---------------------------------------------------------------- main

def battle_speaker(c: Clip) -> int | None:
    """The one character a battle clip's voice-table rows agree on (rows
    name it and show its own portraits), else None."""
    if not c.battle:
        return None
    chars = {ch for ch, _f in c.battle}
    if len(chars) != 1:
        return None
    ch = chars.pop()
    if not 0 <= ch < 100 or any(f != ch for _c, f in c.battle):
        return None
    return ch


def assign_speakers(clips: list[Clip], labels: dict, log) -> dict:
    """Tie each recording-name code to a character, per game, from the
    script lines that play clips of that code.  Fills Clip.character,
    char_id and evidence.

    1. The code is owned by a character in this game (OWNER_SHARE of its
       voiced script lines): every clip of the code goes to that character.
    2. A battle clip whose battle voice-table rows all name one character
       and show only that character's own portraits (facNNN_ = char NNN)
       goes to that character.  On clips that also have an owned code this
       agrees with the code for 99.9% of clips (the exceptions are lines
       of another character filed under a heroine's unit).
    3. No script of this game plays the code (battle-only codes, Eigou disc
       2), but every other loaded game that plays it ties it to the same
       character with at least CROSS_MIN_LINES lines, and no battle table
       contradicts it: that character, marked as a cross-game match.
    4. Otherwise a clip that the scripts play only for one speaker goes to
       that speaker (codes shared by several roles, group lines).
    5. Everything else is left unassigned (_unknown/)."""
    by_game = collections.defaultdict(list)
    for c in clips:
        by_game[GAME_OF[c.disc]].append(c)
    votes = {}
    owners: dict = {}
    for game, cl in by_game.items():
        v = collections.defaultdict(collections.Counter)
        ids: dict = {}
        for c in cl:
            for _ref, key, _k, _t in c.refs:
                if key is None:
                    continue
                lab = labels[(c.disc, key)]
                v[c.code][lab] += 1
                ids.setdefault((c.code, lab), set()).add(f"{key[0]}" + (f":{key[1]}" if key[1] else ""))
        own = {}
        for code, cnt in v.items():
            lab, n = cnt.most_common(1)[0]
            tot = sum(cnt.values())
            if code and n >= OWNER_MIN_LINES and n / tot >= OWNER_SHARE:
                own[code] = (lab, "/".join(sorted(ids[(code, lab)])), f"{n}/{tot}", n)
            else:
                log(f"  {game}: code {code!r} is shared: {dict(cnt.most_common(4))}")
        votes[game], owners[game] = v, own

    def users(cnt) -> str:
        return ", ".join(f"{lab} {n}" for lab, n in cnt.most_common(4))

    for game, cl in by_game.items():
        own, v = owners[game], votes[game]
        cross = {}
        for code in {c.code for c in cl} - set(v):
            other = [g for g in votes if g != game and code in votes[g]]
            labs = {owners[g][code][0] if code in owners[g] and owners[g][code][3] >= CROSS_MIN_LINES
                    else None for g in other}
            if other and None not in labs and len(labs) == 1:
                cross[code] = (labs.pop(), sorted(other))
        for c in cl:
            if c.code in own:
                c.character, c.char_id, share, _n = own[c.code]
                c.evidence = f"name code {c.code}: {share} voiced script lines of {game} spoken by this character"
                continue
            bt = battle_speaker(c)
            if bt is not None:
                c.character = labels[(c.disc, (bt, 0))]
                c.char_id = str(bt)
                n = sum(c.battle.values())
                c.evidence = (f"battle voice table: all {n} rows name character {bt} with its own "
                              f"portraits (fac{bt:03d}_); name code {c.code or '(none)'} not tied by scripts")
                continue
            if c.code in cross and not (c.battle and bt is None and any(
                    labels.get((c.disc, (ch, 0))) != cross[c.code][0] for ch, _f in c.battle)):
                lab, gs = cross[c.code]
                c.character = lab
                c.char_id = ""
                c.evidence = (f"name code {c.code}: not played by {game} scripts; owned by this "
                              f"character in {', '.join(gs)} (cross-game match)")
                continue
            keys = {k for _r, k, _kd, _t in c.refs if k is not None}
            spk = {labels[(c.disc, k)] for k in keys}
            shared = f"name code {c.code} is used by {users(v[c.code])}" if c.code in v else \
                f"name code {c.code or '(none)'} is not played by any script"
            if len(spk) == 1:
                c.character = spk.pop()
                c.char_id = "/".join(sorted(f"{k[0]}" + (f":{k[1]}" if k[1] else "") for k in keys))
                c.evidence = f"{shared}; this clip is spoken only by this character"
            else:
                c.character = ""
                c.evidence = shared + ("; this clip is not played by any script" if not spk
                                       else "; this clip is played for several speakers")
    return owners


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("private", type=Path, help="private root holding discs/<disc>/*.iso")
    ap.add_argument("--out", type=Path, help="output directory (default <private>/research/voices)")
    ap.add_argument("--discs", default=",".join(DISCS), help="comma-separated disc list")
    ap.add_argument("--jobs", type=int, default=max(1, min(8, (os.cpu_count() or 2) - 2)))
    ap.add_argument("--dry-run", action="store_true", help="map clips and print counts; write nothing")
    args = ap.parse_args(argv)
    discs = [d.strip() for d in args.discs.split(",") if d.strip()]
    for d in discs:
        if d not in DISCS:
            raise SystemExit(f"unknown disc {d!r} (choose from {', '.join(DISCS)})")
    out = (args.out or args.private / "research" / "voices").resolve()
    log = lambda s: print(s, flush=True)

    names = Names(args.private)
    clips: list[Clip] = []
    labels: dict = {}
    isos = {}
    for disc in discs:
        d = Disc(args.private, disc)
        isos[disc] = d.iso
        cl = list_clips(d)
        dd = sx.load_disc(args.private, disc)
        refs = script_refs(dd)
        adv = {c.id: c for c in cl if c.bank == "advvoice"}
        slg = {c.id: c for c in cl if c.bank == "slgvoice"}
        for vid, cnt in battle_tables(d).items():
            if vid in slg:
                slg[vid].battle.update(cnt)
        missing = 0
        for vid, rs in refs.items():
            if vid in adv:
                adv[vid].refs.extend(rs)
            else:
                missing += len(rs)
        for rs in refs.values():
            for _r, key, _k, _t in rs:
                if key is not None and (disc, key) not in labels:
                    labels[(disc, key)] = names.label(dd, key)
        for c in slg.values():
            for ch, _f in c.battle:
                if 0 <= ch < 100 and (disc, (ch, 0)) not in labels:
                    labels[(disc, (ch, 0))] = names.label(dd, (ch, 0))
        banks = collections.Counter(c.bank for c in cl)
        log(f"{disc}: {len(cl)} clips {dict(banks)}; {sum(map(len, refs.values()))} voiced "
            f"script lines, {len(refs)} distinct voice ids" + (f", {missing} lines with an id past the bank" if missing else ""))
        clips.extend(cl)

    assign_speakers(clips, labels, log)

    if args.dry_run:
        cnt = collections.Counter((GAME_OF[c.disc], c.character or "_unknown") for c in clips)
        for (g, ch), n in sorted(cnt.items()):
            log(f"  {g:7s} {n:6d} {ch}")
        return 0

    work = out / ".work"
    work.mkdir(parents=True, exist_ok=True)
    for p in work.iterdir():
        p.unlink()

    # jobs: contiguous runs of clips of one bank
    key_of = {(c.disc, c.bank, c.id): c for c in clips}
    jobs = []
    groups = collections.defaultdict(list)
    for c in clips:
        groups[(c.disc, c.msb)].append(c)
    for (disc, msb), cl in groups.items():
        cl.sort(key=lambda c: c.payload_off)
        run, size = [], 0
        for c in cl:
            run.append(((c.disc, c.bank, c.id), c.payload_off, c.payload_size, c.rate))
            size += c.payload_size
            if size >= (24 << 20) or len(run) >= 3000:
                jobs.append((disc, msb, run))
                run, size = [], 0
        if run:
            jobs.append((disc, msb, run))
    jobs.sort(key=lambda j: -sum(x[2] for x in j[2]))
    log(f"decoding {len(clips)} clips in {len(jobs)} jobs on {args.jobs} processes")
    stats: dict = {}
    done = 0
    with mp.Pool(args.jobs, initializer=_init, initargs=(str(work), isos)) as pool:
        for res in pool.imap_unordered(_task, jobs):
            for key, h, ns, pk, rm in res:
                c = key_of[key]
                c.sha1 = h
                if ns is not None:
                    stats[h] = (ns, pk, rm)
            done += 1
            if done % 25 == 0 or done == len(jobs):
                log(f"  {done}/{len(jobs)} jobs")

    # final names: the first occurrence (disc order, bank, id) names the file
    order = {d: i for i, d in enumerate(DISCS)}
    bank_order = {"advvoice": 0, "misc": 1, "slgvoice": 2}
    clips.sort(key=lambda c: (order[c.disc], bank_order.get(c.bank, 3), c.id))
    old = set()
    if (out / "index.csv").exists():
        with open(out / "index.csv", newline="", encoding="utf-8") as f:
            old = {r["file"] for r in csv.DictReader(f)}
    # identical audio: an occurrence left unknown takes the speaker that the
    # other occurrences of the same recording agree on (e.g. Mugen battle
    # clips copied onto Eigou disc 2, where their code is shared)
    same = collections.defaultdict(list)
    for c in clips:
        same[c.sha1].append(c)
    for group in same.values():
        known = {c.character for c in group if c.character}
        if len(known) != 1:
            continue
        lab = known.pop()
        src_c = next(c for c in group if c.character)
        for c in group:
            if not c.character:
                c.character, c.char_id = lab, src_c.char_id
                c.evidence = (f"identical audio to {src_c.disc}:{src_c.bank}:{src_c.id} "
                              f"({src_c.evidence}); own data: {c.evidence}")
    first: dict = {}
    for c in clips:
        ns, c.peak_db, c.rms_db = stats[c.sha1]
        c.duration = round(ns / c.rate, 3)
        if c.sha1 in first:
            c.file = first[c.sha1].file
            continue
        game = GAME_OF[c.disc]
        tag = BANK_TAG.get(c.bank, c.bank)
        stem = f"{game}_d{DISC_NO[c.disc]}_{tag}{c.id:05d}" + (f"_{safe(c.name.split('.')[0])}" if c.name else "")
        folder = c.character or f"_unknown/{tag}/{c.code or 'unnamed'}"
        if folder.startswith("_unknown/") and c.code in ("all",):
            folder = f"_unknown/{tag}/all (group lines)"
        c.file = f"{folder}/{stem}.wav"
        dst = out / c.file
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(work / (c.sha1 + ".wav"), dst)
        first[c.sha1] = c
    for p in work.iterdir():
        p.unlink()
    work.rmdir()
    new = {c.file for c in clips}
    emptied = set()
    for f in old - new:
        p = out / f
        if p.suffix == ".wav" and p.is_file():
            p.unlink()
            emptied.add(p.parent)
    for q in sorted(emptied, key=lambda q: -len(q.parts)):
        while q != out and q.is_relative_to(out):
            try:
                q.rmdir()           # only succeeds when empty
            except OSError:
                break
            q = q.parent

    cols = ["file", "character", "character_id", "game", "disc", "voice_id", "source",
            "duration_s", "script_refs", "japanese_line", "bank", "clip_name", "code", "rate",
            "speaker_evidence", "script_speakers", "battle_table_chars", "peak_dbfs", "rms_dbfs",
            "sha1", "duplicate_of"]
    with open(out / "index.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for c in clips:
            spk = sorted({labels[(c.disc, k)] for _r, k, _kd, _t in c.refs if k is not None})
            texts = []
            for _r, _k, _kd, t in c.refs:
                if t and t not in texts:
                    texts.append(t)
            src = f"{c.msb}@{c.off:#x}+{c.size:#x}"
            if c.abs_off >= 0:
                src += f" ({c.archive}@{c.abs_off:#x})"
            fo = first[c.sha1]
            dup = "" if fo is c else f"{fo.disc}:{fo.bank}:{fo.id}"
            w.writerow([c.file, c.character or "_unknown", c.char_id, GAME_OF[c.disc], c.disc,
                        c.id, src, c.duration, " ".join(r for r, _k, _kd, _t in c.refs),
                        " | ".join(texts), c.bank, c.name, c.code, c.rate, c.evidence,
                        "; ".join(spk) if spk and spk != [c.character] else "",
                        "; ".join(f"char {ch} face fac{fc if fc is not None else '?'} x{n}"
                                  for (ch, fc), n in c.battle.most_common()),
                        c.peak_db, c.rms_db, c.sha1, dup])

    summ = collections.Counter()
    uniq = collections.Counter()
    seen = set()
    for c in clips:
        g = GAME_OF[c.disc]
        summ[(c.character or "_unknown", g)] += 1
        if (c.sha1, g) not in seen:
            seen.add((c.sha1, g))
            uniq[(c.character or "_unknown", g)] += 1
    games = [g for g in ("zettai", "mugen", "eigou") if any(GAME_OF[d] == g for d in discs)]
    with open(out / "summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["character"] + [f"{g} clips" for g in games] + [f"{g} unique" for g in games]
                   + ["files written"])
        files = collections.Counter(first[h].character or "_unknown" for h in first)
        for ch in sorted({k[0] for k in summ}):
            w.writerow([ch] + [summ[(ch, g)] for g in games] + [uniq[(ch, g)] for g in games]
                       + [files[ch]])
    total = sum((out / c.file).stat().st_size for c in first.values())
    unk = sum(1 for c in first.values() if not c.character)
    log(f"{len(clips)} clip occurrences, {len(first)} unique files ({total / 1e9:.2f} GB), "
        f"{unk} unique files in _unknown/; index: {out / 'index.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
