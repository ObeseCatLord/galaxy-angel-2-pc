#!/usr/bin/env python3
"""Find game state that does not scale with dt: compare EE RAM dumps of a
native run and a high frame rate run at equal game time.

usage: framerate_ramdiff.py <native-t1.bin> <native-t2.bin> <high-t1.bin> [--top N]

The dumps come from GA2_FRAMETRACE_DUMP_DIR / GA2_FRAMETRACE_DUMP_AT (the
native run at game times t1 and t2 = 2 * t1 after the first dialogue text, the
high run at t1). For every 32-bit word that changes between the native dumps
the script asks whether the high run's value at t1 is

  match   the native value at t1 (the state advances with game time: correct),
  fast    the native value at t2 (the state advanced 2x too far: it counts
          iterations, not time, so it runs N/60 times too fast),
  other   neither (noise, different phase, a different path).

Words are tested as float32 and as int32. Contiguous runs of `fast` words are
listed with their guest address, ready for a watch in gdb (GA2_WRAP) to find
the writer. Static words (equal in all three dumps) are ignored.
"""
import argparse, sys
import numpy as np


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("native1")
    ap.add_argument("native2")
    ap.add_argument("high1")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--ratio", type=float, default=2.0, help="t2 / t1 of the native dumps (default 2)")
    ap.add_argument("--range", default="0x100000:0x2000000", help="guest address range to compare")
    args = ap.parse_args()
    lo, hi = (int(x, 0) for x in args.range.split(":"))

    def load(path):
        raw = np.fromfile(path, dtype="<u4")
        return raw[lo // 4:hi // 4]

    a0, a1, h0 = load(args.native1), load(args.native2), load(args.high1)
    n = min(len(a0), len(a1), len(h0))
    a0, a1, h0 = a0[:n], a1[:n], h0[:n]
    changing = a0 != a1
    print(f"{int(changing.sum())} words differ between the native dumps; "
          f"{int((h0 != a0).sum())} words differ between native t1 and high t1")

    f0, f1, fh = (x.view("<f4") for x in (a0, a1, h0))
    i0, i1, ih = (x.view("<i4").astype(np.int64) for x in (a0, a1, h0))

    def classify(v0, v1, vh, finite):
        d = v1 - v0
        scale = np.abs(d)
        near0 = np.abs(vh - v0) <= 0.05 * scale + (1e-6 if v0.dtype.kind == "f" else 0)
        near1 = np.abs(vh - v1) <= 0.05 * scale + (1e-6 if v0.dtype.kind == "f" else 0)
        ok = finite & (scale > 0)
        return ok & near0 & ~near1, ok & near1 & ~near0, ok & ~near0 & ~near1

    finite = np.isfinite(f0) & np.isfinite(f1) & np.isfinite(fh) & (np.abs(f0) < 1e7) & (np.abs(f1) < 1e7) & (np.abs(fh) < 1e7)
    fm, ff, fo = classify(f0.astype(np.float64), f1.astype(np.float64), fh.astype(np.float64), finite & changing)
    # Integer view: small counters only (pointers and timestamps are noise).
    small = (np.abs(i0) < 1 << 24) & (np.abs(i1) < 1 << 24) & (np.abs(ih) < 1 << 24) & changing
    im, if_, io = classify(i0, i1, ih, small)
    # A word is a float or an integer; keep the reading with the stronger evidence.
    match = fm | im
    fast = (ff | if_) & ~match
    other = (fo | io) & ~match & ~fast
    print(f"changing words: match {int(match.sum())}, fast {int(fast.sum())}, other {int(other.sum())}")

    def runs(mask, gap=8):
        idx = np.flatnonzero(mask)
        if not len(idx):
            return []
        out, start, prev = [], idx[0], idx[0]
        for i in idx[1:]:
            if i - prev > gap:
                out.append((start, prev))
                start = i
            prev = i
        out.append((start, prev))
        return out

    for name, mask in (("FAST (runs N/60 too fast: not scaled by dt)", fast),):
        rs = runs(mask)
        rs.sort(key=lambda r: r[0] - r[1])
        print(f"\n{name}: {len(rs)} regions (largest first)")
        for s, e in rs[: args.top]:
            addr = lo + 4 * int(s)
            words = int(e - s + 1)
            sel = np.flatnonzero(mask[s:e + 1])[:6] + s
            sample = "  ".join(f"{lo + 4 * int(j):#x}: {a0[j]:08x}->{a1[j]:08x} high {h0[j]:08x} (f {f0[j]:.5g}->{f1[j]:.5g} high {fh[j]:.5g})"
                               for j in sel[:3])
            print(f"  {addr:#08x}  {int(mask[s:e + 1].sum()):4d} words in {words * 4:#x} bytes   {sample}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
