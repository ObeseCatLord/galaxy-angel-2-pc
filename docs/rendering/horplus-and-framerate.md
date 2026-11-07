# Hor+ widescreen and high frame rate: reverse-engineering notes

Status 2026-10-21. Hor+ notes (sections 1-2) are research; the **high frame
rate is implemented** (`GA2_FPS`, section 5), with a timing oracle
(`tools/framerate_oracle.py`) and the audit of what does not scale with `dt`.
Evidence (decompiler output, RAM dumps, screenshots, gdb scripts, tools) is in
`<private>/research/rendering/`. Addresses are guest (EE) addresses; the
recompiled host function for guest address `A` is `FUN_<A>_0x<A>`.

## Summary

- All three games use the same engine (the "Axl" library plus a common
  `CFramework`/`CCamera` layer). Every function involved was found on all
  five discs by masked byte signature; the per-disc table is at the end.
- **Hor+:** one float controls the horizontal field of view of every 3D
  camera: the display *pixel aspect* `PAR` at `display_info + 0x30`
  (Zettai `0x7f96c0`, value 0.94). Each `CCamera` computes
  `ax = (1/PAR) * (W/W2)` = 1.0638 in its constructor and uses it for the
  projection matrix, the clip-to-screen matrix and the EE frustum planes.
  Setting `PAR = 0.94 * k` gives hor+ with factor k (vertical FOV unchanged,
  horizontal view widened by k, side objects not culled). **Verified** on the
  Zettai ship map and Eigou disc 2's narration (k = 1.75, via gdb, no runtime
  change): `<private>/research/rendering/zmap-horplus-compare.png`.
- Output stays a 640-wide GS frame, so the image is anamorphic (display it
  stretched by k). 3D goes through VU1 XGKICK (PATH1); 2D sprites go through
  VIF1 DIRECT (PATH2) using one small Axl 2D API (screen-position setters
  listed in [horplus-2d](horplus-2d.md)), so 2D can be
  squeezed by 1/k at that API, except full-width passes and HUD markers that
  are positioned from the 3D projection (they are already in hor+ space).
- **Frame timing:** one `Update(dt)` + one `Render` per loop iteration, paced
  by a VSync semaphore. Measured: 60 fps (exactly 1 vsync per iteration) in
  title, menus, the ship map and Eigou 2's narration; some battle camera views
  run at 30 fps by design. `dt = min(frames, maxFrames) * (1/59.94 s)`; most
  scenes set `maxFrames = 1`, so in practice it is a fixed 1/59.94 s step per
  rendered frame (slowdown = slow motion). `dt` is passed in seconds to all
  subsystems, so a "two ticks per vsync" high-frame-rate mode is plausible,
  but frame-counted logic has not been ruled out and the VU1 cost doubles.

## 1. Projection

### Where it is built

`CCamera` (RTTI `7CCamera`, 0xd0 bytes, vtable `0x2d3f98` on Zettai):

| Offset | Meaning |
| --- | --- |
| 0x00-0x1c | position, rotation (radians) |
| 0x20-0x5f, 0x60-0x9f | camera matrix, view matrix |
| 0xa0 | vertical FOV (radians; default 60 deg, scene files set `FOVV` in degrees) |
| 0xa4 / 0xa8 | zmin 1.0 / zmax 8388606 (depth range) |
| 0xac / 0xb0 | near 1.0 / far 100000 |
| 0xb4 / 0xb8 | screen centre 2048 / 2048 |
| 0xbc | `scrz = (H2/2) / tan(fov/2)` with H2 = 448 (recomputed by set-FOV `0x118fc0`) |
| 0xc0 | `ax = (1/PAR) * (W/W2)` = 1/0.94 = 1.06383 (constructor only) |
| 0xc4 | `ay = H/H2` = 480/448 = 1.07143 (constructor only) |

`CCamera::ApplyProjection` (`0x118e78`) calls `SetClipExtents(1856, 1856)`
(`0x230390`: globals `Ex`, `Ey`), then the perspective builder `0x22ff20`
(sceVu0ViewScreenMatrix-style: `scrz, ax, ay, cx, cy, zmin, zmax, near, far`)
and stores the result with `0x230300` into three global matrices:

| Global (Zettai) | Matrix | x scale (FOV 80 deg example) |
| --- | --- | --- |
| `0x2cf020` VS | view -> screen | `scrz*ax` = 283.99 (y: `scrz*ay` = 286.02) |
| `0x2cf060` VC | view -> clip | `scrz/Ex` = 0.143832 (same for y) |
| `0x2cf0a0` CS | clip -> screen | `ax*Ex` = 1974.47 (y: `ay*Ey` = 1988.57), offset 2048 |
| `0x2cf1b0` | cull params | `(far-near)/(2Ex)`, `(far-near)/(2Ey)`, `(far+near)/2`, `(far-near)/2` |

So the clip volume is a guard band: `|scrz*x/z| <= 1856`, i.e. GS
2048 +/- 1974 horizontally, about +/-82 deg at FOV 80 against a visible
+/-320 pixels. The only other builder is the orthographic one (`0x230108`),
used by `0x11c0e0` (models drawn in screen space, with `ax = ay = 1`, extents
`W/2-1`, `H/2-1` = 1727, 1807). The framework constructor (`0x113490`) sets a
default 60 deg projection the same way.

### How it reaches VU1

The matrix-set dispatcher `0x22f498` (case 0, view matrix, called from
`0x22fc88(0, view)`) multiplies VS and VC by the view matrix on the EE (VU0
macro code) and queues one constant block for VU1 (`0x7fb830` header,
`0x2250e0` adds it to the VIF1 list); world matrices (case 1) are queued per
object. VU1 data memory, as seen in every capture (`GA2_VU1CAP`), all discs:

| VU1 qw | Content |
| --- | --- |
| 0-3 | VS x View (per frame) |
| 4-7 | VC x View (per frame) |
| 8-11 | CS (per frame) |
| 12-15 | world matrix (per object) |
| 24-25 | constants; qw25 = cull params above |

All discs share one VU1 microprogram (entry 5616 = object path). The
microcode itself was not read; the roles of qw4-11 (clip test, then
clip-to-screen for clipped vertices) follow from the values: VS = CS x VC
exactly (283.99 = 0.143832 x 1974.47).

### EE frustum culling

`FrustumBuild` (`0x1045d8`, args `W/ax, H/ay, scrz, near, far, out, view`)
builds 6 world-space planes; `SphereInFrustum` (`0x104728`) tests bounding
spheres. Callers (battle scene `0x1616c8`/`0x161890` with literal 640/480,
`0x1f4a38` with `sys+0xb4/0xb8` = 640/480) all read `ax` from the camera
(`+0xc0`). The Axl library itself does not frustum-cull on the EE (VC and the
cull params are only read by the VU1 upload and the save/restore in
`0x11c0e0`).

Screen-space helpers that project 3D points for 2D markers/picking use
`scene+0x4c0 = VS x View` and map to a 640x480 screen with
`x = X/W - 2048 + 320`, `y = Y/W - 2048 + 240`, rejecting points outside
`[0,639] x [0,479]` (Zettai `0x162398`, `0x1655c8`, `0x18f878`, `0x190680`,
`0x1889f0`, `0x1944e0`, ...).

### Minimal hor+ change

Hor+ with factor k (1.3333 for 16:9, 1.75 for 21:9) = divide `ax` by k for
perspective cameras. Effects:

- VS and CS x scale shrink by k: the same vertical FOV, k times the
  horizontal view in the 640-wide frame.
- VC (clip) is unchanged: the guard-band clip volume in view space does not
  move; in screen pixels it shrinks to `1974/k` (still far beyond +/-320, and
  never beyond GS range). At k = 1.75 and FOV 80 the visible half-angle is
  63 deg against the 82 deg clip volume.
- EE frustum planes use `640/ax`, so they widen by k automatically.
- 3D-to-2D marker projection uses the new VS, so markers stay on their
  objects in the anamorphic frame.
- Orthographic (2D-space) model draws use `ax = 1` and are not affected.

The single value: `PAR` (`display_info + 0x30`), read only by the camera
constructor and the framework constructor. Display init (`0x227ff0`, called
from `0x2283f8` by Axl init `0x237310` and by a second re-init path
`0x237500`) rewrites it, so patch after every display init.

Proposed runtime hooks (per disc, table below):

1. Post-hook display init: `PAR *= k`. Every camera constructed afterwards is
   hor+. A change of k applies from the next scene (cameras are built per
   scene: callers of the constructor `0x118b58` are scene setup functions).
2. For a live change, additionally post-hook the camera constructor to record
   camera addresses, or override `ApplyProjection` (`0x118e78`) to pass
   `ax/k`; the frustum callers read `cam+0xc0` directly, so patching the
   field is the consistent option.

### Verification

- RAM (`GA2_WATCHDOG_DUMP`): Eigou 2 narration, display info at `0x518650` =
  `{flags 0x1c, 0, 640, 480, 0x31, 2, 640, 448, ..., PAR 0.94}`, `Ex = Ey =
  1856`, VS x 283.99 (FOV 80); Zettai ship map: same PAR, VS x 412.74
  (FOV 60).
- gdb runs (`research/rendering/gdb-horplus-*.gdb`, breakpoint on the host
  function of the camera constructor writes `PAR = 1.645`):
  Zettai map VS x 235.85 = 412.74/1.75, CS x 1128.3 = 1974.47/1.75; Eigou 2
  VS x 162.28 = 283.99/1.75. Screenshots: the map shows all room blocks
  (sides that were cut off at 4:3 are drawn, no culling holes), the vertical
  extent is unchanged; stretched to 21:9 the geometry has correct
  proportions. The 2D overlays (destination preview, block list, circuit
  background) are unchanged and therefore stretched in a 21:9 display.
- Not verified: battles (Mugen tutorial, Zettai SLG views), the
  orthographic model path, 16:9 specifically (same mechanism).

## 2. 2D versus 3D (GIF paths)

- PATH1 (VU1 XGKICK): all perspective 3D (clumps/atomics, AxlPrim immediate
  geometry `0x230d70` such as beams and lines, which use the current
  matrices) and the orthographic model draws.
- PATH2 (VIF1 DIRECT): 2D sprites, texture uploads (IMAGE,
  `AxlTexMgrAddUploadImagePacket` `0x22bcc8`) and full-screen passes. Sampled
  on the title/load screens with gdb (`gdb-gifpaths-zettai.gdb`): REGLIST
  sprites `PRIM, RGBAQ, UV, XYZF2, UV, XYZF2` with PRIM 0x156 (textured
  alpha-blended sprite, UV coordinates) and full-height 64-pixel strips
  (0..64 x 0..480, ...). The sprite template is built by `0x231938`
  (`0x2cf4a0`: tag, regs 0x434310, PRIM 0x156) and filled by the Axl 2D API:
  texel rect `0x2331d8(u, v, w, h)` / `0x233220` (float), colour `0x233280`,
  **screen rect** `0x233308(x0, y0, x1, y1, z)` (float) / `0x2332a0` (int),
  emit `0x233380` (dozens of game callers: dialogue, menus, map UI, battle
  HUD). Correction 2026-10-14: an earlier version of this note had the texel
  and screen setters swapped; see [horplus-2d](horplus-2d.md) for the full
  list of screen-space entry points.
- PATH3 (GIF DMA): the only code touching the GIF channel is `0x2453b0`
  (starts a chain) and `0x247e68` (VIF1/GIF channel housekeeping called
  around flips and display init); no PATH3 packet was seen in the sampled
  frames.
- 2D works in a 640x480 virtual space: the setters add the origin
  `(2048-320, 2048-240)*16` and the draw environment uses XYOFFSET
  (1728, 1808), the same as 3D.

So the split is clean enough to squeeze 2D at the EE API: wrap the
screen-position setters (not the texel setters) to map
`x' = 320 + (x - 320)/k` for every x coordinate, with exceptions:

- rects covering the full width (fades, flashes, letterbox fills, background
  plates): leave them or widen them, otherwise the sides of the 3D image are
  not covered;
- HUD elements positioned from the 3D projection (helpers listed in section
  1): keep the position, squeeze only the size;
- full-screen passes built from 64-pixel strips and framebuffer copies (they
  do not go through the sprite API, but must stay unsqueezed if a GS-level
  approach is used instead).

A GS-level squeeze (transform XYZ of PATH2 primitives) cannot tell markers or
full-screen strips from UI and is not recommended. 2D-only scenes (ADV
dialogue, menus) simply become pillarboxed 4:3 when 3D is not active.

## 3. Frame timing

### Main loop

`main` (`0x100258`): `sys` = system singleton (Zettai `0x2d3be4`, ctor
`0x112b28`), app = `0x2d3be8`; `loop { Update(sys) 0x113210; RenderFlip(sys)
0x112dc0 }`.

- `Update`: reads the EE timer (`0x223790`, timer struct `0x7f8db8`, count +
  overflows) into `sys+0x10`, polls pads, then
  `app->Update(dt)` with `dt = min(sys+0x18, sys+0x1c) * sys+0x08`;
  `sys+0x08 = 1/refresh` (refresh float `0x2ceef0` = 59.94 for NTSC,
  `0x223988`). The app update (`0x13a1c0` -> scene update, then
  `0x1139f0`) passes `dt` in seconds to every subsystem (fades are specified
  in seconds, e.g. 0.2 s).
- `RenderFlip`: starts the packet lists, `app->Render()`, closes and swaps
  the draw buffer (`0x224008`, `0x228760`), waits for the DMA to finish,
  measures the elapsed time since `Update` started in vsyncs
  (`0x223218`: ticks * refresh * 100 / clock), stores
  `sys+0x18 = max(int(elapsed) + 1, minFrames sys+0x20)`, then
  `WaitVSync` (`0x237748`: `WaitSema`, signalled by the VBLANK INTC handler
  `0x2377f8` once `vsyncCount (0x7fe0b8) >= last + interval (0x2cf774)`,
  interval = minFrames), sets the display registers (`0x2285e0`, flip) and
  kicks the next list (`0x224108`).
- `minFrames` (`0x1133a0`): 1 by default. The battle camera-view manager
  (`0x161890`) sets it from a table indexed by view mode: `{1, 2, 1, 2, 2, 2}`
  (identical on all discs), i.e. view modes 1, 3, 4, 5 render at 30 fps.
  Which camera class each mode is was not determined.
- `maxFrames` (`0x113388`): 4 in the constructor, but scenes push 1 on enter
  and restore it on exit (12 call sites on Zettai), giving a fixed step.

Measured with a gdb breakpoint on `RenderFlip` (`gdb_frametiming.py`,
`zmap-frametiming.txt`, `e2-frametiming.txt`): title, load screen, ship map
and Eigou 2 narration: every iteration advanced the guest vsync counter by
exactly 1, `frames = 1`, `maxFrames = 1`, `minFrames = 1`,
`sys+0x08 = 0.016683`. Long loads show one iteration of 16-491 vsyncs (still
`dt` = 1 frame because of the clamp). The emulated vsync slows with the host
(map ~39 iterations/s on Xvfb, still 1 vsync each), so the game slows down
instead of skipping.

### High frame rate options

The rendered state is produced by `Update`; `Render` only builds packets
from it. There is no separate interpolation state.

A. **Two ticks per vsync (implemented, section 5).** Per iteration:
`dt = 1/119.88`, measured frames counted in half-vsyncs, pace the loop at
119.88 Hz on the host, present every flip.
- Override `RenderFlip` (small, all its callees are known) with a host
  version that is identical except: elapsed time converted to 120 Hz units
  (do not change the global refresh float: `0x1a3250` derives a per-frame
  step from it and `0x206648` converts vsync counts to seconds with it), and
  the `WaitVSync` call replaced by a host wait for the next 1/119.88 s
  boundary (the real VBLANK, vsync counter, IOP, sound and movies keep
  59.94 Hz).
- Set `sys+0x08 = 1/119.88` (and restore it when the mode is off). With
  `maxFrames = 1` this is the whole time step.
- Map `minFrames`: native 2 (30 fps views) becomes 4 half-vsyncs to keep
  30 fps, or 2 for 60.
- Runtime: present on each guest flip (DISPFB write in `0x2285e0`) instead of
  at emulated vblank; the GS and VU1 work per second doubles. VU1-heavy 3D
  scenes do not reach 60 fps today (Eigou 2 narration ~41, ship map ~72%),
  so 120 fps there needs the block-level VU1 compiler first; 2D scenes are
  cheap.
- Risk: any logic that counts `Update` calls instead of using `dt` (text
  reveal, script `wait` counts, per-frame RNG/particle spawns, UI blink
  counters, `0x1a3250`-style per-frame steps) runs twice as fast. The 30 fps
  battle views (dt = 2 frames by design) show the battle code scales with
  `dt`; the ADV script/text code was not checked.
- Oracle (needed before shipping, AGENTS.md "keep native game timing until
  an oracle verifies"): log the view matrix (`0x2cefe0`) and a few object
  transforms per tick at 60 Hz and at 120 Hz over a deterministic sequence
  (Eigou 2 narration needs no input) and compare at equal game time; time
  text/fade/voice sequences against the native run.

B. **Logic at 60 Hz, interpolated extra frames.** Call `Render` a second time
between updates with the view matrix (`0x22fc88(0, view)`) interpolated
between the last two ticks. Smooths camera motion only (object animation
advances in `Update`), and `Render` must be shown to have no side effects
(UI animations, texture upload queues). More invasive than A for less gain.

Either way, keep the native mode as default and gate the feature per scene
until the oracle passes.

## 4. Per-disc addresses

| Item | zettai | mugen-1 | mugen-2 | eigou-1 | eigou-2 |
| --- | --- | --- | --- | --- | --- |
| CCamera ctor | 0x118b58 | 0x118cb0 | 0x118bd8 | 0x118cb0 | 0x118cb0 |
| CCamera apply projection | 0x118e78 | 0x118fd0 | 0x118ef8 | 0x118fd0 | 0x118fd0 |
| CCamera set FOV | 0x118fc0 | 0x119118 | 0x119040 | 0x119118 | 0x119118 |
| CFramework ctor (default proj) | 0x113490 | 0x113540 | 0x113510 | 0x113540 | 0x113540 |
| perspective builder | 0x22ff20 | 0x249980 | 0x2319d0 | 0x256680 | 0x24a9f0 |
| ortho builder | 0x230108 | 0x249b68 | 0x231bb8 | 0x256868 | 0x24abd8 |
| set clip extents Ex/Ey | 0x230390 | 0x249df0 | 0x231e40 | 0x256af0 | 0x24ae60 |
| store screen matrices | 0x230300 | 0x249d60 | 0x231db0 | 0x256a60 | 0x24add0 |
| matrix set dispatcher | 0x22f498 | 0x248ef0 | 0x230f48 | 0x255bf0 | 0x249f60 |
| display init (writes PAR) | 0x227ff0 | 0x241a48 | 0x229aa0 | 0x24e748 | 0x242ab8 |
| frustum planes build | 0x1045d8 | 0x104638 | 0x104660 | 0x104638 | 0x104638 |
| sphere vs frustum | 0x104728 | 0x104788 | 0x1047b0 | 0x104788 | 0x104788 |
| main-loop Update | 0x113210 | 0x1132c0 | 0x113290 | 0x1132c0 | 0x1132c0 |
| main-loop Render+flip | 0x112dc0 | 0x112e50 | 0x112e40 | 0x112e50 | 0x112e50 |
| WaitVSync (sema) | 0x237748 | 0x2511a8 | 0x2391f8 | 0x25dea8 | 0x252218 |
| VBLANK INTC handler | 0x2377f8 | 0x251258 | 0x2392a8 | 0x25df58 | 0x2522c8 |
| set min frames | 0x1133a0 | 0x113450 | 0x113420 | 0x113450 | 0x113450 |
| set max frames | 0x113388 | 0x113438 | 0x113408 | 0x113438 | 0x113438 |
| refresh-rate init | 0x223988 | 0x23d3e0 | 0x225438 | 0x24a0e0 | 0x23e450 |
| ticks -> vsyncs*100 | 0x223218 | 0x23cc70 | 0x224cc8 | 0x249970 | 0x23dce0 |
| 2D sprite texel rect (int, not screen) | 0x2331d8 | 0x24cc38 | 0x234c88 | 0x259938 | 0x24dca8 |
| 2D sprite texel rect (float, not screen) | 0x233220 | 0x24cc80 | 0x234cd0 | 0x259980 | 0x24dcf0 |
| 2D sprite screen rect (float) | 0x233308 | 0x24cd68 | 0x234db8 | 0x259a68 | 0x24ddd8 |
| 2D sprite screen rect (int) | 0x2332a0 | 0x24cd00 | 0x234d50 | 0x259a00 | 0x24dd70 |
| 2D sprite colour | 0x233280 | 0x24cce0 | 0x234d30 | 0x2599e0 | 0x24dd50 |
| 2D prim templates init | 0x231938 | 0x24b398 | 0x2333e8 | 0x258098 | 0x24c408 |
| Ex global (Ey = +4) | 0x2cf1a0 | 0x2eb7a0 | 0x2d0ca0 | 0x2f98a0 | 0x2ec860 |
| VS global (VC +0x40, CS +0x80) | 0x2cf020 | 0x2eb620 | 0x2d0b20 | 0x2f9720 | 0x2ec6e0 |
| display info | 0x7f9690 | 0x5171d0 | 0x7fb310 | 0x525a50 | 0x518650 |
| **PAR float (hor+)** | 0x7f96c0 | 0x517200 | 0x7fb340 | 0x525a80 | 0x518680 |
| refresh float | 0x2ceef0 | 0x2eb4f0 | 0x2d09f0 | 0x2f95f0 | 0x2ec5b0 |
| vsync counter | 0x7fe0b8 | 0x51bbf8 | 0x7ffd38 | 0x52a478 | 0x51d078 |
| sys singleton | 0x2d3be4 | 0x2f01e4 | 0x2d56e4 | 0x2fe2e4 | 0x2f12a4 |
| battle view interval table | 0x2dfe90 | 0x2fd0e0 | 0x2e1b30 | 0x30b6b8 | 0x2fe510 |

Matches are by masked instruction signature (immediates and call targets
masked) against the Zettai function; the camera field offsets
(`0xa0-0xc4`) were checked on every disc. PAR, VS and Ex were read from RAM
on Zettai and Eigou 2 only; the Mugen/Eigou 1 globals are derived from code.


## 5. High frame rate (GA2_FPS), implemented 2026-10-20, whole game 2026-10-21

`GA2_FPS=<61..240>` (unset or 60: native, nothing changes; ignored in VR, with
`GA2_GS=cpu`, and for values outside the range). One game iteration is one
`Update(dt)` + `Render` + flip; at N the game's frame is N*1000/1001 Hz
(`dt = 1001/(1000 N)`, so 120 is exactly twice the 59.94 Hz VBLANK, 144 and 240
work too). The real VBLANK (guest vsync counter, IOP, sound, movies) stays at
59.94 Hz. Option A of section 3, with the changes the evidence demanded:
`maxFrames`/`minFrames` are rescaled instead of just halving `dt` (the battle's
30 fps views run at N too), and code that counts iterations is held to the native
cadence (below). **Auto mode runs every scene kind at N**; the only exception is the
disc-change notice (scene 12), which stays at the native rate. `GA2_FPS` is the maximum:
the rate governor (below) lowers the rate when the host cannot keep the guest at real
time and raises it again, so the game is never slower than native.

### Design as implemented

Runtime (`<private>/worktrees/ga2-runtime`, branch `ga2`):

| File | What |
| --- | --- |
| `ps2xRuntime/include/runtime/ga2_framepace.h`, `src/lib/ga2_framepace.cpp` | `GA2_FPS` parsing, `waitTick`, flip counter, per-5 s `[ga2 fps]` report (ticks, guest flips, scan-outs, presented frames per second and the busiest threads' CPU), scripted pad, the `ga2_trace` oracle writer (`GA2_FRAMETRACE`) and `callChain` (guest stack walk for the RPC trace lines) |
| `Kernel/EeScheduler.cpp`, `include/runtime/ee_scheduler.h` | a second clock: `FrameTick` events at `fps*1000/1001` Hz, paced in EE cycles and host time exactly like the VBLANK chain (rebase after 50 ms late, never catch up); `waitFrameTick(interval)` blocks the guest thread on it; while the game waits on it the GS scans out at ticks and not at the VBLANK (`frameDriven()`, off for iterations that run native) |
| `gs/gs_pgs.cpp` | `waitFreshFrame`: the readback ring notifies the window thread; frame fingerprints (16x14 luma grid, mean colour) and PNG shots for the oracle |
| `ps2_runtime.cpp` | at GA2_FPS the window loop sleeps until a new frame arrives (no 60 Hz cap), vsync swap interval on (`GA2_VSYNC=0` to drop it) |
| `include/runtime/ga2_activity.h`, `vu/ps2_vu1_core.cpp` | `scene3d()` (the presentation's wide-frame decision, "VU1 ran within the last half second") counts guest vblanks now (30), not host time |
| `ps2_pad.cpp`, `Syscalls/RPC.cpp`, `ps2xIOP` (`iop_trace.h`, `iop_memory.cpp`, `iop_cdvd.cpp`) | scripted pad, IOP RPC (with the caller chain) / key-on / CD read trace lines, `GA2_FIXED_CLOCK` (frozen RTC: the games seed their RNG from the clock, so native runs become bit-identical) |

Game side (`native/compat/ga2_framerate.cpp`, all five discs; addresses in the
tables at the end of this section):

- `Update` (pre-hook): sets `sys+0x08 = 1/(N*1000/1001)` (the global refresh float is
  not touched), the scene decision (below), the native-tick decision for the holds,
  logs the oracle lines.
- `setMaxFrames` / `setMinFrames` are replaced: native maxFrames m is stored as
  ceil(m*N/60) frames (the same catch-up in time); minFrames is 1 (the battle's 30 fps
  views and the adventure scenes limited to 30 fps render at N; `GA2_FPS_BATTLE=30`
  keeps them at round(m*N/60), i.e. 30 fps); the VBLANK handler's interval keeps the
  native value for the loops that still wait on the real VBLANK.
- the EE-timer-to-vsyncs conversion called by `RenderFlip` is scaled by N/60
  (`frames` counts N Hz frames). It measures EE cycles, which are 3-5 % of a
  frame in every scene tested, so `frames` is 1 except in loads and movies.
- `WaitVSync` is replaced only for `RenderFlip`'s call (recognised by its return
  address): it sleeps on the frame clock. The other nine callers keep the VBLANK.
- the flip function notes a new frame; `MatSet`, the message glyph drawer, the 2D
  item draw and the RNG are observed for the trace.
- `app->Update` runs with `sys+0x08` at the native step (some code turns a frame
  count into seconds with it: `12 frames * sys+8`); `dt` was taken before.
- **gates** (`gate<>`, `DiscRate::gates`): a function that counts iterations is called
  once per native iteration of game time (1/59.94 s, or two of them in a 30 fps
  view) and skipped in between (returns what the last call returned). An
  `update(dt)` style gate (bit 0) is called with `dt` = the whole native frames since
  its last run and with `sys+0x08` native (frame-time integrators:
  `while (dt > 0) { v = v*k + c; dt -= sys+8 }`). The first call, and the first call
  after a gap, runs at once; a function polled in a loop inside one iteration runs
  after 256 skipped calls. Bit 1 (`fresh`): a widget whose scale words are 0,0 is
  updated at once (not drawn invisible until its next boundary). Bit 2 (`pad`): the
  skipped call also ages the pad state (previous := current, repeat outputs :=
  "no event"), so a press or an auto-repeat lasts one native frame and not one
  iteration. A call that blocks the guest thread (a synchronous RPC: the card manager's
  step does) is resumed by the scheduler and returns straight to its caller, so the code
  after the original call in the wrapper never runs: the remembered return value of a
  skipped call would be stale (it closed the load list when the rate changed during a card
  operation: found by the forced-change test under load). The wrapper marks a call in
  flight; a call that did not come back runs the function again at once and a stuck
  `sys+0x08` is put back. `GA2_FPS_GATES=<hex>[:u|:t][,...]` adds experiments (`:t` logs a
  call trace line `C` with the arguments and the return value), `GA2_FPS_NOGATE=<hex,...>`
  leaves single built-in gates out.
- **random-roll hold** (`rng<>`, `DiscRate::rngHold`, `effect`): rolls made by per-frame
  draw callbacks are recorded in the last native-boundary iteration and replayed
  in order in between (the generator is never drawn from), so the generator's
  sequence stays the native one; the distortion effect (`effectHold<>`) also repeats
  the state word it advances (the same state going in and coming out).
- scene policy: the scene manager's record (`**(sys+4)`, +8 is the scene id: 1 boot,
  2 adventure and ship map, 3 battle, 4/8/11 the sortie loads, 5 options, 9
  title/load/save, 12 disc change) decides per iteration whether it runs at N. **Auto:
  every scene at N except scene 12** (`GA2_FPS_NATIVE=<ids, map>|none` overrides the
  list, `GA2_FPS_SCENES=all` is `none`), switching at iteration boundaries (the frame
  count of the switching iteration is reset to 1). `GA2_FPS_NOFIX=1` binds none of the
  fixes above (the oracle's negative control).

Presentation: native mode is unchanged (SetTargetFPS(60), scan-out at the emulated
VBLANK). At GA2_FPS the window shows every guest flip (one scan-out per flip when the
display registers changed), paced by the display when vsync is on. VR keeps the
headset's pacing: GA2_FPS is refused there; to follow the headset rate N would be set
to its refresh rate and `ga2_stereo::flip` (the displayed-frame tag) would have to move
from the VBLANK to the tick, which was not attempted.

### Rate governor (GA2_FPS is the maximum)

A high rate must never make the game slower than native, so `GA2_FPS=N` only sets the
ceiling and the game side (`govStep` in `ga2_framerate.cpp`, at the top of every `Update`)
picks the rate the host sustains:

- Rungs: N, the multiples of 60 below it (the game's frame limits scale exactly there),
  60 (full-rate iterations: the native game's 30 fps views run at 60), then the native
  cadence (59.94 Hz iterations with the game's own limits: the code path of the scenes
  that run native). 120 gives 120, 60, native; 240 gives 240, 180, 120, 60, native.
- Measured every 0.25 s of wall time over whole iterations (windows with a load, a
  movie start or a native scene are dropped): guest game time per wall second, iterations
  per second, and the CPU time of the game thread (`CLOCK_THREAD_CPUTIME_ID`; the IOP
  and audio work it does while waiting counts, which is why "work from Update to the
  wait" was useless: 0.5 ms of 18).
- Drop (two windows, 0.5 s): guest speed below 97 % or iterations below 85 % of the rate,
  and the game thread above 85 % of a core. The new rung is the highest whose cost
  estimate `C * R' / (iterations per guest second)` (C = core seconds per guest second;
  every iteration costs what it did, which leans high) is at most 0.92, else native.
- Climb (3 s, at most two rungs): the optimistic estimate `C * (0.55 + 0.45 R'/its)`
  (45 % of the cost follows the iteration count, the rest follows guest time) is at most
  0.80 at full speed; after a climb that failed within 10 s the next one waits 10 s, 20 s,
  ... 60 s.
- A change happens at an iteration boundary: `ga2_framepace::setRate` retunes the frame
  clock (`EeScheduler::retuneFrameClock`: the tick origin moves, ticks keep counting),
  `sys+0x08`, the frame limits and the tick-to-vsync scale follow `rate()`, the frame count
  of the switching iteration is reset to 1, and the native rung is the same path as the
  scenes that run native. Game time stays exact (dt follows the rate): the oracle passes
  across changes (below).
- Every change is logged: `[ga2 fps] governor: 120 -> native fps (behind real time; guest
  speed 36%, 21 iterations/s, game thread 97% of a core, game time 243.8 s)` (and an
  `E kind=governor` line in the trace). `GA2_FPS_GOVERNOR=off` pins the maximum (the oracle
  runs do this), `GA2_FPS_GOVERNOR_TEST=<seconds>` steps through the rungs by game time
  instead (deterministic, for the oracle).

Zettai prebattle script (opening story, movie, sortie loads, cockpit tutorial), 16:9, on
this host (load 4-6 from other work), final build, native / GA2_FPS=120 / GA2_FPS=240 with
the governor, guest speed (vsync rate / 59.94) per phase: story 100 % / 99 % / 99 %, the
opening movie and the sortie loads (the game thread is at 97 % in native too) 73 % / 72 %
/ 72 %, after them (the cockpit tutorial's 3D views and the closing story) 100 % / 100 % /
100 %. Time at each rung over 437 s of game time: 120 run: 120 for 36 %, 60 for 21 %,
native 44 % (11 changes); 240 run: 240 for 10 %, 180 8 %, 120 17 %, 60 21 %, native 44 %
(14 changes). The movie and the cockpit's 3D camera views stay at native: at 60 they need
0.54 of a core per guest second at 30 iterations per second, the climb to 60 tried twice
and was dropped. In the story the effective rate was 120 (240) for stretches of 8-40 s
and 60 in between: the 3D narration needs 0.9-1.0 of a core at 120 when the other work on
this host runs. The first drop of each run is at the opening movie (game time 243.8 s:
guest speed 36 %, 21 iterations per second, game thread 99 %).

### Why the battle cost more than 2x native per guest second

Investigated with `[ga2 perf]` logs, a gprofng profile of the game thread (41 s window)
and per-call statistics of the texture replacement:

- Per iteration the cost does not grow with the rate: the same iteration costs the same
  (the cockpit's 3D views: 16 ms; the opening movie phase: 48 ms, with the game thread at
  97 % in native too, so the "battle" the first drop in the log shows is the opening
  movie and the sortie loads: `flips 20/s` at 80 ticks/s is the game's own catch-up cap,
  `maxFrames` 4 ticks = 33 ms of game time per iteration, native does the same at 20
  iterations per second and 67 % speed there).
- What grows is the number of iterations: the native cockpit views run at 30 iterations per
  second (`minFrames` 2); rendering them at N makes the host do N/30 times the 3D work
  (VU1 25 Mcycles/s -> 57 at 61 iterations per second, game thread 54 % -> 98 %, guest
  speed 52 %). The governor now keeps those views at native when 60 does not fit.
- Waste found: (1) `IopKernel::beginNextReady` scanned the whole IOP thread list twice per
  call and was 9 % of the game thread in IOP-heavy phases (the IOP emulation as a whole is
  29-38 % of the thread in the movie and the cockpit): one pass now, the second scan only when
  a delay expired (iop tests pass); (2) `ga2_texpacks` (button prompts) decodes and hashes
  every texture the GS creates: 23 % of the game thread over a whole run (640x480 and
  352x896 P8 textures in the story and load scenes, 110 ms per 5 s each) but only 2 % in the
  battle (the 64x480 frame strips, 200 per second): not changed, it is another feature's
  code and not rate dependent; a cache keyed by VRAM page versions would remove it.
- Not waste: the per-tick work (gates, holds) is O(1) per iteration; the trace and the
  oracle hooks are off in normal runs.

### Offenders found by the oracle, and their fixes

Readers of `sys+0x08` (the step; found with the decompiler's references to the sys
pointer `0x2d3be4` and a scan for the load at offset 8): five or six functions per disc,
all handled: the frame-time integrators `0x19fce0` (portrait pop-in, 7 states) and the
particle update `0x197208` (`b0 += dt; if (b0 >= sys+8) { step; b0 -= sys+8 }`, rolls
the generator), the palette colour cycle `0x182e08` (`time / sys+8` frames), the draw
callback `0x19f830` (random flicker) and constructors.

| Offender | Where (Zettai) | Evidence | Fix |
| --- | --- | --- | --- |
| frame-time integrator widgets (battle HUD portrait, 7-state `x = x*0.3 + c`) | `0x19fce0` (+3 discs by a short signature) | item scale values per iteration; pop-ins differed for 2-4 iterations after each message; after gating a freshly created widget was drawn at scale 0 for 1-3 iterations | update gate with whole native steps, `sys+8` native, `fresh` |
| particle update (space dust: one step per `sys+8`, 30 random rolls per step) | `0x197208`, ctor `0x197c40` | RNG draws x1.68 at 120, the battle diverged after 60 s | update gate |
| palette colour cycle (`frame index = time / sys+8`) | `0x182e08` | colours flickered twice as fast | update gate (Zettai, Mugen 2 only: the others do not read `sys+8` there) |
| random flicker callbacks (a roll per draw) | `0x19f830` (portrait/message flicker), `0x15aa40` (screen distortion: advances an angle word by 2 degrees and rolls twice per scan line per call) | G lines: `107808<19f914<11ce0c` x3, `<15adb8<15b374` x1.7 per second | RNG hold, effect hold (`rngHold`, `effect`, `effectState`) |
| memory-card manager step (a state machine with a per-state iteration counter, called by the card UI task) | `0x134fa0` (called from `0x151840` only) | RPC lines `80000400/1` every 6 iterations instead of every 6 frames (found with the caller chain in the R lines); the title's default cursor and the load list timing | gate (first call and first call after a gap run at once) |
| pad auto-repeat (hold counters per button group: `held++` per call, repeat when `held % interval == 0`) | `0x122cd8` (+4 discs by signature), counters `0x7e9388..` | RAM diff at two game times: eight words counting iterations (3 -> 6 in 50 ms at 120 Hz) | `pad` gate |
| ship map camera ease `pos += (target-pos)*0.1` (floor change) | `0x1f0388` (+ the 4 discs by signature) | camera z path identical per iteration | `mapCamera<>`: delta scaled to `1-(1-k)^(60/N)` |
| scene-exit fade of an adventure scene (the picture squeezed to 4:3 100 ms earlier at 120) | not the game: `ga2_activity::scene3d()` (VU1 ran within the last 0.5 **host** seconds) switched the presentation between wide and 4:3 | the overlay alpha was identical per game time; frame fingerprints differed for 6 native frames | the window counts guest vblanks (game time) |
| disc-change notice (the window closes 15 iterations after the button press) | scene 12 | `0x7faa68` counting 15 -> 0 per iteration, message window pop-out 133 ms early | scene 12 runs native (the only entry of the safety net) |
| one-iteration flashes | various | small (<= 19 levels) or single frames | none needed: isolated single frames up to 0.2 % of the frames are blips in the oracle |

### The oracle (`tools/framerate_oracle.py`, `GA2_FRAMETRACE`)

Trace file (one line per game iteration and per event; `gt` is the game's own time,
the sum of the `dt` it was given): `U` state, `M` view matrices, `W` the first
`GA2_FRAMETRACE_WORLDS` world matrices (default 12), `E` key-ons / CD reads / pad,
`F` a fingerprint (16x14 mean luma grid, mean colour) of every frame the GS scanned
out (`GA2_FRAMETRACE_FRAMES=1`), `T` 2D layer items whose placement changed
(`GA2_FRAMETRACE_ITEMS=1`), `R` IOP RPC calls with their caller chain
(`GA2_FRAMETRACE_RPC=1`), `P` probed guest words (`GA2_FRAMETRACE_PROBE=addr>off>off:words;...`),
`G` RNG draws by caller chain, `C` tapped calls (`GA2_FPS_GATES=<hex>:t`).
Helpers: `GA2_FRAMETRACE_WINDOW=from,to` (limit M/W/T), `GA2_FRAMETRACE_SHOTS=dir@t,..`
(PNG of the frame at a game time; high-rate runs), `GA2_FRAMETRACE_DUMP_DIR/AT/ANCHOR=abs`
(RAM dumps for `tools/framerate_ramdiff.py`: words that count iterations show as
`fast` between a native pair of dumps and a high dump; one 3 -> 6 counter and one
15 -> 14 countdown found that way, then `GA2_WRAP="gdb ... -x gdb_rwatch.py"` for the
writer), `GA2_PAD_SCRIPT=<file>` (presses in game time: `abs 26 c 0.15`, `sceneN`,
`reveal` anchors; c circle, x cross, z square, v triangle, Return start),
`GA2_FIXED_CLOCK=<seconds>` (frozen RTC).

Two native runs of the same scenario with `GA2_FIXED_CLOCK` and a game-time pad script
are bit-identical (Zettai story + battle, 424 s: every matrix, frame fingerprint, item,
text character, RPC and RNG draw): that control passes the oracle, so every difference
to a high run is the high run's. Run it:

    GA2_FIXED_CLOCK=1760000000 GA2_PAD_SCRIPT=keys.pad GA2_FRAMETRACE=a.trace GA2_FRAMETRACE_FRAMES=1 \
      GA2_FRAMETRACE_ITEMS=1 GA2_FRAMETRACE_RPC=1 python3 tools/run_owned.py <binary> out 330 ...   # native
    ... GA2_FPS=120 GA2_FRAMETRACE=b.trace ...                                                       # high
    python3 tools/framerate_oracle.py a.trace b.trace --anchor pad:1 [--sid 2] [--duration S]

It aligns on an anchor, cuts the timeline at every late iteration (frames above the
game's minFrames, a load, a movie) and aligns each clean second by whole iterations,
then requires every sample of the high run (matrix components, frame fingerprint
blocks, item placements) to lie within the range the native run covers within +-1
native frame (16.7 ms) of the same game time (an event-started animation begins at
the first iteration after the event, so +-1 frame is inherent); 30 fps views, picture
cuts after an input and text have wider windows (see the tool's docstring), text by
character, probes equal, RNG draws per caller, rare IOP requests within one frame, and
(new) the smoothness check: during motion the high run must show a new picture on nearly
every iteration (distinct pictures per second per 1 s window and per burst of 5+
changing native flips). The tool's own sensitivity: with `GA2_FPS_NOFIX=1` the title
screen's emblem rotation is 12 mrad (tolerance 0.9 mrad) off and the earlier runs without
the gates/holds failed on RNG (x1.7-3), portrait pop-ins, the card polling and the
battle's divergence.

### Results (Zettai on this host: 16 cores shared with other work, software Vulkan/GL; the oracle compares game time, not wall time)

"pictures/s" = distinct consecutive frame fingerprints per game second during motion,
native -> high (N = 120; during motion the native picture changes every frame at 59 per
second, or every other frame in the 30 fps views).

| Scene kind / sequence | Result | Compared | Smoothness (pictures/s, native -> high) |
| --- | --- | --- | --- |
| Title, load list, cursor, card check (Zettai, scene 9) | PASS | 4252 frames (36 s), 73 items, 1049 card polls (`--rpc-max 2000`) at the native cadence, 0-2 native frames earlier | cursor/blink bursts: 2 of 2 smooth; fade 58.8 -> 119.1 |
| Options menu (scene 5: cursor moves, value changes, cancel) | PASS | 2924 frames (24 s), 19 items | 3 of 3 bursts smooth |
| Adventure, story chapter 1 incl. 3D narration, voiced text, button prompts, scene-exit fades (scene 2) | PASS | 18505 frames (154 s), 73 items, 884 characters (rate max +8 ms), 334 pad presses, objects/camera inside the native range | 47.6 -> 92.9 (iteration rate 110: the host cannot hold 120 here); 52 windows animate every native frame, none repeats; 59 bursts, 3 repeat |
| Adventure system menu (Start), backlog (Square), auto (Triangle), yes/no style prompts | PASS | 12235 frames (102 s), 26 items, 62 pad presses | fades 25.6 -> 45.4 (short bursts; 2 of 12 bursts repeat) |
| Ship map (camera scroll with pad holds, selection ring, floor change `e`) | PASS | 9577 frames (80 s), 30 items, camera inside the native range | map animations 30.8 -> 61.2 |
| Battle (cockpit tutorial, HUD portraits and messages, dust, distortion ring, 30 fps views run at N), first 49 s | PASS | 5541 frames, 73 items, RNG 10.6 -> 10.4 draws/s, 369 characters, camera/objects inside the native range | 27.9 -> 112.5 (iteration rate 108); the HUD pop-ins (gated) update at the native cadence |
| Mugen 1 opening (notice, title, ADV with 2D sprites and the text box) | PASS | 14173 frames (118 s), 27 items, 317 characters | 59.0 -> 118.4, all 117 windows smooth |
| Mugen 2 opening (disc-change notice, title, ADV) | PASS (scene 12 native) | 15318 frames (128 s), 41 items, 84 characters | 52.9 -> 101.4; 2 of 24 windows and 4 of 21 bursts repeat (a fade) |
| Eigou 1 opening | PASS | 11529 frames (96 s), 14 items, 288 characters | 46.7 -> 90.3 |
| Eigou 2 narration (3D nebula, voiced text, no input) | PASS | 12715 frames (106 s) | 59.0 -> 118.9 |
| native vs native control (Zettai 424 s) | PASS, bit-identical | all checks | 44.7 -> 44.7 |
| Forced rate changes (governor test sequence 120, 60, native every 3 s: Zettai ADV menu/backlog run, 120 s; every 5 s: story + battle first 49 s, 70 changes) | PASS (frames, items, text, pad, camera/objects, probes, RNG); `--no-smooth` (a third of the time is native, so the smoothness check does not apply) | 8892 frames; 12555 + 3288 frames | -- |

Not comparable, listed as such: **the battle after about 50 s** and everything that
follows (battle menus, results screens, the disc-1 end): events that wait for an
IOP-timed completion (voice lines) land at different game times when the two runs
run at different fractions of real time (native 79 %, high 65 % of wall on this
host in the battle): the first difference is a camera dolly 100-140 ms earlier at 325 s
and everything random diverges after it. The RNG draw counts agree up to that point
(the call counts per caller are within 2 of the native run at 330 s). A host at 100 % in
both runs (or a faster one) would make the whole battle comparable. Also not reached:
the ship map's briefing/selection screens beyond the scripted moves, the gallery, the
movie players' own menus, the end of disc 1 and the second half of each disc (no saves
for them; the Mugen/Eigou openings are the only runs on those discs, with blank cards).
Movies keep their own rate (the opening movie is played inside the oracle runs and
presented). Loads, the card checks and the first second of each new scene differ by up
to 4 native frames (the IOP finishes them in host time) and are aligned per window.

(The oracle outputs, traces and frame pairs are in
`<private>/research/run58/framerate/`.)

### Achieved rate and headroom

The host was shared (load 15-50 on 16 cores, software Vulkan/GL for the window). GA2_PERF
`[ga2 fps]` lines: ADV 78-90 of 120 on Zettai (game thread at 62-78 % of one core, about
8 ms of EE thread per frame, so about 125 fps without contention); Mugen 1 story 108 of
120; Mugen 2 and Eigou 2 narration 72-89 %; the options screen only 65 % (many 2D items,
software GS); the full-rate battle 65-67 % (native: 79 %, so the battle is
bound by the host in both). When the host cannot keep up the game runs in slow motion exactly like
native (the tick clock is cycle- and host-paced). Distinct frames: presented/s equals guest
flips/s in every report (one scan-out per flip).

### Per-disc addresses

Update / WaitVSync / its call from RenderFlip (return address) / ticks conversion
and its call / setMax / setMin / interval global / flip / sys pointer / MatSet /
message glyph drawer / 2D item draw / map update / RNG next / map camera follow:

| disc | Update | WaitVSync (ra in RenderFlip) | ticks->vsyncs (ra) | setMax / setMin | interval global | flip | sys | MatSet | glyph | item draw | map update | RNG | map camera |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| zettai | 0x113210 | 0x237748 (0x112ebc) | 0x223218 (0x112e70) | 0x113388 / 0x1133a0 | 0x2cf774 | 0x2285e0 | 0x2d3be4 | 0x22fc88 | 0x111c70 | 0x11ce18 | 0x1f6760 | 0x107758 | 0x1f0388 |
| mugen-1 | 0x1132c0 | 0x2511a8 (0x112f4c) | 0x23cc70 (0x112f00) | 0x113438 / 0x113450 | 0x2ebd74 | 0x242038 | 0x2f01e4 | 0x2496e0 | 0x111cd8 | 0x11d030 | 0x20fc50 | 0x1077b8 | 0x2097b0 |
| mugen-2 | 0x113290 | 0x2391f8 (0x112f3c) | 0x224cc8 (0x112ef0) | 0x113408 / 0x113420 | 0x2d1274 | 0x22a090 | 0x2d56e4 | 0x231738 | 0x111cf0 | 0x11ce98 | 0x1f8218 | 0x1077e0 | 0x1f1e40 |
| eigou-1 | 0x1132c0 | 0x25dea8 (0x112f4c) | 0x249970 (0x112f00) | 0x113438 / 0x113450 | 0x2f9e74 | 0x24ed38 | 0x2fe2e4 | 0x2563e0 | 0x111cd8 | 0x11d030 | 0x21c278 | 0x1077b8 | 0x215dd8 |
| eigou-2 | 0x1132c0 | 0x252218 (0x112f4c) | 0x23dce0 (0x112f00) | 0x113438 / 0x113450 | 0x2ece34 | 0x2430a8 | 0x2f12a4 | 0x24a750 | 0x111cd8 | 0x11d030 | 0x2109b0 | 0x1077b8 | 0x20a510 |

The fixes of the second pass (portrait widget, card step, particle update, palette
cycle, pad updater, RNG-held draw callback, distortion effect with its angle word, pad
state arrays (raw state; +0x10 previous, +0x18 current; the four repeat outputs at the
second address, 0x10 apart, 4 bytes per port)):

| disc | portrait `0x19fce0` | card step `0x134fa0` | particle `0x197208` | palette `0x182e08` | pad updater `0x122cd8` | RNG-held `0x19f830` | effect `0x15aa40` / angle word | pad raw / outputs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| zettai | 0x19fce0 | 0x134fa0 | 0x197208 | 0x182e08 | 0x122cd8 | 0x19f830 | 0x15aa40 / 0x2bcfc4 | 0x2b6850 / 0x7e9350 |
| mugen-1 | 0x1ada30 | 0x1359f8 | 0x1a5298 | - | 0x122ef0 | 0x1ad580 | 0x1613f8 / 0x2d9504 | 0x2d2d50 / 0x506e90 |
| mugen-2 | 0x1a1780 | 0x135738 | 0x198ca8 | 0x1848a8 | 0x122d58 | 0x1a12d0 | 0x15c4e0 / 0x2beae4 | 0x2b8350 / 0x7eafd0 |
| eigou-1 | 0x1b34b0 | 0x135b58 | 0x1aa828 | - | 0x122ef0 | 0x1b3000 | 0x163440 / 0x2e75a4 | 0x2e0dd0 / 0x515710 |
| eigou-2 | 0x1ae5b8 | 0x135ad0 | 0x1a5e20 | - | 0x122ef0 | 0x1ae108 | 0x161e70 / 0x2da5b4 | 0x2d3dd0 / 0x508310 |

### Safety net and risks

- Safety net (native fallback): **scene 12, the disc-change notice** (all discs; shown
  by Mugen 2 and the Eigou discs between discs, a static window that closes 15
  iterations after the press). `GA2_FPS_NATIVE=<ids,map>` overrides the list.
- What is held to the native cadence and therefore not smoother than native: the card
  manager, the pad auto-repeat, the battle HUD portraits (they update at 30 or 60 Hz
  and show the intermediate pictures as repeats), the space dust particles, the
  distortion ring, the random flicker, the palette cycle. They are small and
  housekeeping-like; making them smooth would need a re-implementation (fractional
  recurrence steps `x = x*a^f + c*(1-a^f)/(1-a)` for the portrait).
- Unverified: the battle beyond 50 s, battle menus and result screens, the later parts of
  each disc, the Mugen/Eigou battles (no saves), the gallery, the Mugen/Eigou system
  menus beyond the Zettai ones (same engine), movies at N (presented, not compared).
- Input-triggered changes appear up to a few native frames earlier at N than natively
  (the pad is read once per native iteration, but the pipeline behind it is shorter in
  time): the oracle allows 2 native frames later and about 100 ms earlier for message
  starts, 2.1 frames (2.6 iterations in 30 fps views) for picture cuts.
- The oracle needs deterministic input (`GA2_PAD_SCRIPT`) and `GA2_FIXED_CLOCK`; real
  play (random blinks seeded by the real clock) differs run to run natively too.
- GA2_FPS needs the GPU GS; VR is native. `ps2x_tests` on this host: 570 of 611 pass
  (41 GS/VU0 tests fail identically with and without this work: not caused by it).

### Phase 4 (2026-10-22): whole-battle oracle, live cap, VR

The battle beyond 50 s was "not comparable" because the two runs ran at different fractions of
real time. That was only part of it; with deterministic runs the real causes showed:

- **Deterministic test runs.** `GA2_FREERUN=1` makes every host deadline count as passed (events
  fire at their EE cycle; the game runs as fast as the host can) and a scripted run
  (`GA2_PAD_SCRIPT`) sees only the script (window input reached the guest in host time). Two
  runs of the same script are now identical whatever the host load (Zettai prebattle, 2550 s of
  game time, every trace line).
- **Loads took less EE time at N.** An idle EE advances to the next scheduled event and only
  then runs the IOP: with frame ticks queued every 1/N s, a CD read's completion was seen twice
  as often and a 225-vsync load finished in 205. Ticks are now queued only while the game waits
  on the frame clock (ticks in between are counted from the EE cycle): loads take the native EE
  time.
- **Overrunning iterations lost less game time.** Natively an iteration that overruns the
  game's catch-up limit (`frames > maxFrames`: loads, scene switches) ends on a VBLANK; at N it
  ended on the next tick (half frames at 120). Such an iteration now wakes at the next VBLANK
  and the ticks are realigned to it. With both: game time minus VBLANK time stays within about
  two native frames of the native run over the whole sequence (was 0.69 s apart by the battle).
- **Gates and synchronous RPC.** A gated function that blocks in a synchronous RPC never came
  back through the wrapper, so the wrapper ran it every iteration (the card step, the sound
  updates: N/60 times the native IOP requests). The gate now points the call's return address at
  its own entry: the function's `jr ra` returns through the gate (directly or after the
  scheduler resumes it), which records v0 and continues at the caller. New gates: the per-frame
  sound/stream update (`0x1229f8`) and the sound manager update (`0x12ed58`; voice status
  queries), all discs. IOP request rates are now the native ones.
- **The game's own step.** Gated updates got `float(1001/60000)` and the high step was
  `1001/(1000 N)` in float; the game's `1/59.94` float differs in the 7th digit, threshold timers
  (`b0 >= sys+8`) tripped in other iterations (a particle spawn moved, every later random choice
  changed: the battle camera orbited the other way). Both now derive from the game's own step
  (at 120 exactly half of it).
- **The battle's 30 fps views keep 30 fps by default.** At full rate (`GA2_FPS_BATTLE=full`) the
  battle dialogue reacts up to 0.45 s sooner after a press (the message flow steps once per
  iteration: 30 steps a second natively). Kept at 30, text, items and key-ons match native over
  the whole battle (key-ons within 34 ms). Open: camera/objects/frames still differ after about
  350 s (the orbit camera in the cockpit tutorial) and need the remaining iteration-counting code
  found; the random-draw report lists the particle callers through the gate's own address (the
  return trick), an artefact.
- **Live cap and native default.** The hooks now run in every run that can use them (GA2_FPS is
  the start cap; 60 is the native cadence); the settings menu raises the cap live up to 240.
  At the native cadence the hooks call the game's own setters and leave its own step: a native run
  with the hooks matches one without (`GA2_FPS_LIVE=0`) except a sub-frame EE-time difference (one
  IOP request one iteration earlier in 560 s).
- **VR** follows the headset: the cap is the median of the predicted display period (stalls below
  50 Hz ignored); the window has no vsync in VR. Checked in Monado's null compositor (it reports
  20 Hz: native kept); a 90/120 Hz headset is untested.

### Phase 5 (2026-10-22): the battle iteration for iteration

With deterministic runs (`tools` in `<private>/research/run58/framerate/phase4/tools`: `run.sh`,
`check.sh`, `drawcmp.py` for the first differing random draw, `vsgap.py` for the VBLANK count at equal
game time) the remaining differences were all one native frame of iteration counting:

- **Scene switches** (`sceneManager<>`, hook on the scene manager's update `0x103550` / mugen-1, eigou
  `0x1035b0` / mugen-2 `0x1035d8`; manager +4 pending request, +8 scene id): a request made in an
  iteration starting at game time t is carried out at the first iteration starting at or after (t rounded
  up to a native frame) + 1 frame, as natively; the manager's update is skipped until then. The three
  iterations after a switch run at the native cadence (`g_settle`): a new scene counts its first
  iterations (the battle sets up its objects and its 30 fps limit in its second).
- **30 fps views on their native grid** (`g_minOrigin`): the limit natively takes effect one frame after
  the native frame of the iteration that set it; until then single ticks, then the iterations end on
  that grid (every minFrames native frames).
- **Game time against the VBLANK** (`g_lag`): natively game time trails the VBLANK count by a whole
  number of frames that changes only at overruns (F = frames between the slow iteration's VBLANK and the
  wake, the loss F - min(F, maxFrames)). The iteration woken after an overrun gets the game time that
  puts the next VBLANK at the native game time; an iteration whose wait changes (entering/leaving the
  native cadence) gets its own wait as game time. (Rounding woken iterations to whole frames instead put
  game time half a frame ahead of the VBLANK at each slow iteration: voice lines, timed in EE time,
  ended late against game time and battle messages waited an extra iteration.)

Result (Zettai prebattle script, free-running, native vs 120 with the battle's 30 fps views at 30):
every iteration of the battle at the native game time; the random sequence identical through the end
of the battle dialogue (1008 draws to 369.1 s); the VBLANK count at equal game time within 1 of native
outside the sortie loads (was 14 by the battle); sound key-ons within 25 ms; text within one native
frame. After 369 s the random sequence parts (the cockpit tutorial's camera orbits differently): the
one-VBLANK difference left from the boot-time scene switch, where settling starts half a frame off.
Native runs on a real machine differ from each other by more than that (only free-running runs are
repeatable at all).

## Reproduction

Private tools in `research/rendering/tools/`: `dec.sh` + `Decomp.java`
(Ghidra headless, read-only: decompile, `callers:`, `refs:`, `dis:`),
`sigmatch.py` (cross-disc signatures), `cap.py` (VU1 capture reader),
`run.py` + `x11_input.py` (runs `tools/run_owned.py` with key injection that
also accepts the current untitled game window; the stock harness reports
`no visible game/reference window ... titles=[]` with today's build).
gdb wrapping: `GA2_WRAP="gdb -q -batch -x <script> --args"`.
