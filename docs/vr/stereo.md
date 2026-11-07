# 6DoF stereo VR for the 3D scenes: plan

Status 2026-10-15: **implemented as single-pass stereo** (below), which
replaces the multi-pass plan in the sections after it. Research and a gdb
prototype on the stock Zettai binary came first.

## Implemented: single-pass stereo

The game renders each 3D frame once (one Update, one VU1 pass) from the
head: `native/compat/ga2_vrstereo.cpp` pre-hooks RenderFlip (take the newest
head setup), MatSet(0) from `CCamera::Apply` (view = V C S^-1 inv(head) C S),
the perspective builder (a projection covering both eyes' field of view over
640x448, and clip extents Ex/Ey lowered so the guard band stays inside the
GS's 0..4095; the game's 1856 overflowed and produced a grey wash), the
sphere/frustum test (always visible) and the flip (latches the setup). The
GS stream goes to two paraLLEl-GS instances (`gs_pgs.cpp`, one Vulkan device
each); each moves every vertex with Q != 1 by f * (+-ipd/2) * Q pixels
(`Hacks::stereo_shift_q`; Q = 1/view z, so this is exactly the picture of an
eye moved along the camera x axis). Frames carry their setup through the
readback ring; `ga2_vr` shows each eye's frame over the whole view and
submits it with the pose and field of view it was rendered with, so the
compositor reprojects. Cost: a second GS raster on the GPU, nothing on the
EE or VU1. Diagnostics: `GA2_VR_STEREO_DEBUG` (1 game view, 2 game
projection, 4 game frustum test, 8 log builder arguments), `GA2_VR_DUMP`,
`research/vr/run_vr.sh` (Monado simulated headset, headless gamescope;
`run_vr_hands.sh` is the same with Monado's qwerty driver, which adds two
controllers, used for the hand reticles drawn over the eye frames, 0084:
`research/vr/hands-e2`).
Limits: untextured geometry without Q and 2D (UV sprites) get no parallax
(`stereo_shift_flat`, 0 = at infinity); the 2D HUD is in the 3D view unless
the HUD panel (next section) is on.
 Evidence is in `<private>/research/vr/`:
- `ct-map.txt`, `ct-e2.txt`: call traces of one frame on the ship map and
  Eigou 2's narration;
- `gdb_stereo_proto.py`, `st1`-`st3`: the prototype runs;
- `lists/`: packet lists of update and render-only iterations;
- `zmap-stereo-pair.png`: the two eye images;
- `address-table.md`: the table in section 6.

## HUD panel (`GA2_VR_HUD_PANEL=1`, default off)

Status 2026-10-20, branches `vr-hud` (runtime) and `vr-hud` (paraLLEl-GS), not
merged. Shows the 2D overlay (HUD, dialogue box, menus) of a stereo 3D scene
on a flat panel at a fixed distance instead of baked into the eye images at
infinity. Evidence: `<private>/research/vr/hud-panel/`.

Design:
- A third paraLLEl-GS instance (the UI layer instance, `eyes[2]` in
  `gs_pgs.cpp`, its own device) gets the same GIF stream as the two eye
  instances. Per primitive of the scene target, `GSInterface::ui_classify`
  decides scene or UI (below); the eye instances skip UI primitives
  (`Hacks::ui_layer = 1`), the UI instance skips scene primitives
  (`ui_layer = 2`) and everything that does not draw into the scene target.
  Split only while the frame is a stereo frame (`ga2_stereo::building().active`
  at the vsync that precedes it); frames outside 3D scenes keep their UI in
  the picture (theater mode and movies are unchanged). A frame carries a flag
  saying it was split, so the panel and the baked UI never show together.
- The scene target is learned, not configured: the discs copy their finished
  32-bit render target to the 16-bit scanout buffer with textured sprites
  (path 3, dithered); the copy's source buffer is the scene target. The copy
  also marks the frame boundary: the first sprite of a copy latches the split
  flag of the frame behind it and, in the UI instance, scans the target out
  (`GSRenderer::scan_out_buffer`: RGBA, alpha kept, no CRTC; the region the
  copy reads, 640x480 = the 4:3 picture before the copy squeezes it to the 448
  display lines). So a UI image belongs to the frame the copy puts on screen,
  the same one the eye instances scan out at the next vsync.
- The UI instance runs at its own super-sampling (`GA2_VR_HUD_SSAA`, default
  1: the panel image is the 640x480 target; 4 scans out at 2x, 1280x960,
  smoother edges when the panel is magnified, see Cost). Its target is cleared
  by the game's own frame clear, whose colour it zeroes (transparent black).
- Alpha: the discs mask the target's alpha when they draw (`FBMSK`
  0xff000000), the UI instance unmasks it. Blended draws would overwrite the
  alpha of what is under them (a glyph cell with transparent texels punched
  holes in the dialogue box), so the UI instance's ubershader accumulates
  coverage for "source over" blends (`BLEND_MODE_UI_ALPHA_BIT`,
  `ui_layer_alpha`: a' = a_s + a_d (1 - a_s), the colour is premultiplied by
  the same blend); other blends (additive) leave the coverage alone, so light
  adds over the scene. The shader bank was regenerated (`gs/slangmosh.sh`;
  regenerating the old shaders reproduces the committed bank byte for byte).
- `ga2_vr.cpp` draws each eye's scene image as before, then the panel (shader
  `kHudFs`: alpha 0x80 = 1.0, premultiplied blend) as a quad seen from the pose
  and field of view that eye's frame is submitted with, then the hands. The
  panel has the angular size of the theater screen at its distance (4:3),
  1.6 m (`GA2_VR_HUD_DISTANCE`) straight ahead of the recentred origin, centre
  0.1 m below eye level (`GA2_VR_HUD_HEIGHT`), fixed in the play space like the
  theater screen (`GA2_VR_HUD_LOCK=head`: in front of the head pose the frame
  was rendered with); `GA2_VR_HUD_SIZE` scales it, `GA2_VR_HUD_GAIN` the
  brightness.

UI classification (`ui_classify`, per primitive of the scene target):
- Path 1 (VU1 XGKICK, 3D): scene, except screen-space geometry: Q == 1 on
  every vertex and no depth test (ZTE off or ZTST ALWAYS), not spanning the
  target (the radar's blips and sweep, Zettai battle).
- Paths 2 and 3: UI, except primitives whose bounding box spans 90 % of the
  scissor width and height (textured: the glow pass's copy back, backdrops and
  full-screen fades stay in the scene; untextured and not blended: the frame
  clear, which the UI instance keeps and zeroes), and untextured, unblended
  primitives that span the width or the height (the clear's 64-pixel strips,
  letterbox bars).
- Eigou 2 narration (frames with the dialogue box): per frame 3D (~5 k
  triangles) on path 1, the clear and ~30 UI sprites on path 2, the copy on
  path 3; the UI layer has the dialogue box, text, the Auto/Back/Menu buttons
  and the name gems. Zettai battle: radar, communication window, unit panel,
  reticle ring and gauges on path 2; the radar blips on path 1.

Diagnostics: `GA2_VR_HUD_DEBUG=1` runs the scene and UI instances without a
headset (always split) and `GA2_VR_HUD_DUMP=<prefix>` writes
`<prefix>-scene.png`, `-ui.png`, `-comp.png` (`_DUMP_AT`, `_DUMP_COUNT`,
`_DUMP_GAP`); `GA2_PERF=1` adds a `[pgs] hud:` line (UI snapshots per 5 s,
primitives by path and class); `GA2_VR_EYE_SIZE=<w>x<h>` sets the swapchain
size (Monado's simulated headset recommends 320x240).

Cost (Eigou 2 narration in the simulated headset, `[pgs]` lines of
`GA2_PERF=1`, system otherwise idle; the VU1-heavy scene of that disc runs at
about half speed with or without the panel, the numbers are EE-thread
milliseconds per guest vsync): panel off gif 1.06, vsync 0.32 ms; panel on
(default SSAA 1) gif 1.28, vsync 0.39 ms, game speed unchanged (82-83 % vs
82-85 % on the same scene); `GA2_VR_HUD_SSAA=4` gif 1.78, vsync 0.57 ms. The
snapshot (render pass flush, a scan-out image, submit) and its readback are
what a super-sampled instance pays for; nothing waits for the GPU. One more
Vulkan device means more GPU memory (a whole VR session with the panel peaked at
3.9 GB on the 4090; two runs that had only ~3 GB free, another job holding
18-21 GB, died in the NVIDIA GL driver inside the HUD texture upload, probably
the memory; nine runs with a free GPU did not).

Limits: the 2D HUD is not pointable (the lasers do not hit the panel);
fades dim only the scene (the full-screen fade is in the scene, the panel
stays at full brightness); the mouse/picture probe sees the scene image
without the overlay in split frames; a UI primitive that only samples a
render-to-texture target would lose it (none seen); the classifier is
evidence-based on Eigou 2 and Zettai only (Mugen 1/2, Eigou 1 use the same
2D path and entry points but were not run with the panel).

Earlier notes: [hor+ and frame rate](../rendering/horplus-and-framerate.md),
[2D entry points](../rendering/horplus-2d.md).

## Controller bindings (branch `vr-bind`, 2026-10-20)

Every PS2 input is an OpenXR action of its own with a plain name, so SteamVR's
binding UI lists them all and each can be rebound (`ga2_vr.cpp` creates them,
`ga2_vr_bindings.h` holds the default bindings per interaction profile,
`ga2_vr_input.h` turns the action states into the pad: pure, unit-tested in
`ps2xTest/src/ga2_vr_input_tests.cpp`).

What the games read (static analysis of Zettai, Mugen 1 and Eigou 1, the same
function shapes in Mugen 2 and Eigou 2; the pad word is the game's own
`data[2] << 8 | data[3]`, logical ids index the map at Zettai `0x2dd180`, Mugen
`0x2f99a0`):
- Menus, dialogue, status screens: D-pad / left stick (the game mirrors the left
  stick past half deflection into the D-pad ids), Circle confirm, Cross cancel,
  Start, Select (system menu), Triangle / Square in a few; in the dialogue Circle
  advances and **R1 held** (id `0x10`) reads through the text, Square / Cross are the
  Auto / Back (backlog) buttons. One menu uses all of L1 / R1 / L2 / R2 to change
  pages (Zettai `0x141df8`).
- Ship map (`0x1efc80`): D-pad or left stick scroll, R1 / R2 (raw masks) change the
  level, Circle selects. The cursor screens (`0x140ad8`) move a pointer with the
  D-pad (4 px per frame) or the left stick (analog).
- Battle, tactical map: D-pad or left stick pan (the stick is analog: `axes 6 / 7`
  through a dead zone of 0x28), **R1 / R2 analog pressure** (`pad bytes 17 / 19`) lower
  and raise the camera, Circle select / execute, Cross cancel, Square order all,
  Triangle H-Weapon, **L3** pilot (cockpit) view, **R3** unit camera, **L2 held** the
  portrait menu, Start pause, Select pause menu.
- Cockpit: left stick / D-pad move the reticle (analog), **Cross held** shoots
  (lock-on markers every 0.05 s), **Circle held and released** fires the charged shot,
  Triangle / Square the hyper weapon, L1 / R1 and Select, **L3** back to the map.
  Rear view: L3, Square, Start.
- Unit camera view: the **right stick** (`axes 4 / 5`) turns the camera, **R1 / R2
  pressure** zooms, R3 / Circle / Cross leave. This is the only place the right stick
  is read; face-button pressures are never read. L2 / R2 digital: only the portrait
  menu (L2) and the ship map and page menu (R2).
- Analog pressure is read for R1 and R2 only. The runtime used to hand the pad bytes
  `16..19` as L1, L2, R1, R2; libpad's order is L1, R1, L2, R2, so keyboard and
  gamepad R1 now reach the games' pressure reads, and the VR grips and right stick
  carry real analog pressure.

Actions and their meaning (action name, label in the binding UI):

| action | label | PS2 |
| --- | --- | --- |
| `aim` | Aim (pointer) | the laser (poses) |
| `trigger` | Point and click (trigger: confirm / fire) | mouse click on the theater screen; Circle in 3D scenes (below) |
| `circle` `cross` `square` `triangle` | Circle (confirm), Cross (cancel), Square, Triangle | face buttons |
| `menu` | Menu (tap: Start, hold 0.6 s: Select) | Start / Select |
| `start` `select` | Start, Select (system menu) | direct |
| `l1` `r1` `l2` `r2` | L1 R1 L2 R2 | shoulders, analog (pressure) |
| `l3` `r3` | L3 / R3 (stick click) | stick clicks; both = cheat menu |
| `dpad` | D-pad | D-pad (press at 0.55, release at 0.35) |
| `left_stick` `right_stick` | Left / Right analog stick | analog bytes; the right stick pushed left / right is also L2 / R2 |
| `face_pad` `face_pad_click` | Face pad position / click | click = Circle (right), Cross (down), Square (left), Triangle (up) |

Default bindings (grips: R1 / L1 analog above 0.6 down, release 0.4):

| controller (profile) | face | menu / Start, Select | sticks | grips, triggers | stick clicks |
| --- | --- | --- | --- | --- | --- |
| Quest, Rift (`oculus/touch_controller`), Touch Pro (`facebook/touch_controller_pro`), Touch Plus (`meta/touch_controller_plus`), Pico 4 / Neo 3 (`bytedance/*`) | right A = Circle, right B = Cross, left X = Square, left Y = Triangle | left menu = menu action | left stick = left analog stick + D-pad, right stick = right analog stick (sideways: L2 / R2) | grips = L1 / R1, triggers = point / click / fire | L3 / R3 |
| HP Reverb G2 (`hp/mixed_reality_controller`) | as Touch | both menu buttons = menu action | as Touch | as Touch | L3 / R3 |
| Index (`valve/index_controller`) | right A = Circle, right B = Cross, left A = Square, left B = Triangle | left trackpad hard press = menu action, right trackpad hard press = Select | as Touch | as Touch | L3 / R3 |
| Windows Mixed Reality, Odyssey (`microsoft/motion_controller`, `samsung/odyssey_controller`) | no face buttons: right trackpad click by direction | both menu buttons = menu action | as Touch | squeeze = L1 / R1, triggers | L3 / R3 |
| Vive wands (`htc/vive_controller`) | right trackpad click by direction (right = Circle, down = Cross, left = Square, up = Triangle) | left menu = menu action | left trackpad = left stick + D-pad | grips = L1 / R1, triggers | left trackpad click = L3, right menu = R3; no R-stick, no L2 / R2 |
| other / simple (`khr/simple_controller`) | left select = trigger, right menu = Cross | left menu = menu action | none (no D-pad) | select = trigger | none |

Profiles that need an instance extension (Touch Pro / Plus, Pico, HP, Odyssey)
are suggested only when the runtime offers it; the list is checked against each
profile's path list in the unit tests. Both grips held for a second recentre the
screen (neither L1 nor R1 is pressed meanwhile).

What the trigger presses depends on the scene (`ga2_vr_input.h` `Scene`):
- theater screen (2D scenes, menus, movies): the mouse click on what the laser
  points at, no pad button;
- 3D scene without a screen (Eigou narration over a 3D backdrop, the ship map,
  rear view) and the tactical map: Circle from the pointing hand (the one whose
  trigger was pulled last);
- cockpit: either trigger = Cross held (shoot);
- unit camera view: the triggers are L2 / R2 pressure (zoom) and the right stick
  stays a pure camera stick.
The tactical map is flagged by `ga2_vrpointer::wantTrigger`
(`native/compat/ga2_battlepointer.cpp`); the cockpit and the unit camera view by
`ga2_vrpointer::setScene(Cockpit / UnitCamera)`, called once per frame from the
views' update functions by `native/compat/ga2_vrscene.cpp` (only for `GA2_VR=1`):

| disc | cockpit view update | unit camera view update |
| --- | --- | --- |
| Zettai | `0x1a6db8` | `0x1aa6e0` |
| Mugen 1 | `0x1b4710` | `0x1b8888` |
| Mugen 2 | `0x1a8858` | `0x1ac180` |
| Eigou 1 | `0x1ba1c8` | `0x1be2f0` |
| Eigou 2 | `0x1b52a8` | `0x1b9420` |

(Mugen 2 by `sigmatch.py` from Zettai, the others by function size, the same
gating ids and the battle-re address table; all ten are in the generated function
tables.) When two notes overlap for half a second after a view change (the map's
and a view's) the newer wins.

Diagnostics: `GA2_VR_PAD_LOG=1` prints the pad state on every change and, once,
which actions the runtime bound to a controller (`[vr] bound actions: ...`);
`GA2_PAD_TRACE=1` prints what the game reads from the pad (buttons, sticks, shoulder
pressure); `GA2_VR_INPUT_SCRIPT=<file>` replaces the controllers by scripted input
(lines `<seconds> name=value ...`, names in `ga2_vr.cpp`), which is how the chain
action -> pad -> game was proved without hands: the trigger script advanced Eigou 2's
narration, a stick / shoulder script reached the game as pad bytes (pressure mode
`0x79`). Monado's qwerty devices need a window for keys and present as
`microsoft/motion_controller` here, so button presses were scripted; the runtime
accepted all 11 suggested profiles and bound `trigger`, `menu`, `l1 r1 l3 r3`,
`dpad` and both sticks, `face_pad` (+ click) of the WMR profile.

Scene hooks, verified (Zettai, `research/vr/zettai-scene/`): with `GA2_VRSCENE_REQUEST` forcing the
cockpit the log shows `[ga2 vrscene] zettai: cockpit view`, the scripted right trigger is `[vr] pad
buttons=4000` (Cross) and the game reads `[pad] buttons=0xbfff` (Cross held); the story's battle tutorial
prompt keeps the battle paused, so no shots show in the eye dumps. The forced unit camera view (mode 3 without
a watched unit) ran its hook once and then stopped updating, so L2 / R2 there is covered by unit tests only.
Mugen 1 was built and its hooks bound (`[ga2 vrscene] mugen-1: ... hooks bound`); the other discs' ten
addresses exist in their generated function tables but those binaries were not built.
A named scene counts even when the picture is not a stereo frame (a paused 3D scene shows the theater screen).

Test rig note: `research/vr/run_vr*.sh` sets `XDG_RUNTIME_DIR` to a private directory for
Monado's socket, which also hides PulseAudio ("AUDIO: Failed to initialize playback
device"). Without audio the voice-timed Auto dialogue crawls (Zettai's pre-battle
briefing took twice as long), so a run that must reach a battle needs
`PULSE_SERVER=unix:/run/user/<uid>/pulse/native` and a null sink with the game's streams
moved onto it (as `tools/run_owned.py` does). The Zettai battle starts in the cockpit
view (with the tutorial prompt), so the cockpit hook is exercised right at its start.

Cheat menu in the headset: both stick clicks (L3 + R3) open and close it, as on a
gamepad. It is drawn by `ga2_cheat_menu` into a 1280x960 texture (straight alpha)
that `ga2_vr.cpp` shows on the theater screen (a 4:3 quad just in front of it) or,
in stereo scenes, on a panel 0.9 m in front of the head (0.8 m tall, head-locked so
it is always in view; mip-mapped so the text stays readable on small eye images).
While it is open the game gets an idle pad; D-pad (left stick) up / down move,
Circle (A, or the trigger, which is Circle there) chooses, Cross (B) closes, and
pointer clicks go nowhere. Evidence: eye dumps in both eyes on the theater screen
and in a stereo scene, `research/vr/cheat-menu/`.

Limits: nothing was tried on a headset (Index trackpad hard press and WMR trackpad
click thresholds are the runtime's).

Scripted runs (test aids): the clock of `GA2_VR_INPUT_SCRIPT`, `GA2_VR_DUMP_AT` (a comma
list; `-<n>` in the file names) and `GA2_VRSCENE_REQUEST` is game time (guest vblanks /
59.94, `ga2_activity::gameSeconds()`), so a slow or loaded host does not shift them. While
a script runs the lasers point at nothing and the window's own mouse stays out of the
menus (a headless desktop wiggles it and moved the title cursor). `GA2_VRSCENE_REQUEST=
"2@600,3@650,0@700"` asks the battle manager for view mode 2 (cockpit), 3 (unit camera),
0 (tactical map) at those game seconds, for runs where the tutorial does not yet allow
L3 / R3.

## Summary

- **Frame structure.** The whole 3D world is drawn inside one function,
  `RenderMgr::Render` (Zettai `0x117be0`). It renders a list of draw objects
  (2D layers, 3D scenes, effects) in priority order. Each 3D draw object
  applies its own camera while it renders (`CCamera::Apply` `0x118d98`).
  Animation and hierarchy updates (`AxlAnimUpdate`, `AxlClumpUpdateFrame`)
  and camera list updates run in `Update`, not in `Render`.
- **Rendering again does not advance the game.** A render-only iteration
  (main-loop `Update` skipped) builds the same 3D packet list as the
  iteration that updated the game. The differences are only a texture upload
  (VRAM residency) and the camera selection, which must be reset so the
  camera is applied again.
- **Prototype works.** The game's own 3D world, rendered from two camera
  positions 6.4 game units apart, gives a correct stereo pair on the ship map.
  The 2D UI stays put. Image: `zmap-stereo-pair.png`.
- **Proposed design: frame-sequential passes.** Run the game's own main loop
  three times per vsync: eye L (with Update), eye R and the UI, the last two
  without Update. The vsync wait and flip happen only on the last pass. The
  runtime captures each pass's framebuffer.
  - Per-eye views and asymmetric projections go in through
    `CCamera::Apply`/`ApplyProjection`.
  - The runtime's existing scanout and readback path takes the images with
    DISPFB pointed at the pass's draw buffer.
  - Each hook is a single pre or post action, so it survives EE scheduler
    preemption, which a host loop that calls `Render` twice does not.
- **Cost.** VU1 work doubles. The 3D scenes are already VU1-bound: the ship
  map runs at ~72% and Eigou 2's narration at ~41 vsync/s. Full-rate stereo
  needs the planned block-level VU1 compiler (about 2-3x). Until then,
  alternate eyes (one eye per vsync), which costs the same as mono.

## 1. Frame structure (measured)

Main loop: `main` (`0x100258`) repeats `Update` (`0x113210`) then
`RenderFlip` (`0x112dc0`). One ship-map frame (`ct-map.txt`; ~290 world
matrix sets omitted):

```
RenderFlip
  0x229640 frame begin (packet list header, draw env), 0x2295a0 clear (64-px strips)
  AppRender 0x13a220 -> CFramework::Render 0x113b98 -> RenderMgr 0x117be0(framework+4)
      rebuild opaque/translucent lists from the draw-object pool (+0x2c),
      depth per object from the view matrix 0x2cefe0 (0x118720), FBMSK 0x226470
      item: 2D background layer      -> 2 sprites (0x2333a0 emit)
      item: ship map 3D (0x1f4a38)
            camera select 0x119478 -> CCamera::Apply 0x118d98
                -> view calc (vtable+0x1c, 0x1f0828 -> 0x119068)
                -> ApplyProjection 0x118e78 -> MatSet(0, cam+0x20) 0x22fc88 (VU1 constants)
                -> lights MatSet(2/3) 0x1188c0
            frustum 0x1045d8, ~145 x [MatSet(1, world) + atomic draw 0x230630]
      item: glow post-process (0x1e5560; FBMSK changes, 256x256 targets)
      item: 2D UI layer (0x11ce18)   -> 9 sprites
  scene render (vtable+0x54), fade overlay 0x128a10
  list close 0x224008, draw-buffer swap 0x228760, wait DMA idle 0x224758,
  WaitVSync 0x237748, flip 0x2285e0 (DISPFB), kick 0x224108
Update 0x113210 -> app update 0x13a1c0 -> scene update; camera lists 0x1193a0 (reset selection)
```

Eigou 2 narration (`ct-e2.txt`): `RenderMgr` (`0x117cb8` on Mugen/Eigou)
renders two 3D draw objects. Each selects and applies the camera (camera at
z = 2000 from the origin), then `AxlClumpRender` with 11 `AxlAtomicRender`
calls. One orthographic draw (`0x11c2f8`) follows, then the fade.
`AxlClumpUpdateFrame` runs in `Update`; `AxlAnimUpdate` never runs in
`Render`.

Packet lists: the map frame's list is ~39 KB. Geometry is referenced by DMA
REF tags, not copied, and the pool is 3 MB (`0x223f40(0x4e92c0, 0x300000)`),
so three passes fit easily.

### Rendering again without advancing state (verified)

`gdb_stereo_proto.py` makes every second main-loop iteration render-only:
`Update` is skipped with a host-level `return`, which the caller treats as a
normal fallthrough. The packet list of every iteration is hashed with
list-relative pointers normalised (`st1.txt`, dumps in `lists/`). With the
same camera, an update iteration and the next render-only iteration differ
in only two ways:

- One 640x480 background texture upload (`BITBLTBUF 0x130a3660` + IMAGE).
  The texture manager uploads only what is not resident. This is benign.
- The camera block was missing: `0x119478` re-applies the camera only when
  the selected index changes, and the skipped `Update` (`0x1193a0`) is what
  resets it to -1. A render-only pass must set `camera_list+0x18 = -1`
  (camera list = `*(framework 0x2b6640)+0xc`).

The 3D part of the list (~37 KB) is identical. The game ran normally through
~430 such iterations.

## 2. Camera injection

- Game view `V` (world -> camera, row vectors `v' = v V`, translation in row
  3) is set by `MatSet(0, cam+0x20)` from `CCamera::Apply`. `cam+0x20` is
  recomputed every frame by the view calc, so writing it before the call is
  safe; the prototype does this. Camera axes: x right, y down (screen y
  grows downward), z forward (w = z).
- Eye view (row vectors, all 4x4 affine): `V_eye = V · C · S⁻¹ · inv(P_eye) · C · S`.
  - `P_eye` is the OpenXR eye pose relative to the recentred play-space
    origin, in metres.
  - `S = diag(s, s, s, 1)` scales metres to game units (`s` units per
    metre), so `S⁻¹` takes camera-space units to metres.
  - `C = diag(1, -1, -1)` converts OpenXR axes (y up, -z forward) to the
    camera's axes.
  - The game camera becomes the player's anchor. Head motion and the IPD
    come from `P_eye`.
- Per-eye asymmetric projection, from the OpenXR FOV tangents
  `tanL < 0 < tanR`, `tanD < 0 < tanU`, mapped to the full 640x448 buffer:
  - `scrz*ax = 640/(tanR - tanL)`
  - `cx = 2048 - 320 - tanL*scrz*ax`
  - `scrz*ay` and `cy` likewise, with 448 lines; y is flipped.

  Write these into `cam+0xbc/0xc0/0xc4/0xb4/0xb8` before `ApplyProjection`
  (`0x118e78`), or pre-hook the builder (`0x22ff20`; args `f12 scrz`,
  `f13 ax`, `f14 ay`, `f15 cx`, `f16 cy`). Keep hor+ off in VR (PAR native);
  the eye projection replaces it.
- Guard band: the clip volume `VC` is symmetric about the optical axis with
  half-extent `Ex/scrz` in tangent space (`0x230390` sets `Ex`, `Ey`). Choose
  `Ex` so that `cx ± ax*Ex` stays inside the GS range 0..4095. For example,
  `Ex = min(cx, 4095 - cx)/ax - 16` with the same rule for y.
- Culling:
  - VU1 culls and clips against each eye's clip volume, so that is correct.
  - The EE frustum test (`0x104728`) is built from the game camera (battle
    `0x1616c8`/`0x161890`, map `0x1f4a38`). In VR, make it always pass
    (override to return 1), or the world behind the game camera is missing
    when the player turns around.
  - Some scenes simply do not contain geometry outside the authored view.
    That is content, not a bug.
- World scale, from the prototype disparity: ±3.2 units gave ±60 px at
  `scrz*ax = 412.7`, so the map plane is ~22 units from the camera and the
  visible map is ~35-40 units wide.
  - Ship map: **30 units/m**. The map becomes a ~1.2 m diorama viewed from
    ~0.75 m; IPD 64 mm = 1.9 units; near plane 1 unit = 3 cm.
  - Space narration: the camera sits at 2000 units, so depth is mostly
    background. 100 units/m puts the nebula about 20 m away.
  - Battles: not measured; make the scale a per-scene setting.

## 3. Getting each eye's image out

The game double-buffers the framebuffer (`0x228760` toggles the draw buffer
`0x7f96c8`; FBPs at display info `+0x20` and `+0x24`). There is no VRAM room
for two extra 640x448 targets, so each pass is drawn into the normal draw
buffer and captured before the next pass is kicked:

- Iteration order: build the list, close it, swap, wait for the previous DMA,
  then kick this list at the end (`0x224108`). When `0x224108` is entered in
  iteration k+1, list k has executed and list k+1 has not been submitted.
  Pre-hook `0x224108`: capture list k's buffer, then let the kick go ahead.
- Capture: `gs_pgs.cpp` `vsync()` already builds a scanout from DISPFB/DISPLAY
  and reads it back asynchronously (three-slot ring). Add
  `ga2_pgs::capture(eye, fbp, fbw, psm, 640x448)`: the same scanout with
  DISPFB pointed at the pass's buffer, tagged L/R/UI, keeping GPU order (after
  list k, before list k+1). `ga2_vr` then renders each eye's texture as a
  full-view quad in that eye's projection, plus the UI image on a floating
  panel. The theater mode already draws one texture with OpenGL.
- Passes per vsync:
  1. **Eye L.** `Update` runs. 2D emits are suppressed (pre-hook the emit
     wrappers sprite `0x233380`, flat `0x2334c0`, quads `0x232aa0`,
     `0x232cf0`, `0x232f10`), except full-width flat fills (fades). Skip
     `WaitVSync` (`0x237748`) and the flip (`0x2285e0`).
  2. **Eye R.** `Update` is skipped (override returns without calling the
     original), the camera selection is reset, and filtering is as in pass 1.
  3. **UI.** `Update` is skipped. The 3D is suppressed: skip
     `AxlClumpRender` `0x239450` and `AxlAtomicRender` `0x239e50`, plus the
     glow item, or skip the 3D draw objects in `RenderMgr`. Clear to alpha 0.
     `WaitVSync` and the flip run normally, so the desktop mirror shows the
     UI. The panel alpha comes from the sprites: GS sprites write source
     alpha even with blending. 3D passes mask alpha (FBMSK 0xff000000).
- The glow post-process belongs with the eye passes: it reads that pass's
  frame. The fade overlay (`0x128a10`) runs in all passes.
- Why iterations and not a loop inside `Render`: `dispatchGuestBranch`
  returns false at EE scheduler safe points and unwinds the host stack, and
  the guest resumes by pc. A host loop calling `RenderMgr` several times
  would be abandoned whenever a pass is preempted. Each per-iteration hook is
  one pre or post action and resumes correctly.
- Timing: `Update` runs once per vsync with its native `dt`, so game speed
  is unchanged. `RenderFlip`'s measured frame count includes all three passes
  (more than one vsync means `dt` = 2 frames in scenes with maxFrames > 1;
  most scenes clamp to 1).

## 4. Cost and feasibility

- VU1: eye passes double it; the UI pass has almost no VU1 work.
  - Ship map: ~16 ms of VU1 per frame natively, so ~32 ms in stereo, about
    30 fps.
  - Eigou 2 narration: ~21 ms (2.1M VU cycles at ~10 ns), so ~42 ms in
    stereo.
  - Full-rate stereo needs the block-level VU1 compiler (HANDOFF item 25).
- EE: three packet builds per vsync (the game's render code: a few ms).
- GS: paraLLEl-GS cost scales with passes; small at 640x448, more with SSAA.
- Fallback with no extra cost: alternate eyes (L on even vsyncs, R on odd),
  each updated at 30 Hz with the head pose sampled per pass, and
  OpenXR reprojection in between. This works for the slow scenes (narration,
  ship map); battles will show eye-to-eye motion lag.
- Resolution: 640x448 per eye (anamorphic over the eye FOV) is low.
  paraLLEl-GS SSAA/high-resolution scanout (×2/×4) applies per pass.

## 5. Prototype (gdb, stock binary)

`gdb_stereo_proto.py` on the Zettai ship map (fixture card). From 75 s on:
- even iterations run `Update` and render eye L;
- odd iterations skip `Update`, reset the camera selection and render eye R;
- the view matrix passed to `MatSet(0)` from `CCamera::Apply` (return
  address `0x118dd0`) is offset by ∓3.2 units along camera x.

Result (`st3`):
- 163 L and 162 R view injections;
- every screenshot is one of two images, shifted by +56 and -56 px against
  the native view (597-px-wide window);
- the 3D map shifts, and the 2D UI (preview panel, block list, cursor ring)
  does not;
- `zmap-stereo-pair.png`: L, R, red/cyan anaglyph, native.

Not done: a real headset, both eyes captured from one game frame (needs the
runtime capture call), and battles.

## 6. Addresses

| Function | zettai | mugen-1 | mugen-2 | eigou-1 | eigou-2 |
| --- | --- | --- | --- | --- | --- |
| main-loop Update (skip on R/UI passes) | 0x113210 | 0x1132c0 | 0x113290 | 0x1132c0 | 0x1132c0 |
| RenderFlip | 0x112dc0 | 0x112e50 | 0x112e40 | 0x112e50 | 0x112e50 |
| app Render | 0x13a220 | 0x13adc0 | 0x13aa48 | 0x13af20 | 0x13bad8 |
| CFramework::Render | 0x113b98 | 0x113c48 | 0x113c18 | 0x113c48 | 0x113c48 |
| RenderMgr::Render | 0x117be0 | 0x117cb8 | 0x117c60 | 0x117cb8 | 0x117cb8 |
| camera select (re-applies on index change) | 0x119478 | 0x1195d0 | 0x1194f8 | 0x1195d0 | 0x1195d0 |
| camera list update (resets index) | 0x1193a0 | 0x1194f8 | 0x119420 | 0x1194f8 | 0x1194f8 |
| CCamera::Apply | 0x118d98 | 0x118ef0 | 0x118e18 | 0x118ef0 | 0x118ef0 |
| CCamera::ApplyProjection | 0x118e78 | 0x118fd0 | 0x118ef8 | 0x118fd0 | 0x118fd0 |
| CCamera view calc | 0x119068 | 0x1191c0 | 0x1190e8 | 0x1191c0 | 0x1191c0 |
| matrix set (0 view, 1 world, 2/3 lights) | 0x22fc88 | 0x2496e0 | 0x231738 | 0x2563e0 | 0x24a750 |
| perspective builder | 0x22ff20 | 0x249980 | 0x2319d0 | 0x256680 | 0x24a9f0 |
| frame begin / clear | 0x229640 / 0x2295a0 | 0x243098 / 0x242ff8 | 0x22b0f0 / 0x22b050 | 0x24fd98 / 0x24fcf8 | 0x244108 / 0x244068 |
| list close / buffer swap / wait DMA | 0x224008 / 0x228760 / 0x224758 | 0x23da60 / 0x2421b8 / 0x23e1b0 | 0x225ab8 / 0x22a210 / 0x226208 | 0x24a760 / 0x24eeb8 / 0x24aeb0 | 0x23ead0 / 0x243228 / 0x23f220 |
| WaitVSync / flip / kick | 0x237748 / 0x2285e0 / 0x224108 | 0x2511a8 / 0x242038 / 0x23db60 | 0x2391f8 / 0x22a090 / 0x225bb8 | 0x25dea8 / 0x24ed38 / 0x24a860 | 0x252218 / 0x2430a8 / 0x23ebd0 |
| FBMSK / emit A+D register | 0x226470 / 0x225670 | 0x23fec8 / 0x23f0c8 | 0x227f20 / 0x227120 | 0x24cbc8 / 0x24bdc8 | 0x240f38 / 0x240138 |
| fade overlay | 0x128a10 | 0x129288 | 0x1290d0 | 0x1293e8 | 0x129288 |
| AxlClumpRender / AxlAtomicRender | 0x239450 / 0x239e50 | 0x252ed0 / 0x2538d0 | 0x23af00 / 0x23b900 | 0x25fbd0 / 0x2605d0 | 0x253f40 / 0x254940 |
| AxlClumpUpdateFrame / AxlAnimUpdate (Update only) | 0x2398b8 / 0x23b1b8 | 0x253338 / 0x254c38 | 0x23b368 / 0x23cc68 | 0x260038 / 0x261938 | 0x2543a8 / 0x255ca8 |
| sphere vs frustum (force 1 in VR) | 0x104728 | 0x104788 | 0x1047b0 | 0x104788 | 0x104788 |
| 2D emits: sprite / flat / quads | 0x233380 / 0x2334c0 / 0x232aa0, 0x232cf0, 0x232f10 | 0x24cde0 / 0x24cf20 / 0x24c500, 0x24c750, 0x24c970 | 0x234e30 / 0x234f70 / 0x234550, 0x2347a0, 0x2349c0 | 0x259ae0 / 0x259c20 / 0x259200, 0x259450, 0x259670 | 0x24de50 / 0x24df90 / 0x24d570, 0x24d7c0, 0x24d9e0 |
| framework singleton (camera list at +0xc, selection +0x18) | 0x2b6640 | derive from 0x113540 | derive from 0x113510 | derive from 0x113540 | derive from 0x113540 |

Globals per disc (VS, Ex, display info, vsync counter) are in the earlier
note's table. Matches are masked signatures of the Zettai function; for
`RenderMgr` and `MatSet` on Mugen 1 and Eigou (different code), the address
is the call target in `CFramework::Render`/`CCamera::Apply`.

## Risks

- Draw objects with render-time state: none were seen on the map or in the
  narration, but battle effects (`0x16d580`, beams) were not checked. Hash the
  3D part of the packet list per pass as a regression check.
- The texture manager uploads per pass whatever is not resident; that is
  wasted bandwidth, not wrong output.
- Scenes without a `CCamera` (orthographic `0x11c0e0`/`0x11c2f8` draws) stay
  flat; show them on the panel.
- Movies (IPU) and 2D-only scenes stay in theater mode; switch on the
  presence of a perspective camera apply in the frame.
- Comfort: the game camera still moves (narration dolly, battle cameras).
  Offer "anchor to the game camera" (default) and "fixed world" for static
  scenes.
- AGENTS.md: keep native timing; the three-pass design keeps one `Update`
  per vsync.
