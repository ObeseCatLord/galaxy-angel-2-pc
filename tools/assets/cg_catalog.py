#!/usr/bin/env python3
"""Build a CG (or, with --kind bg, background) catalog from the private assets dump.

Reads <dump>/<game>/index.csv for every game, deduplicates CGs by sha256,
derives characters from the Japanese gallery labels, groups expression/state
variants (iv08_12a, iv08_12b, ...) and computes image statistics used to
classify finished illustrations versus sketches, storyboards, blank screens
and text cards.

Never modifies the dump. Writes:
  <out>/catalog.json    one record per unique image, every original location
  <out>/catalog.csv     same, flattened
  <out>/by-character/<Name>/<game>/<original name>.png  symlinks
  <out>/by-category/<category>/<game>/<original name>.png  symlinks

Manual corrections live in <out>/overrides.json:
  {"<sha256>": {"category": "...", "characters": [...], "en": "..."}}
or keyed by "<game>/<original name>" for convenience.
"""
import argparse
import csv
import json
import os
import re
from pathlib import Path

import numpy as np
from PIL import Image

GAMES = ["zettai", "mugen", "eigou"]

# Gallery-label spellings that are not the sprite table's primary name, or
# that the sprite table maps ambiguously (shared bodies such as ソルダム).
EXTRA_NAMES = {
    "カズヤ": "Kazuya", "アプリコット": "Apricot", "ナノナノ": "NanoNano",
    "カルーア": "Kahlua", "テキーラ": "Tequila", "アニス": "Anise",
    "リリィ": "Lily", "ココ": "Coco", "ロゼル": "Roselle", "ソルダム": "Soldum",
    "サンタローザ": "SantaRosa", "ケルシー": "Kelsey", "ジュニエヴル": "Genievre",
    "真カルーア": "Berita", "ナツメ": "Natsume", "幼ナツメ": "YoungNatsume",
    "ミルフィーユ": "Milfeulle", "ちとせ": "Chitose", "タクト": "Tact",
    "ヒビキ": "Hibiki", "シュリ": "Shuri", "直": "Nao", "ルコ": "Ruko",
    "カトリーヌ": "Catherine", "スタンレイ": "Soldum", "リコ": "Apricot",
}
# Sprite-table keys that are props, crowds or generic NPCs, not characters.
NOT_CHARACTERS = {
    "Arrow", "BlackCurtainLeft", "BlackCurtainRight", "Everyone",
    "ExpressionTest", "FourMoons", "GreenMoon", "HotSpringBottom",
    "OdenCounter", "Statue", "LuxiolCrew_051",
    # Generic roles: in gallery labels these are ordinary nouns
    # (メイドのアニス = "maid Anise"), not the NPC sprite.
    "AllianceSoldier", "ArmorB", "ArmorC", "ArmorD", "CeldarEnvoy", "CeldarSentry",
    "Child", "Clerk", "ConvenienceStoreClerk", "CurryShopClerk", "CustomerA",
    "CustomerB", "DepartmentStoreClerkA", "DepartmentStoreClerkB", "DitaArmySoldier",
    "Guard", "GuardCaptain", "HatchetResident", "Host", "Lady", "LuxiolCrew", "Maid",
    "Nurse", "OperatorA", "OperatorB", "PicoAdministrator", "Pirate", "RoyalWitchA",
    "RoyalWitchB", "Security", "Soldier", "SurferA", "SurferB", "SurferC",
    "Terrorist", "Thug", "WomanA", "WomanB",
}


def load_names(dump):
    names = {}
    for g in GAMES:
        p = dump / g / "sprites" / "names.json"
        if not p.exists():
            continue
        for key, v in json.loads(p.read_text()).items():
            if key in NOT_CHARACTERS:
                continue
            jp = v.get("japanese")
            if jp:
                names.setdefault(jp, key)
    names.update(EXTRA_NAMES)
    # Longest first so ナノナノ（大人） style names win over their prefixes.
    return sorted(names.items(), key=lambda kv: -len(kv[0]))


def label_characters(label, names):
    found, rest = [], label
    for jp, key in names:
        if len(jp) == 1:
            continue  # single kanji names (直, 梅) give false hits in prose
        if jp in rest:
            if key not in found:
                found.append(key)
            rest = rest.replace(jp, "\0")
    return found


def group_of(name):
    stem = Path(name).stem
    stem = re.sub(r"\.(zettai|mugen|eigou)(-\d)?$", "", stem)
    return re.sub(r"[a-z]$", "", stem)


def image_stats(path):
    im = Image.open(path).convert("RGB")
    im.thumbnail((256, 256))
    a = np.asarray(im).astype(np.float32) / 255.0
    mx, mn = a.max(axis=2), a.min(axis=2)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    lum = a @ np.array([0.299, 0.587, 0.114], np.float32)
    q = (np.asarray(im) // 16).reshape(-1, 3)
    _, counts = np.unique(q, axis=0, return_counts=True)
    counts = np.sort(counts)[::-1]
    gy, gx = np.gradient(lum)
    return {
        "sat_mean": round(float(sat.mean()), 4),
        "colorful_frac": round(float((sat > 0.2).mean()), 4),
        "lum_std": round(float(lum.std()), 4),
        "white_frac": round(float((lum > 0.85).mean()), 4),
        "top_color_frac": round(float(counts[0] / counts.sum()), 4),
        "colors": int(len(counts)),
        "edge": round(float(np.hypot(gx, gy).mean()), 4),
    }


def classify(st, label, name):
    if name.startswith("iv00_ed00_"):
        return "credits-doodle"  # sepia chibi doodles shown on chapter endings
    if st["lum_std"] < 0.02 or st["top_color_frac"] > 0.92:
        return "blank"
    if st["colorful_frac"] < 0.03 and st["white_frac"] > 0.4:
        return "sketch"
    if st["colors"] < 40 and st["top_color_frac"] > 0.6:
        return "text-card"
    if "（大サイズ）" in label:
        return "large-version"
    return "illustration"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--no-stats", action="store_true")
    ap.add_argument("--kind", choices=["cg", "bg"], default="cg",
                    help="bg catalogs backgrounds/ (no character tagging)")
    args = ap.parse_args()
    dump, out = args.dump.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    names = load_names(dump)
    prefix = "cg/" if args.kind == "cg" else "backgrounds/"

    old = {}
    if (out / "catalog.json").exists():
        old = {r["sha256"]: r for r in json.loads((out / "catalog.json").read_text())}
    overrides = {}
    if (out / "overrides.json").exists():
        overrides = json.loads((out / "overrides.json").read_text())

    recs = {}
    for g in GAMES:
        idx = dump / g / "index.csv"
        if not idx.exists():
            continue
        for r in csv.DictReader(idx.open()):
            if not r["output"].startswith(prefix):
                continue
            sha = r["sha256"]
            rec = recs.setdefault(sha, {
                "sha256": sha, "file": f"{g}/{r['output']}", "name": Path(r["output"]).name,
                "group": group_of(r["output"]), "width": int(r["width"]),
                "height": int(r["height"]), "label": "", "occurrences": [],
            })
            occ = {"game": g, "disc": r["disc"], "file": f"{g}/{r['output']}",
                   "archive_path": r["archive_path"], "archive": r["archives"]}
            if occ not in rec["occurrences"]:
                rec["occurrences"].append(occ)
            if r["label"] and not rec["label"]:
                rec["label"] = r["label"]

    # Variants without a gallery label inherit the characters of labeled siblings.
    by_group = {}
    for rec in recs.values():
        # Background labels name rooms (アプリコットの部屋), never people.
        rec["characters"] = label_characters(rec["label"], names) if args.kind == "cg" else []
        by_group.setdefault(rec["group"], []).append(rec)
    for rec in recs.values():
        if not rec["label"]:
            sib = [c for s in by_group[rec["group"]] for c in s["characters"]]
            rec["characters"] = list(dict.fromkeys(sib))
            rec["characters_source"] = "group" if sib else "none"
        else:
            rec["characters_source"] = "label"

    for sha, rec in recs.items():
        prev = old.get(sha, {})
        if args.no_stats and "stats" in prev:
            rec["stats"] = prev["stats"]
        else:
            rec["stats"] = image_stats(dump / rec["file"])
        rec["category"] = classify(rec["stats"], rec["label"], rec["name"])
        if args.kind == "bg" and rec["category"] == "illustration":
            rec["category"] = "background"
        if rec["label"].startswith("旧ＧＡ"):
            # Old-GA location art; "ミルフィーユの部屋" names a room, not a person.
            rec["category"], rec["characters"] = "location-art", []
        for k in ("en", "tags"):
            if k in prev:
                rec[k] = prev[k]
        # Group keys collide across games (iv01_00a differs per disc), so they
        # only fill records the gallery label did not already identify.
        keys = [f"group:{rec['group']}"] if not rec["characters"] else []
        keys += [sha] + [o["file"].replace(f"/{prefix}", "/") for o in rec["occurrences"]]
        for key in keys:
            if key in overrides:
                rec.update(overrides[key])
                rec["characters_source"] = "manual"

    rows = sorted(recs.values(), key=lambda r: r["file"])
    (out / "catalog.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    with (out / "catalog.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sha256", "file", "group", "width", "height", "category",
                    "characters", "label", "en", "occurrences"])
        for r in rows:
            w.writerow([r["sha256"], r["file"], r["group"], r["width"], r["height"],
                        r["category"], " ".join(r["characters"]), r["label"],
                        r.get("en", ""), " ".join(o["file"] for o in r["occurrences"])])

    for view in ("by-character", "by-category"):
        d = out / view
        if d.exists():
            for p in sorted(d.rglob("*"), reverse=True):
                p.unlink() if p.is_symlink() or p.is_file() else p.rmdir()
    for r in rows:
        keys = [("by-category", r["category"])]
        keys += [("by-character", c) for c in (r["characters"] or ["_none"])]
        for view, k in keys:
            for o in r["occurrences"]:
                dst = out / view / k / o["game"] / Path(o["file"]).name
                dst.parent.mkdir(parents=True, exist_ok=True)
                if not dst.exists():
                    os.symlink(dump / o["file"], dst)

    cats = {}
    for r in rows:
        cats[r["category"]] = cats.get(r["category"], 0) + 1
    print(len(rows), "unique;", cats)


if __name__ == "__main__":
    main()
