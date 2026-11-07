#!/usr/bin/env python3
"""Collect the Galaxy Angel II pictures that carry Japanese text (buttons,
menus, system messages, chapter and stage titles, place names, captioned
CGs) into one folder for translators, with their texture-pack names.

    python3 tools/assets/text_images.py <private> [--curation CSV] [--out DIR]
            [--jobs N] [--no-contact]

Input: the disc images ``<private>/discs/<disc>/*.iso`` and a curation list
(default ``<private>/research/translation/images/curation.csv``) with the
columns ``sha256, archive_path, category, japanese, note``.  ``sha256`` is the
pixel hash used by ``dump_images.py`` (its ``index.csv``), so a row selects
exactly one picture whatever its file name or disc.  The list was made by
looking at every 2D picture of the five discs (see
``docs/modding/translation.md``); it is kept in the private root because it
quotes game text.

Output (default ``<private>/research/translation/images/``):

* ``<game>/<category>/<name>.png`` and ``shared/<category>/`` for pictures
  that occur in more than one game; each distinct picture is written once.
* ``index.csv`` (and ``ga2_images.xlsx``): file, games, discs, category,
  archive paths, size, Japanese, English (empty), texture-pack name(s),
  match status, notes.
* ``contact/<folder>.png`` contact sheets and ``README.md``.

Texture-pack names: the port names a texture ``<hash>_<w>x<h>.png`` after an
FNV-style hash of the sampled GS region in PS2 RGBA (alpha 0x80 = opaque),
see ``gs_texpacks.cpp`` ``hashPixels``.  The tool computes that hash for the
whole picture (``predicted``) and searches the runtime dumps
``<private>/research/buttons/dump-*`` for the picture or a region of it
(``dumped``).  Only Python 3, Pillow and numpy are needed.
"""

from __future__ import annotations

import argparse
import csv
import multiprocessing as mp
import os
import re
import shutil
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dump_images as di  # noqa: E402
import extract_skybox as sky  # noqa: E402

from PIL import Image, ImageDraw  # noqa: E402

DISCS = [('zettai', 'zettai'), ('mugen', 'mugen-1'), ('mugen', 'mugen-2'),
         ('eigou', 'eigou-1'), ('eigou', 'eigou-2')]
GAME_ORDER = ['zettai', 'mugen', 'eigou']
M64 = (1 << 64) - 1


# ---------------------------------------------------------------------------
# The port's view of a texture: PS2 RGBA words and hashPixels
# ---------------------------------------------------------------------------

def ps2_words(d: bytes) -> tuple[int, int, np.ndarray]:
    """AGI -> (w, h, uint32 array h x w) as gs_texpacks decode() sees it:
    R | G<<8 | B<<16 | A<<24 with the raw PS2 alpha (0x80 = opaque)."""
    pix = struct.unpack_from('<I', d, 0x08)[0]
    psm = struct.unpack_from('<H', d, 0x0E)[0]
    w, h = struct.unpack_from('<HH', d, 0x18)
    if psm in (0x13, 0x14):
        clut = struct.unpack_from('<I', d, 0x1C)[0]
        n = 256 if psm == 0x13 else 16
        raw = np.frombuffer(d, '<u4', n, clut)
        if n == 256:
            i = np.arange(256)
            raw = raw[(i & 0xE7) | ((i & 0x08) << 1) | ((i & 0x10) >> 1)]
        if psm == 0x13:
            idx = np.frombuffer(d, np.uint8, w * h, pix)
        else:
            b = np.frombuffer(d, np.uint8, (w * h + 1) // 2, pix)
            idx = np.empty(b.size * 2, np.uint8)
            idx[0::2], idx[1::2] = b & 15, b >> 4
            idx = idx[:w * h]
        return w, h, raw[idx].reshape(h, w).astype(np.uint32)
    if psm == 0x00:
        return w, h, np.frombuffer(d, '<u4', w * h, pix).reshape(h, w).astype(np.uint32)
    if psm == 0x02:
        v = np.frombuffer(d, '<u2', w * h, pix).astype(np.uint32)
        c = ((v & 31) << 3) | (((v >> 5) & 31) << 11) | (((v >> 10) & 31) << 19)
        c |= np.where(v & 0x8000, 0x80, 0).astype(np.uint32) << 24
        return w, h, c.reshape(h, w)
    raise ValueError(f'unsupported PSM {psm:#x}')


def hash_pixels(px: np.ndarray) -> int:
    """gs_texpacks.cpp hashPixels over a h x w array of PS2 RGBA words."""
    h, w = px.shape
    x = 0x9E3779B97F4A7C15 ^ ((w << 32) | h)
    for v in px.ravel().tolist():
        x ^= v
        x = (x * 0x100000001B3) & M64
        x ^= x >> 29
    return x


def pack_name(px: np.ndarray) -> str:
    h, w = px.shape
    return f'{hash_pixels(px):016x}_{w}x{h}.png'


def png_words(path: Path) -> np.ndarray:
    """A runtime dump PNG back to PS2 RGBA words (its alpha is the GS alpha
    doubled and clamped, so 255 -> 0x80)."""
    a = np.asarray(Image.open(path).convert('RGBA'), dtype=np.uint32)
    return a[..., 0] | (a[..., 1] << 8) | (a[..., 2] << 16) | (((a[..., 3] + 1) >> 1) << 24)


# ---------------------------------------------------------------------------
# Disc scan (worker)
# ---------------------------------------------------------------------------

_WANT: set = set()
_DISCS: dict = {}


def _init(want):
    global _WANT
    _WANT = set(want)


def scan_task(task):
    iso, dat, off, usize, csize, path = task
    try:
        disc = _DISCS.get(iso) or _DISCS.setdefault(iso, sky.Disc(Path(iso)))
        data = di.unpack(disc.read(dat, off, csize if csize and csize != usize else usize), usize, csize)
        im, fmt = di.decode_agi(data)
        sha = di.pixel_hash(im)
        if sha not in _WANT:
            return path, sha, None
        w, h, px = ps2_words(data)
        buf = bytearray()
        im.save(_Buf(buf), 'PNG')
        return path, sha, {'w': w, 'h': h, 'fmt': fmt, 'px': px.tobytes(), 'png': bytes(buf),
                           'pack': pack_name(px)}
    except Exception as e:  # noqa: BLE001  (reported, never fatal)
        return path, None, f'{type(e).__name__}: {e}'


class _Buf:
    def __init__(self, b):
        self.b = b

    def write(self, data):
        self.b += data
        return len(data)


def scan_discs(priv: Path, want: set, jobs: int, log):
    """-> {sha: info} with info['occ'] = [(game, disc, path, archives)]."""
    found: dict = {}
    for game, disc_id in DISCS:
        isos = sorted((priv / 'discs' / disc_id).glob('*.iso'))
        if not isos:
            log(f'{disc_id}: no ISO, skipped')
            continue
        iso = str(isos[0])
        disc = sky.Disc(Path(iso))
        files: dict = {}
        for dat, off, us, cs, path, where in di.disc_sources(disc):
            if not path.lower().endswith('.agi') or path.startswith('adv/face/') or us == 0:
                continue
            f = files.setdefault(path, {})
            f.setdefault((us, cs), [dat, off, us, cs, []])[4].append(where)
        tasks, meta = [], []
        for path in sorted(files):
            for v in files[path].values():
                tasks.append((iso, v[0], v[1], v[2], v[3], path))
                meta.append(' '.join(sorted(set(v[4]))))
        log(f'{disc_id}: {len(tasks)} pictures')
        with mp.Pool(jobs, initializer=_init, initargs=(sorted(want),)) as pool:
            for k, (path, sha, res) in enumerate(pool.imap(scan_task, tasks, chunksize=16)):
                if sha is None:
                    log(f'  {path}: {res}')
                    continue
                if sha not in want:
                    continue
                info = found.setdefault(sha, res)
                info.setdefault('occ', []).append((game, disc_id, path, meta[k]))
    return found


# ---------------------------------------------------------------------------
# Runtime dump matching
# ---------------------------------------------------------------------------

def load_dumps(priv: Path):
    out = []
    for d in sorted((priv / 'research' / 'buttons').glob('dump-*')):
        for p in sorted(d.glob('*.png')):
            m = re.fullmatch(r'([0-9a-f]{16})_(\d+)x(\d+)', p.stem)
            if not m:
                continue
            out.append((d.name, p.name, png_words(p)))
    return out


def find_in(big: np.ndarray, small: np.ndarray, counts: dict):
    """Positions (x, y) where small occurs inside big (exact pixels)."""
    H, W = big.shape
    h, w = small.shape
    if h > H or w > W:
        return []
    # Anchor on the sampled pixel of small that is rarest in big.
    ys = np.linspace(0, h - 1, min(h, 5)).astype(int)
    xs = np.linspace(0, w - 1, min(w, 5)).astype(int)
    best = None
    for y in ys:
        for x in xs:
            c = counts.get(int(small[y, x]), 0)
            if c == 0:
                return []
            if best is None or c < best[0]:
                best = (c, y, x)
    _, ay, ax = best
    hits = []
    for y, x in np.argwhere(big == small[ay, ax]):
        oy, ox = y - ay, x - ax
        if 0 <= oy <= H - h and 0 <= ox <= W - w and np.array_equal(big[oy:oy + h, ox:ox + w], small):
            hits.append((int(ox), int(oy)))
            if len(hits) > 4:
                break
    return hits


def match_dumps(pics: dict, dumps: list, log):
    """pics: {sha: info with 'arr'}.  Adds info['dumped'] = [(dumpdir, name,
    relation)] and returns the number of distinct dumps that matched."""
    used = set()
    for sha, info in pics.items():
        arr = info['arr']
        vals, cnt = np.unique(arr, return_counts=True)
        counts = dict(zip(vals.tolist(), cnt.tolist()))
        info['dumped'] = []
        for dname, fname, d in dumps:
            if d.size < 64 or (d == d.flat[0]).all():
                continue  # tiny or one-colour strips match anything
            if d.shape == arr.shape:
                if np.array_equal(d, arr):
                    info['dumped'].append((dname, fname, 'whole'))
                    used.add(fname)
                continue
            for x, y in find_in(arr, d, counts)[:1]:
                h, w = d.shape
                info['dumped'].append((dname, fname, f'region {x},{y} {w}x{h}'))
                used.add(fname)
    log(f'runtime dumps: {len(dumps)} files, {len(used)} match a listed picture')
    return len(used)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def sheet(items, path: Path, cols=6, cell=(240, 120)):
    if not items:
        return
    cw, ch = cell
    rows = (len(items) + cols - 1) // cols
    out = Image.new('RGB', (cols * cw, rows * (ch + 14)), (40, 40, 48))
    dr = ImageDraw.Draw(out)
    chk = Image.new('RGBA', (cw, ch), (110, 110, 120, 255))
    cd = ImageDraw.Draw(chk)
    for yy in range(0, ch, 8):
        for xx in range(0, cw, 8):
            if (xx // 8 + yy // 8) % 2:
                cd.rectangle([xx, yy, xx + 7, yy + 7], fill=(140, 140, 150, 255))
    for k, (png, cap) in enumerate(items):
        im = Image.open(png).convert('RGBA')
        sc = min(cw / im.width, ch / im.height, 2.0)
        im = im.resize((max(1, int(im.width * sc)), max(1, int(im.height * sc))),
                       Image.NEAREST if sc >= 1 else Image.LANCZOS)
        bg = chk.copy()
        bg.alpha_composite(im, ((cw - im.width) // 2, (ch - im.height) // 2))
        x, y = (k % cols) * cw, (k // cols) * (ch + 14)
        out.paste(bg.convert('RGB'), (x, y))
        dr.text((x + 2, y + ch + 1), cap[:cw // 6], fill=(255, 255, 160))
    out.save(path, optimize=True)


README = """# Galaxy Angel II: pictures with Japanese text

Every picture of the three games (all five discs) that shows Japanese text,
ready for translation.  Made by `tools/assets/text_images.py`; rerunning it
rewrites this folder (keep your edited files elsewhere).

* `<game>/<category>/` pictures used by one game, `shared/<category>/`
  pictures used by more than one game (stored once; `index.csv` lists every
  disc and archive path).
* `index.csv` / `ga2_images.xlsx`: one row per picture: the Japanese text
  (transcribed by eye; `<...>` marks handwriting not transcribed), an empty
  English column, the texture-pack file name(s) and notes.  `partial` in the
  notes means only part of the picture is text (logos, character tiles,
  stage previews).
* `contact/` contact sheets per folder for browsing.

Categories: buttons (single labelled buttons), menus (title, options,
save/load, extras), system (memory card, disc and confirmation messages),
titles (game logos and subtitles), chapter (chapter numbers and titles),
location (place-name plates shown at scene changes), battle (stage titles,
briefing and battle UI), tutorial, cg (event pictures with text, mostly
Eigou storyboard placeholders) and other.

## Editing

* Keep the picture's shape (aspect ratio) and its transparent areas: the game
  draws the whole rectangle, so anything left transparent stays see-through.
* You may draw at any whole multiple of the size (2x, 4x, 8x): the port
  scales the replacement into the same place, and larger art looks sharper at
  higher internal resolutions.  Keep text inside the area the original text
  used; menus place the pictures exactly.
* Save as PNG with alpha (RGBA).  Alpha 255 is opaque.
* Some titles are split into one picture per character (`tut`, `omk` glyph
  sets): translate the set together so the letters line up, or put the whole
  word into the first glyph and leave the rest empty.
* Button icons (circle, cross...) inside pictures are drawn over by the
  port's button-style option where `buttons/map.json` lists them; keep them
  in place.

## Installing into a language pack

A language pack is a mod folder with `"language": "en"` in its `mod.json`
(see docs/MODDING.md).  Put each translated picture in its `textures/`
folder (any sub-folder) under the texture-pack name from `index.csv`:

```
mods/ga2-en/
  mod.json            {"name": "English", "language": "en", ...}
  textures/menus/0123456789abcdef_176x24.png
```

The name is `<hash>_<width>x<height>.png` of the ORIGINAL picture (the hash
identifies the original pixels); your file may be larger.  `match` in
`index.csv` says how sure the name is:

* `dumped`: the game was seen drawing exactly this picture (or the region in
  `texture_pack`) under that name.  Use it as is.
* `predicted`: computed from the picture; correct when the game draws the
  whole picture in one piece, which is the usual case for UI pictures.  If a
  replacement does not show, turn on Options > Dump textures, show the
  picture in the game, and take the name from `<data>/dumps/textures/`.
  When the game draws only part of a picture (a region), its dump is a crop:
  translate that crop and name it after the dump.
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('private', type=Path)
    ap.add_argument('--curation', type=Path)
    ap.add_argument('--out', type=Path)
    ap.add_argument('--jobs', type=int, default=max(1, min(12, (os.cpu_count() or 2) - 2)))
    ap.add_argument('--no-contact', action='store_true')
    args = ap.parse_args(argv)
    priv = args.private
    out = args.out or priv / 'research' / 'translation' / 'images'
    cur_path = args.curation or out / 'curation.csv'

    def log(m):
        print(m, flush=True)

    with open(cur_path, encoding='utf-8') as fh:
        cur = {r['sha256']: r for r in csv.DictReader(fh)}
    log(f'{len(cur)} curated pictures')
    pics = scan_discs(priv, set(cur), args.jobs, log)
    missing = sorted(set(cur) - set(pics))
    for s in missing:
        log(f'  not found on any disc: {cur[s]["archive_path"]} {s[:12]}')

    for sha, info in pics.items():
        info['arr'] = np.frombuffer(info['px'], np.uint32).reshape(info['h'], info['w'])
    match_dumps(pics, load_dumps(priv), log)

    # Clean the generated parts of the output folder (keep curation.csv).
    for p in list(out.iterdir()) if out.exists() else []:
        if p.name in ('curation.csv',) or p == cur_path:
            continue
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
    out.mkdir(parents=True, exist_ok=True)

    rows, taken, folders = [], set(), {}
    order = sorted(pics, key=lambda s: (cur[s]['category'], cur[s]['archive_path'], s))
    for sha in order:
        info, c = pics[sha], cur[sha]
        games = sorted({o[0] for o in info['occ']}, key=GAME_ORDER.index)
        discs = sorted({o[1] for o in info['occ']}, key=[d for _, d in DISCS].index)
        folder = f'{"shared" if len(games) > 1 else games[0]}/{c["category"]}'
        stem = Path(info['occ'][0][2]).stem
        name = f'{folder}/{stem}.png'
        if name in taken:
            name = f'{folder}/{stem}.{discs[0]}.png'
            if name in taken:
                name = f'{folder}/{stem}.{sha[:8]}.png'
        taken.add(name)
        (out / folder).mkdir(parents=True, exist_ok=True)
        (out / name).write_bytes(info['png'])
        folders.setdefault(folder, []).append((out / name, stem))
        paths = sorted({o[2] for o in info['occ']})
        whole = [f for _, f, rel in info['dumped'] if rel == 'whole']
        regions = sorted({f'{f} ({rel})' for _, f, rel in info['dumped'] if rel != 'whole'})
        if whole:
            pack, match = ' '.join(sorted(set(whole))), 'dumped'
        else:
            pack, match = info['pack'], 'predicted'
        if regions:
            pack += ' | regions: ' + ' '.join(regions)
            if not whole:
                match = 'predicted; regions dumped'
        notes = c['note']
        if whole and info['pack'] not in whole:
            notes += f' | full-picture hash {info["pack"]} differs from the dump'
        rows.append([name, ' '.join(games), ' '.join(discs), c['category'], ' '.join(paths),
                     f'{info["w"]}x{info["h"]}', c['japanese'], '', pack, match, notes,
                     sha])

    cols = ['file', 'games', 'discs', 'category', 'archive_path', 'size', 'japanese',
            'english', 'texture_pack', 'match', 'notes', 'pixel_sha256']
    with open(out / 'index.csv', 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        w.writerows(rows)
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'modding'))
        import script_xlsx as sx
        widths = [34, 10, 18, 10, 30, 9, 40, 40, 34, 12, 40, 20]
        kinds = ['link', 'text', 'text', 'text', 'wrap', 'text', 'wrap', 'edit', 'wrap', 'text', 'wrap', 'text']
        xr = [[('link', r[0], r[0])] + r[1:] for r in rows]
        sx.write_xlsx(out / 'ga2_images.xlsx',
                      [('Images', sx.sheet_xml(list(zip(cols, widths, kinds)), xr),
                        f'$A$1:${sx.col_letter(len(cols) - 1)}${len(rows) + 1}')])
    except Exception as e:  # noqa: BLE001
        log(f'xlsx not written: {e}')
    (out / 'README.md').write_text(README, encoding='utf-8')
    if not args.no_contact:
        (out / 'contact').mkdir(exist_ok=True)
        for folder, items in sorted(folders.items()):
            sheet(items, out / 'contact' / (folder.replace('/', '_') + '.png'))

    # Summary
    from collections import Counter
    by = Counter((r[0].split('/')[0], r[3]) for r in rows)
    for k in sorted(by):
        log(f'  {k[0]:7} {k[1]:9} {by[k]}')
    m = Counter(r[9].split(';')[0] for r in rows)
    log(f'{len(rows)} pictures written; match: {dict(m)}; '
        f'with dumped regions: {sum(1 for r in rows if "regions:" in r[8])}; missing {len(missing)}')


if __name__ == '__main__':
    main()
