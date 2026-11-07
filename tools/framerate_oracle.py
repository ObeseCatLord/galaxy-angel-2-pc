#!/usr/bin/env python3
"""Timing oracle for GA2_FPS: compare a native run with a high frame rate run
at equal game time.

usage: framerate_oracle.py <native.trace> <high.trace> [options]

Both traces come from GA2_FRAMETRACE=<file> (runtime/ga2_framepace.h, hooks in
native/compat/ga2_framerate.cpp). Every line carries the game time `gt` (the
sum of the dt the game was given, so it advances with the game and not with the
wall clock), the wall time, the guest vsync count and the frame tick:

  U  state after iteration n: dt of the next step, frames/maxFrames/minFrames,
     elapsed (native vsyncs), message characters revealed, scene id, ...
  M  view matrices set during that iteration (16 floats, caller address ra)
  W  the first world (object) matrices of the iteration
  E  events: sound key-on (kon), CD reads (cd), scripted pad presses (pad), ...
  F  fingerprint of each frame the GS scanned out (GA2_FRAMETRACE_FRAMES=1):
     16 x 14 grid of mean luma, tagged with the game time of the iteration
     that drew it
  T  2D layer items whose placement changed (GA2_FRAMETRACE_ITEMS=1)
  R  the game's requests to its IOP services (GA2_FRAMETRACE_RPC=1)
  P  probed guest memory words (GA2_FRAMETRACE_PROBE)

What is compared. dt-scaled logic gives the same state at the same game time;
code that counts iterations gives a different one. An animation started by an
event begins at the first iteration after the event, so its phase can differ
by up to one native frame (16.7 ms) between runs: that is the tolerance, the
same one the task sets for events. Each high sample (matrix, frame
fingerprint, item placement) must therefore lie inside the range the native
run covers within +-1 native frame of the same game time (per component,
plus a small tolerance). An effect running 2x too fast leaves that range
after a frame or two; so does one that runs N/60 times too fast at any rate.

Alignment. The runs are aligned on an anchor event (--anchor). Between loads
the game is deterministic in game time, but a load (a slow frame: frames > 1 in
the U lines, or a movie) finishes at an IOP-timed moment and the scene after it
starts a whole number of iterations earlier or later. The timeline is
therefore cut at every late iteration of either run (plus a margin: 0.25 s,
2.5 s after a load of ten or more frames) and each clean segment between them is
aligned on its own, by whole iterations of the high run, to where its frames
match best (a block counts at most 40 levels, so a region that fades is decisive
and an effect that differs is not). Only soon after a load (12 s) may the shift
exceed 34 ms (up to --align seconds); it is reported. A speed error cannot be
absorbed by a shift. --sid N compares only the times both runs are in scene N
(1 boot, 9 title/load/save, 2 adventure + ship map, 3 battle, 5 options, 12 disc
change; messages are filtered too).

The 30 fps views (the game's minFrames 2: the native run has a sample every 33 ms
and reads the pad once per iteration) are compared with windows of one native
iteration (matrices, items), 1.6 iterations (text) and, for picture cuts, 2.1
native frames or 2.6 native iterations (input -> state -> draw is a pipeline a
few iterations deep that is shorter at a higher rate).

Checks (PASS):
  camera/objects  every matrix component inside the native range (rotation
                  entries 3/4 of the largest native step, translation 1%)
  frames          no block of the 16 x 14 luma grid more than --frame-tol
                  levels (default 40) outside the native range; isolated
                  single frames up to 0.2% of the frames compared are blips
  smooth          during motion the high run shows a new picture on (nearly) every
                  iteration: per 1 s window the distinct pictures per second
                  (consecutive flips whose fingerprint -- 16 x 14 luma grid plus the
                  mean colour -- differ) in both runs, and per burst (5+ consecutive
                  changing native flips) the share of iterations with a new picture
  items           each 2D layer item inside the native range (--item-tol of
                  its largest native per-frame step)
  probes          probed words equal the native run's at the same game time
  rng             the generator's draws per game second and caller are not more
                  than 1.35 times the native run's
  text            each message character appears within one native frame (two
                  native iterations in a 30 fps view); a message starts at most
                  two native frames later and about 100 ms earlier (not graded
                  within 12 s of a load)
  pad             scripted presses at the same game time
  rpc             rare IOP requests (<= --rpc-max per run) at the same time
                  within one native frame after the segment's shift
Key-ons and CD reads are IOP-timed (the BGM sequencer, streaming, voice lines
that scene scripts wait for) and are only listed: when the host runs the two
runs at different fractions of real time (the battle: 79 % native, 65 % high on
the test host) events that wait for them land at different game times (by
about 100-150 ms after 50 s of battle), which no check can separate from an
error; compare such sequences only over the stretch before they diverge
(--duration). Exit status 0 if every check passes.
"""
import argparse, bisect, json, math, struct, sys
from collections import defaultdict

NATIVE_FRAME = 1001.0 / 60000.0
WINDOW = NATIVE_FRAME * 1.02   # the range a sample may fall in: +-1 native frame of the same game time
_STEPS = ([], [])              # the native run's iteration times (relative to its anchor) and steps
_PADS = []                     # the high run's scripted pad events (relative to its anchor)


def after_pad(t, span=0.3):
    """True when a pad event happened within the last `span` seconds (input -> state -> draw pipeline)."""
    i = bisect.bisect_right(_PADS, t + 0.05) - 1
    return i >= 0 and t - _PADS[i] <= span



def win(t, pipeline=False):
    """The window around native time t: one native frame, or one native iteration in a 30 fps view (the native run has no
    sample between its iterations, and a state change lands on the iteration grid of each run)."""
    times, dts = _STEPS
    if not times:
        return WINDOW
    i = min(max(bisect.bisect_left(times, t), 0), len(dts) - 1)
    # a 30 fps view samples the pad once per 33 ms iteration, a higher rate every 8 ms: an input-triggered change can appear
    # up to one native iteration plus the pipeline's frame earlier
    # (pipeline=True, for picture cuts: input -> state -> draw is two or three iterations deep, each 25 ms shorter at 120)
    if pipeline:
        return max(2.1 * NATIVE_FRAME, dts[i] * 2.6)
    return max(WINDOW, dts[i] * (1.6 if dts[i] > 1.5 * NATIVE_FRAME else 1.02))
INT_FIELDS = ("n", "i", "fr", "mx", "mn", "gl", "gc", "gn", "fl", "sid", "sarg", "draws", "rn")


def parse(path):
    runs = {k: [] for k in "UMWEFPDRTG"}
    probe_seen = [None, 0]
    with open(path, errors="replace") as f:
        for line in f:
            if len(line) < 3 or line[0] not in runs or line[1] != " ":
                continue
            rec = {}
            for item in line.split()[1:]:
                if "=" in item:
                    k, v = item.split("=", 1)
                    rec[k] = v
            try:
                rec["gt"] = float(rec["gt"])
                rec["wall"] = float(rec["wall"])
                rec["vs"] = int(rec["vs"])
            except (KeyError, ValueError):
                continue
            # A line torn by a write from another thread (frame lines come from the GS thread) is dropped.
            try:
                if "m" in rec:
                    rec["m"] = [float(x) for x in rec["m"].split(",")]
                if "item" in rec:
                    rec["v"] = [float(x) for x in rec["v"].split(",")]
                if "blocks" in rec:
                    rec["blocks"] = [int(rec["blocks"][i:i + 2], 16) for i in range(0, len(rec["blocks"]), 2)]
                    rec["mean"] = [float(x) for x in rec["mean"].split(",")]
                    rec["flip"] = int(rec["flip"])
            except (KeyError, ValueError):
                continue
            for key in INT_FIELDS:
                if key in rec:
                    try:
                        rec[key] = int(rec[key])
                    except ValueError:
                        pass
            for key in ("dt", "el"):
                if key in rec:
                    rec[key] = float(rec[key])
            if line[0] == "P":
                # The probe's key: its address (without the old `*`) and its place among that iteration's probes.
                rec["a"] = rec.get("a", "").rstrip("*")
                if (rec.get("n"), rec["a"]) != probe_seen[0]:
                    probe_seen[0], probe_seen[1] = (rec.get("n"), rec["a"]), 0
                else:
                    probe_seen[1] += 1
                rec["h"] = f"#{probe_seen[1]}"
            runs[line[0]].append(rec)
    return runs


def find_anchor(run, spec):
    """Game time of the anchor in one run."""
    kind, _, arg = spec.partition(":")
    if kind == "gt":
        return float(arg)
    if kind == "glyph":
        for u in run["U"]:
            if u.get("gc", 0) > 0:
                return u["gt"]
    elif kind in ("kon", "cd"):
        want, seen = int(arg or 1), 0
        for e in run["E"]:
            if e.get("kind") == kind and (kind == "kon" or e["gt"] > 0):
                seen += 1
                if seen == want:
                    return e["gt"]
    elif kind == "lsn":
        for e in run["E"]:
            if e.get("kind") == "cd" and e.get("lsn") == arg:
                return e["gt"]
    elif kind == "pad":
        want, seen = int(arg or 1), 0
        for e in run["E"]:
            if e.get("kind") == "pad" and e.get("mask") != "0000":
                seen += 1
                if seen == want:
                    return e["gt"]
    elif kind == "scene":
        # the n-th change of the scene id (U lines' sid)
        want, seen, last = int(arg or 1), 0, None
        for u in run["U"]:
            if last is not None and u.get("sid") != last:
                seen += 1
                if seen == want:
                    return u["gt"]
            last = u.get("sid")
    elif kind == "probe":
        # the first time the probed location (hex address) is readable: e.g. the battle manager pointer appearing
        for q in run["P"]:
            if q.get("a") == arg.rstrip("*") and q.get("v", "null") != "null":
                return q["gt"]
    elif kind == "view":
        for m in run["M"]:
            return m["gt"]
    raise SystemExit(f"anchor {spec!r} not found in a run")


class Series:
    """Sorted (t, vector) samples with linear interpolation."""

    def __init__(self, samples):
        samples.sort(key=lambda s: s[0])
        self.t = [s[0] for s in samples]
        self.v = [s[1] for s in samples]

    def at(self, t):
        i = bisect.bisect_right(self.t, t)
        if i == 0 or i >= len(self.t):
            return None
        t0, t1 = self.t[i - 1], self.t[i]
        a = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
        return [x + (y - x) * a for x, y in zip(self.v[i - 1], self.v[i])]

    def bounds(self, t, w):
        """Per-component (min, max) of the path over [t - w, t + w] (interpolated at the ends)."""
        lo, hi = bisect.bisect_left(self.t, t - w), bisect.bisect_right(self.t, t + w)
        pts = list(self.v[lo:hi])
        for edge in (t - w, t + w):
            v = self.at(edge)
            if v is not None:
                pts.append(v)
        if not pts:
            return None
        return [min(c) for c in zip(*pts)], [max(c) for c in zip(*pts)]

    def coincident(self, t, tol=0.0003):
        i = bisect.bisect_left(self.t, t - tol)
        return i if i < len(self.t) and abs(self.t[i] - t) <= tol else None


# ---------------------------------------------------------------- timeline

class Timeline:
    """Clean segments (no late iteration in either run) with their shifts."""

    def __init__(self, native, high, an, ah, t_lo, t_hi, align, margin=0.25, sid=None):
        late = []
        for run, anchor in ((native, an), (high, ah)):
            for u in run["U"]:
                # late: more frames elapsed than the game's own minimum (the 30 fps views have minFrames 2), or the
                # EE-side work took nearly a whole frame. A load (many frames) is followed by a fade-in whose start is
                # tied to the IOP's completion time: a longer margin after those.
                if u.get("fr", 1) > max(u.get("mn", 1), 1) or u.get("el", 0.0) > 0.9:
                    big = u.get("fr", 1) >= 10 * max(u.get("mn", 1), 1) or u.get("el", 0.0) > 0.9
                    late.append((u["gt"] - anchor, big))
                elif sid is not None and u.get("sid") != sid:
                    late.append((u["gt"] - anchor, False))  # another scene: not part of this comparison
        late.sort()
        blocks = []
        for t, big in late:
            if blocks and t - blocks[-1][1] < 0.6 + (2.0 if blocks[-1][2] else 0.0):
                blocks[-1][1] = t
                blocks[-1][2] = blocks[-1][2] or big
            else:
                blocks.append([t, t, big])
        self.dirty = [(a - 0.05, b + (2.5 if big else margin)) for a, b, big in blocks]
        self.segments = []
        cur = t_lo
        for a, b in self.dirty:
            if a - cur >= 0.4 and cur < t_hi:
                self.segments.append([cur + 0.02, min(a, t_hi), 0.0])
            cur = max(cur, b)
        if t_hi - cur >= 0.4:
            self.segments.append([cur + 0.02, t_hi, 0.0])
        self.clean = [list(s) for s in self.segments]
        # Windows of at most a second: the phase between scene content and the anchor can change inside a clean segment too.
        windows = []
        for a, b, _ in self.segments:
            t = a
            while t < b - 1e-9:
                end = min(b, t + 1.0) if b - t > 1.5 else b
                windows.append([t, end, 0.0])
                t = end
        self.segments = windows
        self.starts = [s[0] for s in self.segments]
        self.native, self.high, self.an, self.ah, self.align = native, high, an, ah, align
        if align > 0:
            self._align_segments()

    def _frames(self, run, anchor):
        seen, out = set(), []
        for f in run["F"]:
            if f["flip"] not in seen:
                seen.add(f["flip"])
                out.append((f["gt"] - anchor, f["blocks"]))
        out.sort(key=lambda x: x[0])
        return out

    def _align_segments(self):
        nf, hf = self._frames(self.native, self.an), self._frames(self.high, self.ah)
        if len(nf) < 4 or len(hf) < 4:
            return
        us = [u for u in self.high["U"] if u["gt"] > self.ah + 1.0]
        step = us[1]["gt"] - us[0]["gt"] if len(us) > 1 else NATIVE_FRAME
        steps = max(1, int(self.align / step))
        near_steps = max(1, int(0.034 / step))
        times = [f[0] for f in nf]
        htimes = [f[0] for f in hf]
        ends = [b for a, b in self.dirty]
        prev = None  # the previous window of this clean stretch: (end, shift)
        for seg in self.segments:
            # Only soon after a stretch cut at a late iteration (a load: its end is paced by the IOP, host time) can the
            # phase differ by more than about two native frames.
            j = bisect.bisect_right(ends, seg[0]) - 1
            after_load = j >= 0 and seg[0] - ends[j] < 12.0
            seg_steps = steps if after_load or not ends else near_steps
            lo = bisect.bisect_left(htimes, seg[0])
            hi = bisect.bisect_right(htimes, seg[1])
            sample = hf[lo:hi:max(1, (hi - lo) // 40)]
            if len(sample) < 3:
                continue
            best = None
            worst_cost = 0.0
            carried = prev[1] if prev is not None and abs(prev[0] - seg[0]) < 1e-6 else None
            for k in sorted(range(-seg_steps, seg_steps + 1), key=abs):
                d = k * step
                total = count = 0
                for t, blocks in sample:
                    t += d
                    i = bisect.bisect_right(times, t)
                    if i == 0 or i >= len(nf):
                        continue
                    (t0, b0), (t1, b1) = nf[i - 1], nf[i]
                    a = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
                    # robust: every block counts, but none by more than 40 levels (a few blocks of an effect that differs do
                    # not decide the phase, a small fading region does)
                    total += sum(min(abs(h - (x + (y - x) * a)), 40.0) for h, x, y in zip(blocks, b0, b1))
                    count += 1
                if count:
                    worst_cost = max(worst_cost, total / count)
                if count and (best is None or total / count < best[0] * 0.85 - 0.3):
                    best = (total / count, d)
            if best is not None:
                seg[2] = best[1]
                # A window without motion (every shift fits equally) takes the shift of the window before it.
                if carried is not None and worst_cost - best[0] < 1.0:
                    seg[2] = carried
            prev = (seg[1], seg[2])

    def shifts_near(self, t, d):
        """The window's shift d first, then the other shifts of the windows next to it in the same clean stretch."""
        out = [d]
        i = bisect.bisect_right(self.starts, t) - 1
        for j in (i - 1, i + 1):
            if 0 <= j < len(self.segments) and abs(self.segments[j][0] - self.segments[i][0]) < 1.5:
                if all(abs(self.segments[j][2] - x) > 1e-9 for x in out):
                    out.append(self.segments[j][2])
        return out

    def after_load(self, t, span=12.0):
        """True within `span` seconds after a stretch that was cut at a load (its end is paced by the IOP, host time)."""
        i = bisect.bisect_right([b for a, b in self.dirty], t) - 1
        return i >= 0 and t - self.dirty[i][1] < span

    def shift(self, t):
        """The shift of the clean segment containing high-run time t, or None in a dirty stretch."""
        i = bisect.bisect_right(self.starts, t) - 1
        if i >= 0 and t <= self.segments[i][1]:
            return self.segments[i][2]
        return None

    def describe(self):
        if not self.segments:
            return "no clean segments"
        total = sum(b - a for a, b, _ in self.segments)
        shifts = sorted(s[2] * 1000 for s in self.segments)
        return (f"{len(self.clean)} clean segments in {len(self.segments)} windows ({total:.1f} s; {len(self.dirty)} stretches with late "
                f"iterations skipped), alignment shifts {shifts[0]:+.1f}..{shifts[-1]:+.1f} ms (median {shifts[len(shifts) // 2]:+.1f})")


# ---------------------------------------------------------------- checks

def streams(matrices, anchor):
    out = defaultdict(list)
    for m in matrices:
        out[(m["i"], m["ra"])].append((m["gt"] - anchor, m["m"]))
    return out


def check_matrices(native, high, key, an, ah, tl, rot_tol=2e-4):
    """-> (lines, failing stream count, rows)"""
    ns, hs = streams(native[key], an), streams(high[key], ah)
    rows = []
    for k, hsamples in hs.items():
        nseries = Series(list(ns.get(k, [])))
        if len(nseries.t) < 8:
            continue
        step_tr = step_rot = 0.0
        for i in range(1, len(nseries.t)):
            step_tr = max(step_tr, max(abs(nseries.v[i][j] - nseries.v[i - 1][j]) for j in (12, 13, 14)))
            step_rot = max(step_rot, max(abs(nseries.v[i][j] - nseries.v[i - 1][j]) for j in (0, 1, 2, 4, 5, 6, 8, 9, 10)))
        # three quarters (rotation) / one percent (translation) of the largest native per-frame step is allowed on top of the
        # +-1 frame range (eased paths are not linear between the native samples)
        tol_rot, tol_tr = max(rot_tol, 0.75 * step_rot), max(1e-3, 0.01 * step_tr)
        count, worst_rot, worst_tr, worst_t = 0, 0.0, 0.0, None
        for t, vec in hsamples:
            d = tl.shift(t)
            if d is None:
                continue
            # The shift is chosen from the picture; content that started with the scene (an animated object) can sit at the
            # phase of a neighbouring window of the same clean stretch: any of those shifts may match.
            best = None
            cands = tl.shifts_near(t, d)
            if any(t + dd > nseries.t[-1] or t + dd < nseries.t[0] for dd in cands):
                continue  # the native run has no samples at one of the phases (a scene ends)
            for dd in cands:
                w_ = win(t + dd, after_pad(t) or tl.after_load(t))
                bounds = nseries.bounds(t + dd, w_)
                if bounds is None:
                    continue
                lo, hi = bounds
                out = [max(0.0, lo[x] - vec[x], vec[x] - hi[x]) for x in range(16)]
                r_ = max(out[x] for x in (0, 1, 2, 4, 5, 6, 8, 9, 10))
                t_ = max(out[x] for x in (12, 13, 14))
                score = r_ / tol_rot + t_ / tol_tr
                if best is None or score < best[0]:
                    best = (score, r_, t_)
            if best is None:
                continue
            count += 1
            rot, tr = best[1], best[2]
            if rot > worst_rot:
                worst_rot, worst_t = rot, t
            if tr > worst_tr:
                worst_tr, worst_t = tr, t if worst_rot <= tol_rot else worst_t
        if count:
            rows.append(dict(stream=f"i={k[0]} ra={k[1]}", samples=count, rot_dev=worst_rot, tr_dev=worst_tr, tol_rot=tol_rot,
                             tol_tr=tol_tr, native_step=step_tr, worst_t=worst_t,
                             **{"pass": worst_rot <= tol_rot and worst_tr <= tol_tr}))
    if not rows:
        return ["no matching matrix streams"], 0, rows
    failing = [r for r in rows if not r["pass"]]
    lines = [f"{len(rows) - len(failing)}/{len(rows)} streams inside the native range (+-1 native frame)"]
    for r in (sorted(failing, key=lambda r: -r["samples"])[:4] + sorted((r for r in rows if r["pass"]), key=lambda r: -r["samples"])[:2]):
        lines.append(f"  {r['stream']:>16} n={r['samples']:5d} outside the range by rotation {r['rot_dev']:.2e} (tol {r['tol_rot']:.1e}) "
                     f"translation {r['tr_dev']:.2e} (tol {r['tol_tr']:.1e}, native step {r['native_step']:.2e})"
                     f" {'ok' if r['pass'] else 'FAIL at t=%.3f' % (r['worst_t'] or 0)}")
    return lines, len(failing), rows


def check_frames(native, high, an, ah, tl, tol):
    nseen, hseen = set(), set()
    nf = []
    for f in native["F"]:
        if f["flip"] not in nseen:
            nseen.add(f["flip"])
            nf.append((f["gt"] - an, f["blocks"]))
    nf.sort(key=lambda x: x[0])
    times = [f[0] for f in nf]
    if len(nf) < 4:
        return ["no F lines (run with GA2_FRAMETRACE_FRAMES=1)"], 0, None
    bad, compared = [], 0
    for f in sorted(high["F"], key=lambda f: f["gt"]):
        if f["flip"] in hseen:
            continue
        hseen.add(f["flip"])
        t = f["gt"] - ah
        d = tl.shift(t)
        if d is None:
            continue
        te = t + d
        w_ = win(te, True)
        lo, hi = bisect.bisect_left(times, te - w_ - 1e-4), bisect.bisect_right(times, te + w_ + 1e-4)
        if hi - lo < 1:
            continue
        compared += 1
        near = [nf[i][1] for i in range(lo, hi)]
        mn = [min(c) for c in zip(*near)]
        mx = [max(c) for c in zip(*near)]
        # a block that changes fast natively (a snapping cursor, a flash) can differ by half its swing from the
        # native frame the sample is paired with: the range is widened by half the swing
        out = [max(0.0, a - 0.5 * (b_ - a) - h, h - b_ - 0.5 * (b_ - a)) for h, a, b_ in zip(f["blocks"], mn, mx)]
        if max(out) > tol:
            bad.append((max(out), t, out))
    lines = []
    if bad:
        groups, cur = [], [bad[0]]
        for e in bad[1:]:
            if e[1] - cur[-1][1] > 0.2:
                groups.append(cur)
                cur = []
            cur.append(e)
        groups.append(cur)
        lines.append(f"{len(groups)} interval(s) where the picture is not what the native run shows within +-1 native frame of that game time:")
        for g in groups[:8]:
            hit = defaultdict(float)
            for e in g:
                for k, dd in enumerate(e[2]):
                    hit[k] += dd
            top = sorted(hit, key=lambda k: -hit[k])[:3]
            lines.append(f"   t={g[0][1]:.3f}..{g[-1][1]:.3f} ({len(g)} frames, worst {max(e[0] for e in g):.0f} levels outside, "
                         f"blocks col,row {[(k % 16, k // 16) for k in top]})")
    worst = max((e[0] for e in bad), default=0)
    lines.append(f"{compared} high frames in clean segments compared (16x14 luma grid); {len(bad)} have a block more than {tol:g} levels "
                 f"outside the native range" + (f", worst {worst:.0f}" if bad else ""))
    # Isolated single frames (one iteration of a render-side flicker) up to 0.2% of the frames compared are blips, not failures.
    singles = sum(1 for g in groups if len(g) == 1) if bad else 0
    fails = len(bad) - (singles if singles <= 0.002 * max(compared, 1) else 0)
    if bad and fails != len(bad):
        lines.append(f"{singles} isolated single-frame blip(s) (<= 0.2% of the frames) not counted")
    return lines, fails, dict(compared=compared, bad=len(bad), worst=worst, blips=singles)


def check_smooth(native, high, an, ah, tl, rate, share=0.10, burst_share=0.5):
    """Smoothness: during motion (the native picture changes from flip to flip) the high run must show a new picture about
    every iteration, not the same picture repeated. Per window: distinct pictures per game second (consecutive flips whose
    fingerprints differ) in both runs; windows where the native run animates every frame (>= 54 pictures/s) and the high
    run shows fewer than 80% of its iteration rate are 'repeating'."""
    def flips(run, anchor):
        seen, out = set(), []
        for f in sorted(run["F"], key=lambda f: f["gt"]):
            if f["flip"] not in seen:
                seen.add(f["flip"])
                out.append((f["gt"] - anchor, (f["blocks"], f["mean"])))
        return out

    nf, hf = flips(native, an), flips(high, ah)
    if len(nf) < 4 or len(hf) < 4:
        return ["no F lines"], 0, None

    def rate_in(fl, lo, hi):
        sel = [f for f in fl if lo <= f[0] < hi]
        if len(sel) < 2:
            return None
        changed = sum(1 for a, b in zip(sel, sel[1:]) if a[1] != b[1])
        return changed / max(hi - lo, 1e-9), (len(sel) - 1) / max(hi - lo, 1e-9)

    moving, repeating = [], []
    for a, b, d in tl.segments:
        if b - a < 0.5:
            continue
        rn = rate_in(nf, a + d, b + d)
        rh = rate_in(hf, a, b)
        if not rn or not rh or rn[0] < 20.0:
            continue
        moving.append((a, rn[0], rh[0], rh[1]))
        if rn[0] >= 54.0 and rh[0] < 0.8 * rate:
            repeating.append((a, rn[0], rh[0]))
    # Bursts: runs of at least 5 consecutive native flips that all differ (a cursor sliding, a fade, a pop-in): the high run
    # must show a new picture on at least 80% of its iterations over the same span.
    bursts, repeated = 0, []
    i = 0
    while i < len(nf) - 1:
        j = i
        while j + 1 < len(nf) and nf[j + 1][1] != nf[j][1]:
            j += 1
        if j - i >= 5:
            t0, t1 = nf[i][0], nf[j][0]
            d = tl.shift(t0)
            seg_h = [f for f in hf if t0 - (d or 0) <= f[0] <= t1 - (d or 0)] if d is not None else []
            if len(seg_h) >= 4:
                changed = sum(1 for a, b in zip(seg_h, seg_h[1:]) if a[1] != b[1])
                bursts += 1
                if changed < 0.8 * (len(seg_h) - 1) and (t1 - t0) * rate >= 8:
                    repeated.append((t0, t1, changed, len(seg_h) - 1))
        i = j + 1
    lines = []
    if not moving and not bursts:
        return ["no motion (the native picture changes < 20 times a second everywhere, no bursts)"], 0, dict(windows=0)
    if not moving:
        lines.append(f"{bursts} motion bursts (5+ consecutive changing native flips), {len(repeated)} repeat pictures in the high run")
        for t0, t1, c, n_ in repeated[:6]:
            lines.append(f"   t={t0:.2f}..{t1:.2f}: {c} new pictures in {n_} iterations")
        return lines, (1 if len(repeated) > burst_share * bursts else 0), dict(windows=0, bursts=bursts, repeating_bursts=len(repeated))
    mean_n = sum(m[1] for m in moving) / len(moving)
    mean_h = sum(m[2] for m in moving) / len(moving)
    full = [m for m in moving if m[1] >= 54.0]
    lines.append(f"{len(moving)} motion windows: distinct pictures/s native {mean_n:.1f}, high {mean_h:.1f} (iteration rate {rate:.0f}); "
                 f"{len(full)} animate every native frame, {len(repeating)} of those repeat pictures in the high run")
    for a, n_, h_ in repeating[:6]:
        lines.append(f"   t={a:.2f}: native {n_:.0f}/s, high {h_:.0f}/s")
    if bursts:
        lines.append(f"{bursts} motion bursts (5+ consecutive changing native flips), {len(repeated)} repeat pictures in the high run")
        for t0, t1, c, n_ in repeated[:6]:
            lines.append(f"   burst t={t0:.2f}..{t1:.2f}: {c} new pictures in {n_} iterations")
    bad = 1 if (full and len(repeating) > share * len(full)) or (bursts and len(repeated) > burst_share * bursts) else 0
    return lines, bad, dict(windows=len(moving), native=mean_n, high=mean_h, repeating=len(repeating), full=len(full), bursts=bursts, repeating_bursts=len(repeated))


def check_items(native, high, an, ah, tl, tol):
    def tracks(run, anchor):
        out = defaultdict(list)
        for t in run["T"]:
            out[t["item"]].append((t["gt"] - anchor, t["v"]))
        return out

    tn, th = tracks(native, an), tracks(high, ah)
    bad, compared = [], 0
    for item in sorted(set(tn) & set(th)):
        ns = tn[item]
        if len(ns) < 6:
            continue
        series = Series([(t, v) for t, v in ns])
        step = [0.0] * 6
        pairs = list(zip(series.t, series.v))
        for (t0, a), (t1, b) in zip(pairs, pairs[1:]):
            for k in range(6):
                step[k] = max(step[k], abs(b[k] - a[k]))
        worst, at = 0.0, None
        for t, v in th[item]:
            d = tl.shift(t)
            if d is None:
                continue
            bounds = series.bounds(t + d, win(t + d, after_pad(t)))
            if bounds is None:
                continue
            lo, hi = bounds
            for k in range(6):
                if step[k] > 0:
                    dev = max(0.0, lo[k] - v[k], v[k] - hi[k]) / step[k]
                    if dev > worst:
                        worst, at = dev, (t, k, v[k], lo[k], hi[k])
        compared += 1
        if worst > tol:
            bad.append((item, worst, at))
    lines = [f"{compared} 2D layer items compared; {len(bad)} leave the native range (+-1 native frame) by more than {tol:g} of their "
             f"largest native per-frame step"]
    for item, worst, at in sorted(bad, key=lambda x: -x[1])[:10]:
        lines.append(f"   item {item}: {worst:.2f} steps outside at t={at[0]:.3f} (field {at[1]}: high {at[2]:.5g}, native range {at[3]:.5g}..{at[4]:.5g})")
    return lines, len(bad), dict(compared=compared, bad=[(i, w) for i, w, _ in bad])


def check_probes(native, high, an, ah, tl):
    def series(run, anchor):
        out = defaultdict(list)
        for p in run["P"]:
            v = p.get("v", "null")
            if v != "null":
                out[(p["a"], p["h"])].append((p["gt"] - anchor, [int(x, 16) for x in v.split(",")]))
        return out

    def as_float(u):
        return struct.unpack("<f", struct.pack("<I", u))[0]

    sn, sh = series(native, an), series(high, ah)
    shown = []
    for key in sorted(set(sn) & set(sh)):
        ser = Series([(t, [float(x) for x in v]) for t, v in sn[key]])
        diffs = defaultdict(lambda: [0, 0.0, None])
        for t, v in sh[key]:
            d = tl.shift(t)
            if d is None:
                continue
            j = ser.coincident(t + d)
            if j is None:
                continue
            ref = [int(x) for x in ser.v[j]]
            for k, (a, b) in enumerate(zip(v, ref)):
                if a != b:
                    fa, fb = as_float(a), as_float(b)
                    dev = abs(fa - fb) if all(abs(x) < 1e9 and x == x for x in (fa, fb)) else float(abs(a - b))
                    st = diffs[k]
                    st[0] += 1
                    if dev >= st[1]:
                        st[1], st[2] = dev, (t, a, b, fa, fb)
        for k, (count, worst, at) in sorted(diffs.items()):
            if worst > 1e-3:
                shown.append((key, k, count, worst, at))
    lines = [f"{len(set(sn) & set(sh))} probed locations; {len(shown)} words differ from the native run at equal game time"]
    for key, k, count, worst, at in shown[:12]:
        lines.append(f"   {key[0]} hop {key[1]} word {k}: {count} samples differ, worst {worst:.4g} at t={at[0]:.3f} "
                     f"(high {at[1]:#x}/{at[3]:.6g}, native {at[2]:#x}/{at[4]:.6g})")
    return lines, len(shown), dict(differing=len(shown))


def check_rng(native, high, an, ah, tl, ratio_tol=1.35):
    """The game's RNG (G lines): draws per game second, by caller, over the clean
    windows. Random effects that roll once per iteration draw N/60 times as often."""
    span = sum(b - a for a, b, _ in tl.segments)

    def collect(run, anchor):
        per = defaultdict(int)
        for g in run["G"]:
            if tl.shift(g["gt"] - anchor) is None:
                continue
            for part in g["callers"].split(","):
                ra, _, c = part.partition(":")
                per[ra] += int(c)
        return per

    pn, ph = collect(native, an), collect(high, ah)
    if span <= 0 or not (pn or ph):
        return ["no RNG draws logged (needs the trace hooks)"], 0, None
    bad = []
    for ra in sorted(set(pn) | set(ph), key=lambda r: -(pn.get(r, 0) + ph.get(r, 0))):
        rn_, rh = pn.get(ra, 0) / span, ph.get(ra, 0) / span
        if max(rn_, rh) < 0.5:
            continue
        ratio = rh / rn_ if rn_ > 0 else float("inf")
        if ratio > ratio_tol:
            bad.append((ra, rn_, rh, ratio))
    totaln, totalh = sum(pn.values()) / span, sum(ph.values()) / span
    lines = [f"{totaln:.1f} draws per game second natively, {totalh:.1f} at the high rate; "
             f"{len(bad)} caller(s) draw more than {ratio_tol:g}x as often per game second"]
    for ra, rn_, rh, ratio in bad[:8]:
        lines.append(f"   caller {ra}: {rn_:.1f}/s native, {rh:.1f}/s high (x{ratio:.2f}): rolls once per iteration")
    return lines, len(bad), dict(native=totaln, high=totalh, bad=[b_[0] for b_ in bad])


def glyph_times(run, anchor):
    """(message, count, time): when each character of each message was first revealed (a message restarts when the count drops)."""
    out, last, msg = [], 0, 0
    for u in run["U"]:
        t = u["gt"] - anchor
        gc = u.get("gc", 0)
        if gc < last:
            msg += 1
            last = 0
        if gc > last:
            for k in range(last + 1, gc + 1):
                out.append((msg, k, t))
        last = gc
    return out

def rare_rpc(native, high, an, ah, tl, rpc_max):
    def keyed(run, anchor):
        groups = defaultdict(list)
        for e in run["R"]:
            groups[(e.get("sid"), e.get("fn"))].append((e["gt"] - anchor, e.get("words")))
        return groups

    gn, gh = keyed(native, an), keyed(high, ah)
    rows = []
    for tag in sorted(set(gn) & set(gh)):
        if len(gn[tag]) > rpc_max:
            continue
        for (tn, wn), (th, wh) in zip(gn[tag], gh[tag]):
            d = tl.shift(th)
            if d is None:
                continue
            rows.append((tag, tn, th + d, th + d - tn))
    return rows


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("native")
    ap.add_argument("high")
    ap.add_argument("--anchor", default="glyph",
                    help="glyph | kon[:n] | cd[:n] | lsn:<sector> | pad[:n] | scene[:n] | probe:<hex> | gt:<s> | view (default glyph); "
                         "give two comma-separated anchors to use a different one for each run (native,high)")
    ap.add_argument("--align", type=float, default=0.3, help="per-segment alignment search, +-seconds (0 = off)")
    ap.add_argument("--duration", type=float, default=0.0, help="compare this many seconds after the anchor (default: all common)")
    ap.add_argument("--skip", type=float, default=0.0, help="ignore the first seconds after the anchor")
    ap.add_argument("--json", help="write the results here")
    ap.add_argument("--frame", type=float, default=NATIVE_FRAME, help="event tolerance (one native frame)")
    ap.add_argument("--item-tol", type=float, default=0.75, help="2D item tolerance outside the native range, in native per-frame steps")
    ap.add_argument("--frame-tol", type=float, default=40.0, help="frame fingerprint tolerance, luma levels")
    ap.add_argument("--sid", type=int, default=None, help="compare only the times both runs are in this scene (U lines' sid: 1 boot, 9 title/load, 2 adventure, 3 battle)")
    ap.add_argument("--no-smooth", action="store_true", help="skip the smoothness check (a run that spends time at the native rate on purpose)")
    ap.add_argument("--rpc-max", type=int, default=400, help="grade IOP request kinds that occur at most this often in the native run")
    args = ap.parse_args()

    native, high = parse(args.native), parse(args.high)
    for name, run in (("native", native), ("high", high)):
        if not run["U"]:
            raise SystemExit(f"{name}: no U lines")
    specs = args.anchor.split(",")
    an, ah = find_anchor(native, specs[0]), find_anchor(high, specs[-1])
    t_hi = min(native["U"][-1]["gt"] - an, high["U"][-1]["gt"] - ah) - 1.0
    if args.duration:
        t_hi = min(t_hi, args.duration)
    t_lo = args.skip
    print(f"anchor {args.anchor}: native gt={an:.4f}  high gt={ah:.4f}; window {t_lo:.2f}..{t_hi:.2f} s after the anchor")

    for name, run, anchor in (("native", native, an), ("high", high, ah)):
        us = [u for u in run["U"] if t_lo <= u["gt"] - anchor <= t_hi]
        if len(us) > 1:
            span = us[-1]["gt"] - us[0]["gt"]
            wall = us[-1]["wall"] - us[0]["wall"]
            frs = defaultdict(int)
            for u in us:
                frs[u["fr"]] += 1
            dts = sorted(u["dt"] for u in us)
            print(f"  {name}: {len(us)} iterations, {(len(us) - 1) / span:.2f} per game second, game speed {span / wall * 100:.0f}% of wall, "
                  f"dt median {dts[len(dts) // 2] * 1000:.3f} ms, frames/iteration {dict(sorted(frs.items()))}")

    _PADS[:] = sorted(e["gt"] - ah for e in high["E"] if e.get("kind") == "pad")
    _STEPS[0][:] = [u["gt"] - an for u in native["U"]]
    _STEPS[1][:] = [u["dt"] for u in native["U"]]
    tl = Timeline(native, high, an, ah, t_lo, t_hi, args.align, sid=args.sid)
    print("  " + tl.describe())
    far = [(s[2], s[0], s[1]) for s in tl.segments if abs(s[2]) > 2 * NATIVE_FRAME]
    if far:
        print(f"  {len(far)} segment(s) aligned more than two native frames away from the anchor (loads finish in IOP time): "
              + ", ".join(f"{a:.1f}..{b:.1f} s {d * 1000:+.0f} ms" for d, a, b in far[:5]))
    ok = True
    report = dict(anchor=args.anchor, anchor_native=an, anchor_high=ah, window=[t_lo, t_hi],
                  segments=[dict(t0=a, t1=b, shift=d) for a, b, d in tl.segments])

    def section(label, lines, failures, data):
        nonlocal ok
        print(f"[{label}] {'ok' if not failures else 'FAIL'}")
        for line in lines:
            print("  " + line)
        report[label] = data
        ok = ok and failures == 0

    for label, key in (("camera", "M"), ("objects", "W")):
        lines, bad, rows = check_matrices(native, high, key, an, ah, tl)
        section(label, lines, bad, rows)
    lines, bad, data = check_frames(native, high, an, ah, tl, args.frame_tol)
    if data is not None:
        section("frames", lines, bad, data)
    else:
        print("[frames] " + lines[0])
    hus = [u for u in high["U"] if t_lo <= u["gt"] - ah <= t_hi]
    if len(hus) > 1 and not args.no_smooth:
        iter_rate = (len(hus) - 1) / (hus[-1]["gt"] - hus[0]["gt"])
        lines, bad, data = check_smooth(native, high, an, ah, tl, iter_rate)
        if data is not None:
            section("smooth", lines, bad, data)
    if native["T"] and high["T"]:
        lines, bad, data = check_items(native, high, an, ah, tl, args.item_tol)
        section("items", lines, bad, data)
    if native["P"] and high["P"]:
        lines, bad, data = check_probes(native, high, an, ah, tl)
        section("probes", lines, bad, data)

    if native["G"] and high["G"]:
        lines, bad, data = check_rng(native, high, an, ah, tl)
        section("rng", lines, bad, data)

    # Text, pad presses, rare IOP requests, IOP-timed events.
    # Text: the rate of the reveal (each character's time after its message's first character) must match
    # within one native frame; when a message starts (input -> window -> first character is a pipeline of
    # iterations, so a higher rate has less latency) may differ by up to two native frames.
    def messages(run, anchor):
        groups = defaultdict(list)
        utimes = [u["gt"] - anchor for u in run["U"]]
        for msg, k, t in glyph_times(run, anchor):
            if t_lo <= t <= t_hi:
                if args.sid is not None:
                    i = min(max(bisect.bisect_left(utimes, t), 0), len(utimes) - 1)
                    if run["U"][i].get("sid") != args.sid:
                        continue
                groups[msg].append((k, t))
        return [v for _, v in sorted(groups.items())]

    mn_, mh_ = messages(native, an), messages(high, ah)
    pairs = [(a, b) for a, b in zip(mn_, mh_) if len(a) == len(b) or True]
    if pairs:
        # A view that renders at 30 fps (the game's minFrames 2) reveals on its own 2-frame steps: one native
        # iteration of that view is the tolerance there.
        ntimes = [u["gt"] - an for u in native["U"]]
        ndts = [u["dt"] for u in native["U"]]

        def native_step(t):
            i = min(max(bisect.bisect_left(ntimes, t), 0), len(ndts) - 1)
            return max(args.frame, ndts[i])

        # In a 30 fps view the character that appears at an iteration boundary can land one native iteration either way
        # (the input that skips or advances the text is sampled on that grid): two iterations there, one at 60 fps.
        def rate_step(t):
            step = native_step(t)
            return step * (3.1 if step > 1.5 * args.frame else 1.0)

        stalls = sorted(u["gt"] - an for u in native["U"] if u.get("fr", 1) >= 10 * max(u.get("mn", 1), 1) or u.get("el", 0.0) > 0.9)
        rate_offs, start_offs, rate_tol, start_tol = [], [], [], []
        for a, b in pairs[:-1] if len(pairs) > 1 else pairs:
            # a message that starts soon after a load stall starts when the IOP finishes (host time): not graded
            j = bisect.bisect_right(stalls, a[0][1]) - 1
            if j >= 0 and a[0][1] - stalls[j] < 12.0:
                rate_only = True
            else:
                rate_only = False
                start_offs.append(b[0][1] - a[0][1])
                start_tol.append(2.05 * native_step(a[0][1]))
            for (k1, t1), (k2, t2) in zip(a, b):
                if k1 == k2:
                    rate_offs.append((t2 - b[0][1]) - (t1 - a[0][1]))
                    rate_tol.append(rate_step(t1) * 1.0001)
        late = [o for o, tol in zip(rate_offs, rate_tol) if abs(o) > tol * 1.02]
        # The pipeline from the pad press to the first character is a few iterations deep: at a higher rate it is shorter, so
        # the high run may start earlier (up to about 100 ms: six native frames) but not later than two native frames.
        early = [o for o, tol in zip(start_offs, start_tol) if o > tol or o < -max(3.0 * tol, 0.1)]
        count_gap = abs(sum(len(a) for a in mn_) - sum(len(b) for b in mh_)) > 3
        print(f"[text] {'ok' if not (late or early or count_gap) else 'FAIL'}\n  {len(pairs)} messages, {len(rate_offs)} characters: reveal rate max offset "
              f"{max(rate_offs, key=abs, default=0.0) * 1000:+.2f} ms ({len(late)} beyond one native frame); message start offset "
              f"{min(start_offs, default=0.0) * 1000:+.1f}..{max(start_offs, default=0.0) * 1000:+.1f} ms ({len(early)} beyond two native frames)"
              + ("; revealed character counts differ" if count_gap else ""))
        report["text"] = dict(messages=len(pairs), characters=len(rate_offs), rate_late=len(late), start_late=len(early))
        ok = ok and not (late or early or count_gap)
    else:
        print("[text] no message characters in both runs")
    n = len(pairs) if pairs else 0
    gn = [(a[-1][1], a[-1][0]) for a, _ in pairs] if pairs else []
    gh = [(b[-1][1], b[-1][0]) for _, b in pairs] if pairs else []
    pn = [e["gt"] - an for e in native["E"] if e.get("kind") == "pad" and t_lo <= e["gt"] - an <= t_hi]
    ph = [e["gt"] - ah for e in high["E"] if e.get("kind") == "pad" and t_lo <= e["gt"] - ah <= t_hi]
    if pn and ph:
        offs = [h - n_ for n_, h in zip(pn, ph)]
        print(f"[pad] scripted presses: {len(offs)} matched; max offset {max(offs, key=abs) * 1000:+.2f} ms")
    rows = rare_rpc(native, high, an, ah, tl, args.rpc_max)
    if rows:
        late = [r for r in rows if abs(r[3]) > args.frame * 1.02]
        worst = max(rows, key=lambda r: abs(r[3]))
        print(f"[rpc] {'ok' if not late else 'FAIL'}\n  {len(rows)} rare IOP requests matched (kinds with <= {args.rpc_max} calls); "
              f"max offset {worst[3] * 1000:+.2f} ms ({worst[0][0]}/{worst[0][1]} at t={worst[1]:.3f}); {len(late)} beyond one native frame")
        for r in late[:5]:
            print(f"    LATE sid={r[0][0]} fn={r[0][1]} t_native={r[1]:.4f} t_high={r[2]:.4f} offset {r[3] * 1000:+.2f} ms")
        report["rpc"] = dict(matched=len(rows), late=len(late))
        ok = ok and not late
    kon = defaultdict(lambda: ([], []))
    for i, (run, anchor) in enumerate(((native, an), (high, ah))):
        for e in run["E"]:
            if e.get("kind") == "kon" and t_lo <= e["gt"] - anchor <= t_hi:
                kon[(e.get("core"), e.get("half"), e.get("mask"))][i].append(e["gt"] - anchor)
    offs = [h - n_ for a, b in kon.values() for n_, h in zip(a, b)]
    if offs:
        late = [o for o in offs if abs(o) > args.frame * 1.0001]
        print(f"[info] IOP-timed sound key-ons: {len(offs)} matched; max offset {max(offs, key=abs) * 1000:+.2f} ms; "
              f"{len(late)} beyond one native frame (they follow EE time, not game time: not graded)")

    # Length: the end of the last complete message both runs reached.
    if len(gn) > 1:
        (tn_, kn_), (th_, _) = gn[-2], gh[-2]
        print(f"[length] last complete message ended at {tn_:.4f} s native vs {th_:.4f} s high: {abs(th_ - tn_) * 1000:.2f} ms apart")
        ok = ok and abs(th_ - tn_) <= 2.05 * args.frame
    report["pass"] = ok
    print("RESULT:", "PASS" if ok else "FAIL")
    if args.json:
        with open(args.json, "w") as f:
            json.dump(report, f, indent=1)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
