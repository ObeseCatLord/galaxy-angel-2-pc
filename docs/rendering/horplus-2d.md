# Hor+ widescreen: where 2D is placed in screen space

Status 2026-10-14. Follow-up to [horplus-and-framerate](horplus-and-framerate.md)
after the first implementation (`native/compat/ga2_features.cpp`) garbled the
ship-map UI. Evidence in `<private>/research/rendering/` (`t2d-native.txt`,
`t2d-native-path2-decoded.txt`, `gdb_trace2d.py`, `gdb_horplus_xy.py`,
`zmap-xy-squeeze-21x9.png`, `decomp/y*.c`).

## Cause of the garbled ship-map UI

`0x2331d8` and `0x233220` are **texel** rectangle setters, not screen
rectangles (my earlier note had them swapped). They write the UV words of the
sprite template (`0x2cf4c0`/`0x2cf4d0` = `(u*16+8, v*16+8)`); the screen
words (`0x2cf4c8`/`0x2cf4d8`, XYZF2) are written by `0x233308` / `0x2332a0`.
Squeezing the texel rectangle sampled the wrong part of each texture: the
"BLOCK-A..D" labels sampled empty texels, the destination preview sampled a
compressed slice of its 160x96 picture plus neighbouring VRAM (the striped
panel), and nothing moved on screen (the panels stayed at their native,
stretched positions).

Verified with gdb on the Zettai ship map, k = 1.75, no runtime change: PAR
scaled at camera/framework construction and the squeeze applied to the
screen-position setters below gives correct UI (labels, preview, rotated
wing quads, the round centre cursor) inside the 4:3 centre of the stretched
21:9 image: `zmap-xy-squeeze-21x9.png` (top: before the squeeze starts,
bottom: squeezed).

## Every 2D screen-space entry point on the ship map

Traced for two map frames (`gdb_trace2d.py`, breakpoints on every Axl 2D
setter, emit and packet allocator, plus a dump of every PATH2 payload):

- All UI sprites and text go through the Axl 2D API screen setters below.
  The map UI is drawn by the game's 2D layer (`0x11b450` sprite,
  `0x11b658`/`0x11ba18` rotated quads), text by the glyph drawers
  (`0x110c78`, `0x111298`, `0x1117a8`, `0x111c70`: each glyph is uploaded with
  `0x22bcc8`, then drawn with the texel setter + `0x233308`).
- Two full-screen parallax background layers are sprites larger than the
  screen (`-64.5..703.5` and `-160.5..799.5`); leave them unsqueezed.
- The rest of PATH2 is not UI: draw-environment packets, a frame clear made
  of 64-pixel strips (`0x2292c8` -> `0x229040`, XYOFFSET 0, SCISSOR the whole
  target), texture uploads, and a glow pass (copy of the frame into a 256x256
  target, offset copies, then one full-screen sprite back at
  (1728,1808)-(2368,2288)). None of these call the 2D API, so hooks on the
  API leave them alone, which is what they need.
- No SCISSOR change happens in the 2D path. SCISSOR is set only in
  draw-environment packets (full target: 0..639 x 0..479 on screen, 0..255
  on the 256x256 glow targets) and by the clear builder `0x229040`
  (whole target). There is no scissor setter to squeeze.
- 2D coordinates are a 640x480 virtual screen. The setters add the origin
  `0x2cf368/0x2cf36c` = `(2048-320, 2048-240)*16` (set by `0x231da8(640,480)`
  at init), and the environment uses XYOFFSET (1728, 1808), the same as 3D.

## Hooks (all discs)

Squeeze rule, applied to every x coordinate (not widths):
`x' = 320 + (x - 320)/k`. Leave the primitive unchanged when its x range
covers the screen (`min x <= 0 && max x >= 640`: backgrounds, fades, fills).
Do not touch y, the texel/ST setters or the colour setters.

| Function (args, MIPS EABI) | zettai | mugen-1 | mugen-2 | eigou-1 | eigou-2 |
| --- | --- | --- | --- | --- | --- |
| **sprite XY float** (`f12 x0, f13 y0, f14 x1, f15 y1, a0 z`) | 0x233308 | 0x24cd68 | 0x234db8 | 0x259a68 | 0x24ddd8 |
| **sprite XY int** (`a0 x0, a1 y0, a2 x1, a3 y1, t0 z`, int16) | 0x2332a0 | 0x24cd00 | 0x234d50 | 0x259a00 | 0x24dd70 |
| **flat rect XY float** (as sprite float) | 0x233448 | 0x24cea8 | 0x234ef8 | 0x259ba8 | 0x24df18 |
| **flat rect XY int** (as sprite int) | 0x2333e0 | 0x24ce40 | 0x234e90 | 0x259b40 | 0x24deb0 |
| **flat quad XY** (`f12..f19` = x0,y0..x3,y3; `a0..a3` z0..z3) | 0x2329c8 | 0x24c428 | 0x234478 | 0x259128 | 0x24d498 |
| **gouraud quad XY** (as flat quad) | 0x232c18 | 0x24c678 | 0x2346c8 | 0x259378 | 0x24d6e8 |
| **textured quad XY** (as flat quad; rotated sprites) | 0x232e38 | 0x24c898 | 0x2348e8 | 0x259598 | 0x24d908 |
| sprite texel rect int (`u, v, w, h`): do not squeeze | 0x2331d8 | 0x24cc38 | 0x234c88 | 0x259938 | 0x24dca8 |
| sprite texel rect float: do not squeeze | 0x233220 | 0x24cc80 | 0x234cd0 | 0x259980 | 0x24dcf0 |
| textured quad ST: do not squeeze | 0x232d18 | 0x24c778 | 0x2347c8 | 0x259478 | 0x24d7e8 |
| 2D origin setup (`W, H`) | 0x231da8 | 0x24b808 | 0x233858 | 0x258508 | 0x24c878 |
| emits: sprite / flat / flat quad / gouraud / textured | 0x233380 / 0x2334c0 / 0x232aa0 / 0x232cf0 / 0x232f10 | 0x24cde0 / 0x24cf20 / 0x24c500 / 0x24c750 / 0x24c970 | 0x234e30 / 0x234f70 / 0x234550 / 0x2347a0 / 0x2349c0 | 0x259ae0 / 0x259c20 / 0x259200 / 0x259450 / 0x259670 | 0x24de50 / 0x24df90 / 0x24d570 / 0x24d7c0 / 0x24d9e0 |

The other seven templates in `0x231938` (lines, points, untextured strips at
`0x2cf380`, `0x2cf3b0`, `0x2cf3f0`, `0x2cf440`, `0x2cf510`, `0x2cf550`,
`0x2cf5a0`) have emit wrappers with no callers and no setters (dead code).

The Mugen/Eigou addresses keep Zettai's offsets from the unique texel setter
and were each confirmed by masked signature; the sprite/flat pairs have
identical code, so the offset decides which is which. The 2D layer, glyph
and clear functions match too:

| Game-side function | zettai | mugen-1 | mugen-2 | eigou-1 | eigou-2 |
| --- | --- | --- | --- | --- | --- |
| 2D layer sprite draw | 0x11b450 | 0x11b668 | 0x11b4d0 | 0x11b668 | 0x11b668 |
| 2D layer rotated quads | 0x11b658, 0x11ba18 | 0x11b870, 0x11bc30 | 0x11b6d8, 0x11ba98 | 0x11b870, 0x11bc30 | 0x11b870, 0x11bc30 |
| glyph drawers | 0x110c78, 0x111298, 0x1117a8, 0x111c70 | 0x110ce0, 0x111300, 0x111810, 0x111cd8 | 0x110cf8, 0x111318, 0x111828, 0x111cf0 | as mugen-1 | as mugen-1 |
| clear (`x, y, w, h, rgba`; 64-px strips): never squeeze | 0x2292c8 | 0x242d20 | 0x22ad78 | 0x24fa20 | 0x243d90 |

Gating: squeeze only while a hor+ 3D scene is shown. Squeezing every 2D
frame also works (the load menu squeezes coherently), but 2D-only scenes
then become pillarboxed and the mouse menu rectangles (`ps2_mouse_menus.cpp`,
RAM and picture probes) no longer match. Note that the squeezed load menu
did not accept the scripted clicks in the test.

## HUD markers anchored to 3D positions

These positions are computed from `VS x View`, which is already hor+, so the
screen-setter squeeze moves them a second time. Every anchored point is made
by the same inline pattern `x = X/W - 2048 + 320`, `y = Y/W - 2048 + 240` and
then stored or returned, to be drawn later by the 2D layer through the same
setters. The anchor is not known at draw time, so the practical fix is to
expand the anchor at its source: `x_a' = 320 + (x_a - 320)*k`. The later
squeeze maps it back to the true screen position and shrinks only the size.

Producers on Zettai (functions loading both 2048.0 and 320.0):

| Function | Output |
| --- | --- |
| `0x1655c8(?, pos, out)` | `out[0..1]` (helper; callers `0x1a2428`) |
| `0x190680(pos, out)` | `out[0..1]` (helper; caller `0x185ce0`) |
| `0x1818f8` | `obj+0x1a0/0x1a4` (unit marker; also 2D effect colours) |
| `0x1a3a98`, `0x1a5bb8` (adds an offset to the point) | locals then sprites |
| `0x162398` | on-screen pick test (`0..639 x 0..479`), stores the world position |
| `0x18f878`, `0x18fc18`, `0x1889f0`, `0x1944e0` | locals; `0x1944e0` draws AxlPrim lines |

Other discs (same pattern, functions loading 2048.0 and 320.0):

- mugen-1: 0x16c338, 0x16f890 (= helper `0x1655c8`), 0x18d3c0, 0x196028, 0x19ceb0, 0x19d250, 0x19dd60 (= `0x190680`), 0x1a2370, 0x1b1560, 0x1b3538
- mugen-2: 0x163e38, 0x167068, 0x183398, 0x18a490, 0x191318, 0x1916b8, 0x192120, 0x195f80, 0x1a5538, 0x1a7658
- eigou-1: 0x16ee88, 0x173560, 0x1929d0, 0x19b6d8, 0x1a2350, 0x1a26f0, 0x1a3200, 0x1a78c8, 0x1b7028, 0x1b8ff0
- eigou-2: 0x16ce58, 0x1703b0, 0x18dee0, 0x196b88, 0x19da38, 0x19ddd8, 0x19e8e8, 0x1a2ef8, 0x1b20f8, 0x1b40d0

Post-hooks are simple for the two helpers (rewrite `out[0]`). The inline
producers need either a post-hook that rewrites the stored field
(`0x1818f8`: `obj+0x1a0`) or an override. Risk: code that tests the expanded
x against `0..639` after the expansion drops markers in the widened side
area; expand after such tests (the pick test `0x162398` runs on the
unexpanded value inside the function). Not verified: no battle run was
traced, and the ship map has no anchored marker (its cursor is fixed at the
screen centre, which the squeeze keeps).

## HUD at the screen edges (2026-10-19)

Squeezing everything around x = 320 keeps the HUD at its 4:3 positions in the
middle of a wide frame (the radar floats in from the left edge). The battle
HUD, the ship map's panels and the communication window are all items of the
Axl 2D layer, drawn one by one through the item draw function (`item, list`):

| | zettai | mugen-1 | mugen-2 | eigou-1 | eigou-2 |
| --- | --- | --- | --- | --- | --- |
| layer item draw | 0x11ce18 | 0x11d030 | 0x11ce98 | 0x11d030 | 0x11d030 |
| layer list draw (calls it per item) | 0x120620 | 0x120838 | 0x1206a0 | 0x120838 | 0x120838 |

Item fields: kind `+8` (0 sprite, 1/3 callback, 2 orthographic VU1 model via
`0x11c0e0`, 4 layout), flags `+0x10`, position `+0x3c/+0x40`, size
`+0x4c/+0x50`, pivot `+0x44/+0x48`, scale `+0x60/+0x64`, draw callback
`+0x78` (user data `+0x7c`). A layout (kind 4, made by the layout loader
`0x11f698`; children fetched with `0x121958(mgr, layout, index)`) keeps its
record at `+0x28` (child count at `+0x14`) and the child array at `+0x2c`.
Callback items (the unit panel bars `0x1a5870`, the radar `0x1a6a18`, the
communication text `0x16c578`, the portrait `0x19f830`, the target ring
`0x1a5bb8`, ...) draw with the item's rectangle; every 2D primitive of the
battle HUD except the full-screen post-effect passes runs inside one of
these item draws (traced with the stack walk of `GA2_HUD_LOG`).

The hook on the item draw sets the anchor used by the screen setters while
that item draws:

- left: `x' = x/k`, right: `x' = 640 - (640 - x)/k`, centre: `320 + (x - 320)/k`
  (all keep the 1/k shape; left/right only add a constant shift of
  `±320(1 - 1/k)`).
- Rectangles are the items' rest rectangles (position, size times scale; a
  zero scale hides an item), not where callbacks end up drawing.
- Thirds rule: left when a rectangle starts in the left third and ends
  before the right third, right for the mirror, otherwise centre.
- A layout's union of child rectangles decides for all its children: left
  or right by the thirds rule, centred when it stays clear of the outer
  sixths (a pop-up unit panel). A layout spread over the screen (Zettai's
  cockpit and its corner counters are loaded as such layouts) is split into
  groups of touching items (screen-sized backdrops such as the 480x480
  reticle circle left out), and each group is placed by its union the same
  way.
- Anything inside the inscribed ellipse (radius +5%) of a large centred item
  seen in the last 250 ms (at least 240x120: the reticle circle, the pop-up
  panel) stays centred, even in a layout of its own: the cockpit's H.POINT
  and ENERGY gauges and their pieces are separate one-item layouts in the
  left and right thirds, and the tutorial's highlight frames around them
  are a little larger than the gauges.
- Callback items without a rectangle (markers drawn on projected 3D
  positions) stay centred.
- Orthographic VU1 models drawn in 2D space never reach the setters: the
  radar blips (kind-2 layer items). A hook on the ortho model draw
  (`0x11c0e0` / mugen-1, eigou-1/2 `0x11c2f8` / mugen-2 `0x11c160`; record:
  x `+0x14`, x scale `+0x38`) places x by the current item's anchor (centred
  outside item draws) and divides the x scale by k for the draw. Before this
  the blips were drawn outside the radar. (The gems around a speaker's name
  are not drawn this way, see the next section.)
- Edge rule: an item or group anchors left only if it reaches into the left
  sixth of the screen (and ends before the right third), right by the
  mirror rule; the page icon at the end of a message box stays centred.

Verified (`research/run57`): Zettai Stage 1-1 at 21:9, 16:10 and 16:9
(`zf21x9`, `ze16x10`, `zf16x9`): radar and blips top left, communication
window (portrait, balloon, page icon) bottom left, unit panel and the pilot's
SPIRIT portrait bottom right, the pop-up unit panel, TARGET bar, reticle
gauges, tutorial highlights and target ring centred. Zettai ship map at 21:9
(`zemap`): destination preview top left, block list top right, cursor
centred. Mugen 1 tutorial battle at 21:9 (`mf21x9`): the same, plus the
unit shortcut bar on the left edge and the controller picture top right.

Runs: fixture `research/fixtures/mc-zettai-prebattle` with
`research/run57/keys-zettai-prebattle.json` (battle from ~330 s),
`mc-zettai-map` with `keys-zettai-map.json`, Mugen 1 from a blank card with
`research/run56/keys-mtut3.json`.

Unit icons on the tactical map (2026-10-20, measured on Mugen 1's tutorial
map at 16:9 with the setter log): each icon is a 48-wide sprite item placed at
the unit's projected position (`unit + 0x1a0` Z layout, `+0x1b0` M/E), so it
gets its anchor from its own rectangle like any item: icons in the middle
squeeze about the centre (`320 + (x - 320)/k`), the outer ones near an edge
keep their distance from it (`x/k`, `640 - (640 - x)/k`). Icons therefore do
not sit on the 3D grid cell of their unit in a wide frame (cosmetic), and
whatever points at them (the battle pointer, `ga2_battlepointer.cpp`) must use
the drawn position: `ga2_hud::markerX(x, 24)` (`native/compat/ga2_hud.h`)
applies the same rule. The same pointer code moves the map's cursor ring (a
72 x 72 sprite item, `mapview + 0x14`, positioned once by the game with a
position function at load) to the mouse; the ring's items are registered with
`ga2_hud::keepCentred` so the edge rules cannot throw it off the pointer.

Only in HUD scenes (2026-10-22): the edge rules apply while the scene manager's
scene is the story (2, the ship map), the battle (3) or the Mugen/Eigou
tutorial battle (13). Angel Select (4) is a full-screen menu over a 3D model:
its radar item (x 60..204) and name plate (27..267) fell under the left rule
and the four rings around the frame model (264..600) under the right one, so
the radar and the pilot's name floated out of the 4:3 panel at 16:9. Outside
the HUD scenes every item stays centred (`edgeScene`, `ga2_features.cpp`; the
scene id is read through the sys singleton as in `ga2_skip.cpp`). That fixed the
radar outline, labels and name plate, but the "present"/"default" stat polygons and
the triangle between the portraits are drawn by the scene's selector through
cameras of its own (not the 2D setters, not the ortho model hook) and stayed
unsqueezed. Angel Select is therefore shown at 4:3 (`narrowScene`: k = 1 while
the scene is 4), exactly as on the PS2; it gains nothing from a wider view (its
background is a 2D picture and the frame model sits in a ring).

When a frame is wide (2026-10-23): VU1 activity alone made the presentation
switch to the wide shape, but the story scene's transition effects (the wipe
into the briefing map, the fade before a movie) and the title's effects run
VU1 without a camera, so visual-novel scenes went wide for about a second at
scene changes. `ga2_features.cpp` now publishes the factor
(`ga2_activity::horPlus`) only while a perspective camera was applied
(`ApplyProjection`) on at least 10 of the last 30 vblanks since the current
scene began (`GA2_WIDE_TRACE=1` shows the switches and scene changes). Verified:
Zettai briefing, title, movies stay 4:3; battle and the ship map go wide at
once (`research/run58/work/widetrace4`, `mapwide`).

Known limit: an item animating across a third boundary changes anchor in
one frame (the Zettai tutorial zooms the radar from the corner to the
middle: it starts at the left edge and ends centred, with one jump).

`GA2_HUD_LOG=<seconds>` (diagnostic) prints from that many seconds after
start: every setter call with the game's call chain (return addresses
walked through the functions' `sd ra` slots), each layer item when its
rectangle or side changes, and each layout's union and side.

## The gems around a speaker's name (2026-10-20)

At 16:9 and 21:9 the two small blue gems on either side of the speaker's
name in a 3D scene's dialogue box (Eigou 2's opening narration) sat off the
name: the left one outside the box's name tag, the right one on a character.
Measured in frame pixels they were at their 4:3 places: unsqueezed. They are
not AxlClump models and not kind-2 layer items (the ortho model hook is never
called in that scene; the layer's items there are sprites and callbacks).
The name tag is a callback item of the message window (`0x1f9e30` ->
`0x1f7538` on Eigou 2); it calls the name drawer `0x1e9820` (zettai
`0x1d1130`), which draws the left gem at x = 122 (`0x42f40000`), the name
text through the glyph drawer (squeezed like all text), then the right gem
after the text, both with the **direct sprite** function `0x10bb90`. That
function builds a GIF packet itself (`0x240190`, 12.4 fixed point of
`x + draw offset`) and never calls the Axl 2D screen setters, so the setter
hooks did not see it; its other callers are menu and window pieces of the
2D layer (`0x146948`, `0x1484e8`, `0x14b3d8`, `0x1520d8`, `0x1fd400`,
`0x157800` on Eigou 2).

Fix: the same float-rectangle squeeze as the sprite setter (`rectFloat`,
x0 `f12` and x1 `f14` through `placeX`, anything spanning the screen left
alone) on the direct sprite function:

| | zettai | mugen-1 | mugen-2 | eigou-1 | eigou-2 |
| --- | --- | --- | --- | --- | --- |
| direct sprite (`f12 x0, f13 y0, f14 x1, f15 y1, f16..f19 u0 v0 u1 v1, a0 flag`) | 0x10bb28 | 0x10bb90 | 0x10bba8 | 0x10bb90 | 0x10bb90 |
| name drawer (left gem, name, right gem) | 0x1d1130 | 0x1e8c40 | 0x1d2bd0 | 0x1f2bf8 | 0x1e9820 |

(Masked signature match of Eigou 2's functions against the other discs; the
name drawer is listed for reference, it is not hooked.) Verified on Eigou 2's
narration at 4:3, 16:9 and 21:9 (`research/run57/gems/`: `gems-compare.png`,
top to bottom: 4:3, 16:9 before, 16:9 after, 21:9 after): the gems sit
symmetrically around the name at the same pixels as at 4:3.

## Battle map markers, radar blips and the message balloon (2026-11-11)

Seen in an Eigou 1 battle at 16:9 and 21:9 (`research/run58/work/ws169`, `ws219`):

- **Unit markers** (ring `cb 0x194298`, frame, face, star: 2D layer items at the unit's icon): the game
  projects the icons with the widened camera (measured: unit offsets on the map are 1/k of their 4:3
  values, `icon43` vs `icon169`), so their x is already in the wide frame. The old rules squeezed them
  again about the centre, and near an edge the 48-wide ring and the 32-wide face could fall on different
  sides of the edge rule and come apart. Now the battle pointer publishes the icons each map update
  (`ga2_hud::setUnitIcons`); an item of no layout, at most 72x72, centred within 20 px of an icon is a
  world marker: its centre stays where the game put it and its shape is squeezed about that centre
  (`AnchorWorld`). The pointer's hit test (`markerX`) uses the icon x unchanged.
- **Radar blips** (kind-2 items, orthographic models): the item draw reaches the ortho draw by a tail
  jump, which the build compiles as a direct call past the hook (Eigou, Mugen; Zettai calls it).
  The item draw hook now places a kind-2 item's x and x scale on the item for its draw (the ortho hook
  then leaves it), and an item of no layout inside a recently drawn edge-anchored layout (the radar box)
  takes that layout's side.
- **Transformed sprites** (`0x11bc30` / zettai `0x11ba18` / mugen-2 `0x11ba98`: x f12, y f13, w f14,
  h f15, pivot f16/f17, scale f18/f19, angle on the stack; builds its own matrix): hooked, x by the
  anchor, width and pivot by 1/k.
- **Message balloon**: the communication window's layout spans the screen; its balloon is a band of
  plain sprites (caps and stretched middle pieces whose rest rectangles do not touch) and was split
  between the left and right edges. A band of three or more plain sprites (no callback) with the same top
  and height, plus what lies inside its box (text, page icon), is now one group, anchored to the edge it
  starts from. Callback digits (the score and time limit of the top bar) are not bands.

Diagnostics: `GA2_HUD_LOG` (rect hooks in battle scenes, with item depth and anchor), `GA2_CAM_LOG`,
`GA2_TRACE_ITEM_CB=<callback>` and `GA2_TRACE_BATTLE_FRAME=<s>` (every guest call, runtime patch 0134).

## Not covered

- AxlPrim immediate geometry (`0x230d70`, beams and lines through VU1) uses
  the 3D matrices and is already hor+.
- (Orthographic VU1 model draws and the direct sprite function are squeezed, see above.)
- Movies (IPU) are separate.
