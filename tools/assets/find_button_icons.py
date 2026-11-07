#!/usr/bin/env python3
"""Find PlayStation button icons in the games' pictures (prompt audit).

    python3 tools/assets/find_button_icons.py <private> [--out DIR] [--jobs N]

Templates: every icon that ``native/buttons/map.json`` lists, cut from the
runtime texture dumps ``<private>/research/buttons/dump-*`` (files
``<hash>_<w>x<h>.png``).  Search space: the pictures of all five discs as
``tools/assets/dump_images.py --all`` writes them
(``<private>/research/assets-dump/<game>/other/...``; portraits, CGs and
thumbnails are skipped).  Each template is matched at several sizes with a
masked sum of squared differences (FFT), on the colour composited over black;
a match must be close relative to the template's own contrast.

Output (default ``<private>/research/run58/prompts``): ``icons.csv`` (one
row per icon found: picture, archive paths, button, rectangle, score) and
``icons-sheet-<n>.png`` contact sheets of the finds.  Pictures whose icons
``map.json`` already covers still appear; ``--map`` reconciliation is done
by comparing the runtime hash of the whole picture (``text_images.py``) and
by name.
"""

from __future__ import annotations

import argparse
import csv
import json
import multiprocessing as mp
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy.signal import fftconvolve

SOURCE = Path(__file__).resolve().parents[2]
SKIP = ("sprites", "cg", "backgrounds", "cg-thumbnails", "bg-thumbnails", "movie-thumbnails", "adv_model",
        "common_model", "slg_machines", "bg-names", "eyecatch")
SCALES = (0.55, 0.7, 0.85, 1.0, 1.2, 1.45)


def rgb_over_black(im: Image.Image) -> np.ndarray:
    a = np.asarray(im.convert("RGBA"), dtype=np.float32)
    return a[..., :3] * (a[..., 3:4] / 255.0)


def stadium(w: int, h: int) -> np.ndarray:
    y, x = np.mgrid[0:h, 0:w] + 0.5
    r = min(w, h) / 2 - 0.5
    cx = np.clip(x, min(w, h) / 2, w - min(w, h) / 2)
    return ((x - cx) ** 2 + (y - h / 2) ** 2 <= r * r).astype(np.float32)


def load_templates(private: Path):
    spots = json.loads((SOURCE / "native/buttons/map.json").read_text())["textures"]
    files = {}
    for d in sorted((private / "research/buttons").glob("dump-*")):
        for f in d.glob("*.png"):
            files.setdefault(f.name.split("_")[0], f)
    out, seen = [], set()
    for h, entries in spots.items():
        if h not in files:
            continue
        img = Image.open(files[h])
        for e in entries:
            crop = img.crop((e["x"], e["y"], e["x"] + e["w"], e["y"] + e["h"]))
            key = (e["button"], crop.size, crop.convert("RGBA").tobytes())
            if key in seen:
                continue
            seen.add(key)
            out.append((e["button"], f"{h}:{e.get('what', '')}", rgb_over_black(crop)))
    return out


def match(img: np.ndarray, tpl: np.ndarray):
    """Masked RMS difference of tpl at every placement in img (valid positions)."""
    h, w = tpl.shape[:2]
    m = stadium(w, h)
    n = m.sum()
    ssd = np.zeros((img.shape[0] - h + 1, img.shape[1] - w + 1), np.float32)
    for c in range(3):
        I, T = img[..., c], tpl[..., c]
        mi2 = fftconvolve(I * I, m[::-1, ::-1], mode="valid")
        mti = fftconvolve(I, (m * T)[::-1, ::-1], mode="valid")
        ssd += mi2 - 2 * mti + (m * T * T).sum()
    rms = np.sqrt(np.maximum(ssd, 0) / (3 * n))
    contrast = np.sqrt((((tpl - (tpl * m[..., None]).sum((0, 1)) / n) ** 2) * m[..., None]).sum() / (3 * n))
    return rms, contrast


TEMPLATES = []


def init(private):
    global TEMPLATES
    TEMPLATES = []
    for button, src, t in load_templates(private):
        for s in SCALES:
            w, h = max(8, round(t.shape[1] * s)), max(8, round(t.shape[0] * s))
            ts = np.asarray(Image.fromarray(t.astype(np.uint8)).resize((w, h), Image.BILINEAR), np.float32)
            TEMPLATES.append((button, src, s, ts))


def scan(path: str):
    try:
        img = rgb_over_black(Image.open(path))
    except Exception:
        return path, []
    found = []
    for button, src, s, t in TEMPLATES:
        h, w = t.shape[:2]
        if h > img.shape[0] or w > img.shape[1]:
            continue
        rms, contrast = match(img, t)
        if contrast < 20:
            continue
        limit = min(28.0, 0.33 * contrast)
        ys, xs = np.nonzero(rms < limit)
        for y, x in zip(ys, xs):
            found.append((float(rms[y, x] / contrast), button, int(x), int(y), w, h, src))
    found.sort()
    kept = []
    for f in found:
        _, _, x, y, w, h, _ = f
        if all(min(x + w, k[2] + k[4]) - max(x, k[2]) <= 0.3 * w or min(y + h, k[3] + k[5]) - max(y, k[3]) <= 0.3 * h
               for k in kept):
            kept.append(f)
    return path, kept


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("private", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--only", help="scan only paths containing this text")
    a = ap.parse_args(argv)
    out = a.out or a.private / "research/run58/prompts"
    out.mkdir(parents=True, exist_ok=True)
    root = a.private / "research/assets-dump"
    paths = []
    for g in ("zettai", "mugen", "eigou"):
        for p in sorted((root / g / "other").rglob("*.png")):
            if not any(part in SKIP for part in p.relative_to(root / g).parts) and (not a.only or a.only in str(p)):
                paths.append(str(p))
    archives = {}
    for g in ("zettai", "mugen", "eigou"):
        with open(root / g / "index.csv", newline="") as f:
            for row in csv.DictReader(f):
                archives.setdefault(str(root / g / row["output"]), set()).add(f"{row['disc']}:{row['archive_path']}")
    init(a.private)
    print(f"{len(TEMPLATES)} templates, {len(paths)} pictures", file=sys.stderr)
    rows = []
    with mp.Pool(a.jobs, initializer=init, initargs=(a.private,)) as pool:
        for i, (path, kept) in enumerate(pool.imap_unordered(scan, paths, chunksize=8)):
            for score, button, x, y, w, h, src in kept:
                rows.append(dict(picture=os.path.relpath(path, root), archives=" ".join(sorted(archives.get(path, ()))),
                                 button=button, x=x, y=y, w=w, h=h, score=f"{score:.3f}", template=src))
            if i % 500 == 0:
                print(f"  {i}/{len(paths)}", file=sys.stderr)
    rows.sort(key=lambda r: (r["picture"], r["y"], r["x"]))
    with open(out / "icons.csv", "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["picture"])
        wr.writeheader()
        wr.writerows(rows)
    # Contact sheets: each find with some context.
    cells = []
    for r in rows:
        im = Image.open(root / r["picture"]).convert("RGBA")
        x, y, w, h = r["x"], r["y"], r["w"], r["h"]
        c = im.crop((x - w, y - h // 2, x + 3 * w, y + h + h // 2)).resize((4 * w * 2, 2 * h * 2))
        bg = Image.new("RGBA", c.size, (60, 60, 60, 255))
        bg.alpha_composite(c)
        ImageDraw.Draw(bg).rectangle((w * 2, h, w * 4, h * 3), outline=(255, 255, 0, 255))
        cells.append((f"{r['button']} {Path(r['picture']).stem}", bg.resize((240, 120))))
    for s in range(0, len(cells), 48):
        sheet = Image.new("RGB", (6 * 240, 8 * 136), (30, 30, 30))
        d = ImageDraw.Draw(sheet)
        for i, (label, c) in enumerate(cells[s:s + 48]):
            X, Y = (i % 6) * 240, (i // 6) * 136
            sheet.paste(c.convert("RGB"), (X, Y + 14))
            d.text((X + 2, Y), label[:38], fill=(255, 255, 255))
        sheet.save(out / f"icons-sheet-{s // 48:02d}.png")
    print(f"{len(rows)} icons in {len({r['picture'] for r in rows})} pictures -> {out / 'icons.csv'}", file=sys.stderr)


if __name__ == "__main__":
    main()
