# Event CGs, character sprites and other 2D pictures

`tools/assets/dump_images.py` decodes the 2D pictures of all five discs to PNG.
It reads the player's ISO images. No game data is stored in this repository.
It needs only the Python standard library and Pillow, and it reuses the disc and
archive readers of `tools/assets/extract_skybox.py`.

```bash
python3 tools/assets/dump_images.py <private> [--game zettai|mugen|eigou] [--all] [--jobs N] [--no-contact]
```

`<private>/discs/<disc>/*.iso` must exist for the disc ids `zettai`, `mugen-1`,
`mugen-2`, `eigou-1` and `eigou-2`. The tool writes
`<private>/research/assets-dump/<game>/` and replaces anything already there.
The two discs of a game are merged into one folder. A full run takes about 3
minutes with 6 jobs and writes about 2.1 GB.

## Output

| Path | Content |
| --- | --- |
| `cg/<name>.png` | event CGs, `adv/bg/iv*.agi` (640x480) |
| `sprites/<Character>/<file>.png` | standing portrait (`adv/face/chaNNN_xxxx.fac`), body layer |
| `sprites/<Character>/parts/<file>_eye<k>.png`, `_mouth<k>.png` | blink and lip-sync frames of that portrait |
| `sprites/<Character>/faces/facNNN_xxxx.png` | 88x88 message-window face icons |
| `sprites/<Character>/placeholders/` | 3-digit files (`cha001_000.fac`, `fac001_000.agi`): early line art and name cards that no table uses |
| `sprites/<Character>/sprites.json` | per portrait: source file, disc, expression label, frame counts, layer offsets and PNGs |
| `sprites/names.json` | folder name -> Japanese `NAME00`, all `NAMEnn` and the character id on each disc |
| `sprites/unknown/` | files whose id has no character table |
| `backgrounds/` | ADV backgrounds `ad_bk_*` |
| `eyecatch/` | chapter eyecatches and title cards (`adv/eyecatch`) |
| `other/cg-thumbnails`, `bg-thumbnails`, `bg-names`, `movie-thumbnails`, `bg-misc`, `ui` | gallery thumbnails, place-name labels, `common/` interface pictures |
| `index.csv` | one row per archive file or layer: disc, archive path, archives holding it, output, size, GS format, sha256 of the RGBA pixels, character, label, note, discs |
| `summary.json` | counts and decode errors |
| `contact-cg.png`, `contact-sprites.png` | contact sheets of all CGs and of the first three portraits plus one face of each character |

`--all` also writes every other AGI texture (battle effects, ship and stage
textures, 3D model textures) to `other/<dir>/`.

**Deduplication.** Each image is hashed by its decoded pixels. One folder holds
each distinct image only once. A later copy, from the other disc, another
archive or another file name, gets an `index.csv` row with
`note = duplicate of <output>`. When the same file name has different pixels
on the two discs of a game, the second copy is written as
`<name>.<disc>.png` (for example `cg/iv00_07a.mugen-2.png`). The
`on_discs` column lists the discs whose data contains the image, or `all`.

**Labels.** The `label` column is the Japanese comment from the game's own
tables: `adv/bg.tbl` and `adv/evcg.tbl` for CGs and backgrounds, and
`[FACE_L]`/`[FACE_S]` of `adv/charNNN.tbl` for expressions (`通常` normal,
`笑い` laugh, and so on; `P1`... are costume variants, `C`/`CL` the Eigou
centre and left-shifted poses).

## Where the pictures are

All discs use the archives described in `docs/assets/skybox.md`: the `ALL.DAT`
PIDX tree, and FSTS packs in `ADV.DAT`, `ADVMAT.DAT` and `SLG.DAT`. Many
pictures are stored in several of them. The tool decodes one copy per distinct
stored size and records every archive and pack in `index.csv`.

* **CGs**: `adv/bg/iv<chapter>_<nn><variant>.agi`. `ivtn*` are the gallery
  thumbnails. `adv/evcg.tbl` builds the omake gallery from `adv/bg.tbl`
  indices (`[IMAGES]`, `[THUMBNAILS]`, `[BGID2FLAGID]`). `iv00_ed*` in Mugen
  and Eigou are the ending-credits design sketches. `iv00_00a` and Zettai's
  `iv01_00a` are captioned placeholders.
* **Portraits**: `adv/face/chaNNN_xxxx.fac`, where `NNN` is the character id
  and `xxxx` the expression (`[FACE_L]` in `adv/charNNN.tbl`).
* **Face icons**: `adv/face/facNNN_xxxx.agi` (`[FACE_S]`). Battle packs carry
  copies in `SLG.DAT` (`sound*` packs) and `common/`. Eigou adds `_e<k>` and
  `_m<k>` icon frames.

## Characters

The folder is the romanised `NAME00` of the `adv/charNNN.tbl` **on the disc
that holds the file**. Ids are not stable across discs. Eigou disc 1 has its
own numbering (036 is Keira there), while Eigou disc 2 uses the Mugen table
(036 is a shop clerk), so each disc is resolved against its own tables. Mugen
disc 2 is the Zettai Extra Route and uses Zettai's tables. A table can list
several names: `002` is カルーア/テキーラ (one body, two personas) and `045`
is ケルシー/ソルダム. `NAME00` names the folder and `names.json` lists the
others. Romanisation is the `ROMAJI` table in the tool. Official spellings are
used where known (Apricot, Kahlua, NanoNano, Lily, Anise, Milfeulle, Ranpha,
Tact). Other names are readings or English job titles (`CurryShopClerk`). Two
different ids with the same name on one disc get `_<id>` appended
(`LuxiolCrew_051`).

Ids without a table on their disc are named by hand (`MANUAL_NAMES`).
`cha077` on Mugen 1 and Eigou 2 is a name-card placeholder labelled
カトリーヌ (Catherine). `cha070` on Mugen 2 is Zettai's ディータ軍兵士.
`fac079_0000` (an empty placeholder face) stays in `unknown/`.

## Formats

**AGI** (header 0x30 bytes, or 0x20 for direct colour):

| Offset | Field |
| --- | --- |
| +0x04 | u16 1, u16 1 if a CLUT follows (0 for CT16) |
| +0x08 | pixel offset (0x30 / 0x20) |
| +0x0E | GS PSM: 0x13 PSMT8, 0x14 PSMT4, 0x02 PSMCT16 (0x00 PSMCT32 supported, not seen) |
| +0x18 | width, height |
| +0x1C | CLUT offset |
| +0x2C | CLUT width x height (16x16 for T8, 8x2 for T4) |

Pixels are linear, not GS-swizzled. T4 stores the low nibble first. CLUTs are
PSMCT32 with alpha 0x80 = opaque, and T8 CLUTs are in CSM1 order (swap index
bits 3 and 4). Alpha is written as `min(255, 2a)`, the same as the runtime
texture dumps. PSMCT16 (`R5 G5 B5 A1`) is used only by Zettai's seven
`adv/eyecatch/peye*.agi`. Those have no alpha bit set and are written opaque.
CGs, backgrounds and portraits are all PSMT8, and small labels are PSMT4. Some
`slg/` and `common/` entries are stored as RAIC streams with packed size =
size. The tool detects the magic and decompresses them.

**FACE** (`.fac`), current form:

| Offset | Field |
| --- | --- |
| 0x00 | `FACE`, u16 0x20 (header size) |
| 0x08 | u32 file size |
| 0x0C | u16 eye frames, u16 mouth frames |
| 0x20 | chunks: 4cc, u32 total size |

* `WIND` (Mugen/Eigou): 3 values, presumably the speech-window anchor (unused here).
* `POIN`: count and `(id, x, y)` anchor points (unused here).
* `IMAG`: `{u32 kind (0 body, 1 eye, 2 mouth), i32 x, i32 y, u32 0x20, u32 AGI size, 0}`, then an AGI.

The older 3-digit files have `FACE`, u16 eyes, u16 mouths, u32 anchor x and y,
then `1 + eyes + mouths` entries `{offset, size, x, y}` of embedded AGIs.

The body layer is already the complete default expression, with open eyes and
closed mouth: every eye and mouth frame differs from the body pixels under it.
The parts are animation frames drawn over the body at `(x, y)`, the blink
frames (`eye0`, `eye1`) and the lip-sync frames (`mouth0`...). Overlaying
frame 0 therefore gives a mid-blink, talking pose, not a "composed default", so
no composed images are written. `sprites.json` has the offsets to build any
frame. Portraits are 192-368 px wide (Zettai) and up to 184x712 (Eigou), with
transparent backgrounds.

## Verification

* Contact sheets for every game were checked by eye. CG colours, palettes and
  alpha are correct, with no swizzle artefacts.
* Byte-exact pixel matches against the runtime texture dumps
  (`<private>/research/buttons/dump-*`, captured from the GS while playing):
  Zettai matched 18 of 23 dumps of a comparable size (CG `iv01_07a`,
  backgrounds, eyecatches, Kazuya/Forte faces, place names; 5 were not found). Eigou matched 119 of 289 dumps, including
  46 eye/mouth frames, 17 portrait bodies and 9 CGs. Mugen matched 187 of
  1850. Its unmatched dumps are 3D, font and battle textures that the default
  run does not write.

## Limits

* Romanised names for minor characters are best guesses. `names.json` keeps
  the Japanese.
* Some table entries point at placeholder art that the disc really contains,
  for example Eigou 2's `cha063_0001` (Benedictine) is the カトリーヌ name card.
  Those files are kept as they are.
* Sprites are attributed by the id in the file name. Group ids such as 993-999
  (`全員`) have no real portraits except a test image.
* The 3D and battle textures (`--all`) are dumped raw and are not sorted.
  Movies (`STRM*.DAT`) are not touched.
