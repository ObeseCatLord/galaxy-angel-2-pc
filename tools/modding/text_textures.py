#!/usr/bin/env python3
"""Put translated pictures into a mod's textures/ folder.

usage: text_textures.py <images-dir> <translated-dir> <mod-dir> [--dumps DIR ...]

<images-dir> is the folder made by tools/assets/text_images.py (index.csv and
the original pictures); <translated-dir> holds the edited pictures at the same
relative paths, at 1x or any whole multiple of the size.

1. Each translated picture is copied under the texture-pack name(s) listed in
   index.csv (textures/<category>/<hash>_<w>x<h>.png).  Those names hold when
   the game draws the whole picture in one piece.
2. With --dumps (folders of runtime texture dumps, Options > Dump textures):
   every dumped texture that is a picture, or a piece of one, gets the
   matching piece of the translated picture under the dump's own name.  This
   covers pictures the game draws in parts (tile sheets, atlases).

Writes textures/index.json (texture -> source picture, how it was named).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assets"))
import text_images as ti  # noqa: E402


def load_dump_dirs(dirs: list[Path]) -> list[tuple[str, np.ndarray]]:
    out = {}
    for d in dirs:
        for p in sorted(d.glob("*.png")):
            if re.fullmatch(r"[0-9a-f]{16}_\d+x\d+", p.stem) and p.name not in out:
                out[p.name] = ti.png_words(p)
    return list(out.items())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images")
    ap.add_argument("translated")
    ap.add_argument("mod")
    ap.add_argument("--dumps", nargs="*", default=[], type=Path)
    args = ap.parse_args(argv)
    images, translated, out = Path(args.images), Path(args.translated), Path(args.mod) / "textures"
    rows = list(csv.DictReader(open(images / "index.csv", encoding="utf-8")))
    manifest, missing = {}, []

    def put(name: str, category: str, picture: Image.Image, source: str, how: str):
        dst = out / category / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        picture.save(dst)
        manifest[f"{category}/{name}"] = {"source": source, "named": how}

    pics = []
    for r in rows:
        en = translated / r["file"]
        if not en.exists():
            missing.append(r["file"])
            continue
        jp = Image.open(images / r["file"])
        im = Image.open(en).convert("RGBA")
        k = im.width / jp.width
        if k < 1 or k != int(k) or im.height != jp.height * k:
            raise SystemExit(f"{en}: {im.size} is not a whole multiple of {jp.size}")
        for name in r["texture_pack"].split(" | ")[0].split():
            put(name, r["category"], im, r["file"], r["match"])
        pics.append((r, im, int(k), ti.png_words(images / r["file"])))
    print(f"{len(manifest)} texture(s) from index.csv names; {len(missing)} picture(s) not translated")

    dumps = load_dump_dirs(args.dumps)
    if dumps:
        known = {Path(k).name for k in manifest}
        added = 0
        for r, im, k, arr in pics:
            vals, cnt = np.unique(arr, return_counts=True)
            counts = dict(zip(vals.tolist(), cnt.tolist()))
            for fname, d in dumps:
                if fname in known or d.size < 64 or (d == d.flat[0]).all():
                    continue
                if d.shape == arr.shape:
                    if np.array_equal(d, arr):
                        put(fname, r["category"], im, r["file"], "dumped")
                        known.add(fname)
                        added += 1
                    continue
                for x, y in ti.find_in(arr, d, counts)[:1]:
                    h, w = d.shape
                    piece = im.crop((x * k, y * k, (x + w) * k, (y + h) * k))
                    put(fname, r["category"], piece, r["file"], f"dumped region {x},{y} {w}x{h}")
                    known.add(fname)
                    added += 1
        print(f"runtime dumps: {len(dumps)} file(s); {added} new texture(s) from pictures drawn whole or in parts")
    (out / "index.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
