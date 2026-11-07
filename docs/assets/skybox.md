# Space skyboxes

The 3D space backgrounds of all three games (ADV narration and title
backgrounds, every space battle) are **sky models**: nested shells of
tri-strips around the model origin that carry tiled star textures,
vertex-coloured haze and clouds of textured nebula quads, drawn with additive
blending. There is no cubemap or panorama on the disc. A panorama has to be
rendered from the model, which is what `tools/assets/extract_skybox.py` does.
It reads the player's ISO (or a directory with the disc's files). No game data
is stored in this repository.

The tool needs only the Python standard library and Pillow.

```bash
python3 tools/assets/extract_skybox.py <disc.iso | disc dir> --list
python3 tools/assets/extract_skybox.py <disc.iso> -o <out> [--width 2048] [--cube 512] [--sky NAME]
python3 tools/assets/extract_skybox.py - --view <equirect.png> <yaw> <pitch> <fovv> <out.png>
```

Output, one folder per disc program (`SLPM_xxx.xx`):

| Path | Content |
| --- | --- |
| `<sky>/<sky>_equirect.png` | equirectangular panorama, `width x width/2` |
| `<sky>/<sky>_cube_{px,nx,py,ny,pz,nz}.png` | cube faces (`--cube` px, 0 = none) |
| `<sky>/<sky>.json` | layers (model, atom, texture, triangle count, radius), texture sources, ADV camera / blend entries, stages using the sky |
| `textures/<dir>/<name>.png` | decoded textures, alpha expanded 0..128 -> 0..255 |
| `backdrops/*.png` | the 2D ADV pictures listed as empty space in `adv/bg.tbl` |

The renderer is pure Python. One sky takes about 6 s (a simple battle sky) to
40 s (the densest nebula) at a width of 2048, and a whole disc takes 4-8
minutes. The output for the five discs is in `<private>/research/skybox/SLPM_*`
(2048x1024 panoramas, 512 px cube faces, `overview.png` contact sheet).

## Mapping

The game uses X right, Y **down** and Z forward (`bgmodel.tbl`: "the Y
direction is the opposite of LightWave's"). The ADV camera (`CAMERA01`, at
(0, 0, -2000), rotation 0, vertical FOV 80) looks along +Z.

* Equirect: column u in [0, 1) is longitude (u - 0.5) * 360 deg. u = 0.5 looks
  along game +Z (the ADV camera's view) and u = 0.75 along +X. Row v in
  [0, 1) is latitude (0.5 - v) * 180 deg, so the top row is game -Y (up).
* Cube faces use a Y-up right-handed frame: +x = game +X, +y = game -Y (up),
  +z = game -Z. The front face `nz` is the ADV camera's +Z view. Every face is
  the unmirrored view from inside the cube, as in "6-sided" skybox packs:

  | Face | Looks along (game) | Image right | Image up |
  | --- | --- | --- | --- |
  | `nz` (front) | +Z | +X | -Y |
  | `px` (right) | +X | -Z | -Y |
  | `pz` (back) | -Z | -X | -Y |
  | `nx` (left) | -X | +Z | -Y |
  | `py` (up) | -Y | +X | -Z (back) |
  | `ny` (down) | +Y | +X | +Z (front) |

  The faces join without seams in the cross `nx nz px pz` with `py` above and
  `ny` below `nz`. A raw OpenGL or Vulkan cube-map upload expects mirrored
  faces (the RenderMan convention), so flip them there or use a loader that
  takes inside-view faces. The equirect is the simplest input for most VR
  engines.
* The panorama is rendered from the model origin. Everything is in the
  model's rest pose: the frame hierarchy is applied, and the ADV `ROT_ANIM`
  spin (0.5-3 deg/s about one axis, from `bgmodel.tbl`) is not. A VR theatre
  can apply that spin itself to animate the sky.

## Where the skies are

All five discs use the same archive formats.

| Disc file | Format | Holds |
| --- | --- | --- |
| `ALL.DAT` | PIDX tree | `adv/*` tables (`adv/bgmodel.tbl`, `adv/bg.tbl`), `adv/bg/*.agi` 2D backgrounds, `se/*` |
| `ADV.DAT` | PIDX packs of FSTS | pack `advinit`: `common/model/op_sbg_*.axo` + their `si_*.agi` textures; pack `title`: title-screen copies |
| `SLG.DAT` | PIDX packs of FSTS | pack `slginit<stage>`: `slg/stage/<stage>/si_sbg_*.axo`, `.tbl` (`TEXDIR`) and textures |

ADV space backgrounds come from `adv/bgmodel.tbl`. Each `MODELnn` names an
AXO file, `BLEND` (0 normal, 1 additive, 2 subtractive), `CAMERA` and
`ROT_ANIM`, and the scene draws the models of one entry pair together. The
tool takes every additive `common/model` entry and groups the pairs by their
comment:

| Sky | bgmodel.tbl use | Discs |
| --- | --- | --- |
| `adv_op_sbg_0000+0001` | Zettai prologue/ending; Celdar space (Mugen, Eigou) | all |
| `adv_op_sbg_0002+0003` | prologue/ending (Mugen opening narration; also in the `title` pack of Mugen 1 and Eigou 2) | Mugen 1, Eigou 1, 2 |
| `adv_op_sbg_0004+0005` | Infinite Corridor capture | Mugen 1, Eigou 1, 2 |
| `adv_op_sbg_9998+9999` | Alliance space (Eigou disc 2 opening narration) | Eigou 2 |

Mugen disc 2 (Zettai Extra Route) uses Zettai's tables. On Eigou 2,
`op_sbg_9998.axo` and `op_sbg_9999.axo` are byte-identical and both are drawn,
so the sky is twice as bright as a single copy. This matches the game's
brightness; see Verification.

Battle skies come from each stage's `slg/stage/<stage>/stage.tbl`. Its `[BG]`
section ("celestial sphere file") lists animation tables, and each table gives
`TEXDIR` and `MODELFILE` entries. The tool draws every model of every `[BG]`
table of the stage together. Those models are `si_sbg_<n>.axo`, plus
`si_sor_0120.axo` (asteroids) on Eigou 1's stage 010100 and `si_trans_bg.axo`
on Eigou 1's stages 020100 and 130600. The `[GAS]`, `[METEORITE]` and
`[BGOBJECT]` sections place gas clouds and rocks in the battle field itself.
Those objects are not part of the sky and are not rendered.

Many stages share one sky. The tool removes duplicates by hashing the models
and textures, and the JSON lists every stage that uses each sky. Distinct
battle skies: Zettai 12, Mugen 1 10, Mugen 2 13 (Zettai Extra Route stages),
Eigou 1 14, Eigou 2 2. `--list` prints the exact set for a disc.

Not every stage is open space. Eigou 1's `slg_si_sbg_0710` is a saturated
rainbow sky (vertex-coloured haze shells), `slg_si_sbg_1110` has a structure
floor, and `slg_si_trans_bg` shows planets. These were decoded the same way as
the others but were not compared with game screenshots.

The ship map (`adv/model/ki_map_00x.axo`) is a holographic deck plan over a
circuit-board backdrop and has no space sky. The 2D space pictures in
`adv/bg.tbl` are `ad_bk_1047a`-`c` ("empty space", 640x480, on Mugen 1 and
Eigou 2). The tool
exports them to `backdrops/`. Zettai's `iv01_00a` ("generic space") has its
caption drawn into the image, so the tool skips it.

## Formats

**PIDX.** Header: `"PIDX"`, 0x30, then u32 values. At 0x0C is the record
table offset and at 0x10 the record count. At 0x20 and 0x24 are the string
table offset and size. A 16-byte archive id sits at 0x40.

* Tree variant (`ALL.DAT`, and the master `IDX.DAT`): 24-byte records from
  0x50.
  * Directory record: `(1, name, child count, first child)`.
  * File record: `(0, name, archive name, offset, size, packed size)`.
  Packed size 0 means the file is stored as is.
* Pack variant (`ADV.DAT`, `ADVMAT.DAT`, `SLG.DAT`): at 0x50 a count and
  offsets (relative to 0x50) to `(name, 0, offset, size, entry count)`.

**FSTS pack.** `"FSTS"`, count, entry table offset, string table offset and
size. Entries are `(name, offset, size, packed size)`, and names are full
virtual paths (`slg/stage/010100/si_sbg_0100.agi`).

**Compression.** An entry whose packed size differs from its size starts with
a magic XOR 0x72 and a u32 size in clear.
* `RAIC`: Okumura LZSS on the XORed payload (4 KiB window filled with zeros,
  start 0xFEE, flag byte LSB first with 1 = literal, reference = 12-bit
  position + 4-bit length + 3). This is the "ARZ" decompressor in HANDOFF
  item 11.
* `RAIB`: stored, XOR only. Zettai has these; the packed size is the size
  plus 8.

**AGI texture.** Header of 0x30 bytes:

| Offset | Field |
| --- | --- |
| +0x08 | pixel offset (0x30) |
| +0x0E | GS PSM: 0x00 CT32, 0x13 T8, 0x14 T4 |
| +0x18 | width, height (u16 each) |
| +0x1C | CLUT offset |
| +0x2C | CLUT width x height (8x2 or 16x16) |

Pixels are linear. T4 stores the low nibble first. CLUTs are PSMCT32, alpha
0x80 = opaque, and T8 CLUTs use CSM1 order, so swap index bits 3 and 4. The
sky textures (shared by name across games, but per pack, because stages carry
their own variants):

| Texture | Format | Use |
| --- | --- | --- |
| `si_sbg_0100`, `0102`, `0104` | 256x256 T4 | tiled star fields (UV repeat up to ~9x) |
| `si_sbg_0103`, `0105` | 256x256 T8, grey | nebula cloud sprites, tinted by vertex colour |
| `si_abg_0000` | 16x16 T4, white | haze shell; only the vertex colours matter |
| `si_sbo_0000` / `0100` | 32x32 / 64x64 T8 | bright star glow / planet |
| `si_sor_0100` | 128x128 T4 (SLG) or 256x256 T8 (ADV) | asteroid sprites |
| `si_sun_*`, `si_sbs_*` | 256x256 T8 | sun flare, special objects (alpha used) |

**AXO model.** A chunk list where each chunk is a 4cc, payload size, count
and record size. A sky model has the chunks `INFO`, `ATOM`, `FRAM`, `MTRL`,
`TEX `, `CBBX`, `GBBX`, `ALIN`, `GEOG` and `END `.
* `ATOM`: 40-byte records of tagged pairs (`PLGI`, `FRAM` frame id, `MTRL`
  index, `BBOX`, `GEOM` index), drawn in file order.
* `FRAM`: 60 bytes. id, two words, translation, rotation (radians, X then Y
  then Z), scale, parent id and two link words. The rotation order XYZ is
  confirmed by the screenshot fit below.
* `MTRL`: 68 bytes. Index, -1, RGBA 1.0, and the `TEX ` index at +60.
* `TEX `: 36-byte records holding an index and the texture name without
  extension. The texture is found in the model's directory, or in `TEXDIR` of
  the stage `.tbl`.
* `GEOG` holds one `GEOM` per geometry: a 32-byte header, then a DMA cnt
  chain of VIF code. The tool does not run VU1. It unpacks the VIF data the
  same way VIF would. One batch is `STCYCL 4,1` and:
  * `UNPACK V4-32 @0`: header `(GS PRIM = 0x5C, 0, 0, vertex count)`, where
    0x5C means triangle strip, Gouraud, textured, alpha blend.
  * `V4-32 @1`: positions.
  * `V4-16 @2`: normals, w = 0x1000.
  * `V2-32 @3`: ST/UV floats, perspective-correct.
  * `V4-8u @4`: vertex RGBA, 0x80 = 1.0.
  * `MSCAL`/`MSCNT`.
  Each batch is one strip.

**Drawing.** Every sky is additive (`BLEND = 1` in `bgmodel.tbl`; battle
skies render the same way). Each texel adds
`texture.rgb * texture.a/128 * vertex.rgb/128 * vertex.a/128` to the
framebuffer, clamped at 255, with texture repeat and bilinear filtering. Shell
radii range from about 1,300 to 70,000 units. The haze shells (white texture,
vertex alpha 0.2-0.3) give the sky its overall colour.

## Panorama assembly

The renderer traces the cone from the origin through each spherical triangle
for every pixel of the equirect panorama. Texture coordinates and vertex
colours are interpolated with the ray's barycentric weights, which matches
the GS's perspective-correct STQ. Atoms are accumulated additively in file
order, so draw order does not matter. Frame transforms are composed from the
root down. Cube faces and camera views are resampled bilinearly from the
panorama.

The panorama is faithful to the geometry seen from the origin. In the game the
ADV camera sits 2,000 units off-centre and the battle camera moves around the
field, so the nearer shells (radius about 1,300-25,000) show parallax that a
panorama cannot reproduce. For a VR theatre that is the right trade-off: the
viewer sits at the centre.

## Verification

The reference frames are game screenshots from the hidden-display runs
(`<private>/research/run55`, `run56`). For each comparison the panorama was
reprojected to a 640x448 4:3 pinhole view. The camera orientation, and in the
battle case the FOV, were fitted to the frame by correlating blurred
luminance, with the dialogue box and HUD masked. The fit scripts
(numpy, verification only), `run_verify.sh` and the side-by-side images
(`cmp_*.png`, game on the left) are in `<private>/research/skybox/verify/`.

| Scene (frame) | Sky | Fitted camera | Correlation |
| --- | --- | --- | --- |
| Mugen 1 opening narration (`run56/m2xmouse` 72 s) | `adv_op_sbg_0002+0003` | yaw -2, pitch -3, roll 0.3, FOV 80 (fixed) | 0.956 |
| same, 20 s later (92 s) | same | pitch -15 | 0.947 |
| Eigou 2 opening narration (`run55/eigou2chg` 112 s) | `adv_op_sbg_9998+9999` | yaw -9, pitch 0, roll 0.3, FOV 87 | 0.957 |
| Mugen 1 tutorial battle camera view (`run56/mtut` 392 s) | `slg_si_sbg_0110_010100` | yaw 128, pitch -44.75, roll 9, FOV 67 | 0.77 |

What the comparisons show:

* The fitted orientation of both ADV narrations is close to the predicted
  camera (+Z, no roll). This confirms the axes, the Y-down convention and the
  XYZ frame rotation order. Between 72 s and 92 s the sky turned 12 deg about
  X, matching `ROT_ANIM = 0.5,0,0` (0.5 deg/s, measured 0.6 deg/s from the
  capture timestamps).
* The colours and layout of the nebulae, stars and core flare match.
* Mean brightness of the panorama is 10-15% above the game's. That remaining
  gap could come from display output, dithering or the exact GS alpha
  equation. Drawing Eigou 2's sky once would leave it at about half the game's
  brightness, which is why both identical models are drawn.
* The battle frame matches on the bright star-glow constellation and the
  reddish haze along the bottom. Positions differ by a few pixels because the
  battle camera is not at the sky's centre (parallax on the 12,000-25,000
  glow shell).

To reproduce a view with the tool alone:

```bash
python3 tools/assets/extract_skybox.py - --view \
  <private>/research/skybox/SLPM_667.79/adv_op_sbg_0002+0003/adv_op_sbg_0002+0003_equirect.png \
  -2 -3 80 view.png        # compare with research/run56/m2xmouse/frame-00072.png
```

## Using the output

* **Ultrawide pillarbox.** Sample the equirect (or the `pz`/`nz` faces) at
  the angles the VN frame does not cover. For a static background, render a
  wide pinhole view with `--view` at a larger FOV. For motion, apply
  `ROT_ANIM` from the sky's JSON.
* **VR theatre.** Load the six cube faces, or the equirect, as the
  environment. The 2D screen sits in front of the viewer along the cube's -z
  axis (game +Z).
* Higher resolution: `--width 4096` (4x the render time). The source star
  textures are 256 px tiles repeated up to 9 times around the sphere, so
  widths above about 4096 add no detail.
