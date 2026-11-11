# Modding the Galaxy Angel II PC port

Mods change the games without touching your disc images: replacement disc
files (scripts, data) and replacement textures (any resolution, so translated
or redrawn art can be sharper than the original when you play at 2x/4x).
Everything here works on all five discs.

## Where mods live

The launcher's data folder (`~/.local/share/ga2` on Linux, `%APPDATA%\ga2`
on Windows) has a `mods` folder. Each mod is a folder in it:

```
mods/
  my-mod/
    mod.json
    files/SLPM_662.43/...        disc files to replace or add (per disc program)
    textures/...                 texture replacements, any sub-folders
    font/metrics.bin             optional: Latin font metrics (see Scripts and text)
```

`mod.json`:

```json
{
  "name": "My mod",
  "version": "1.0",
  "author": "you",
  "description": "What it changes",
  "language": "en"
}
```

`language` is only for language packs (below); leave it out otherwise.

Disc programs: `SLPM_662.43` Zettai, `SLPM_667.79` / `SLPM_667.80` Mugen
discs 1/2, `SLPM_551.18` / `SLPM_551.19` Eigou discs 1/2.

The launcher's **Mods** screen (M) lists installed mods: Enter or a click
turns one on or off, `[` and `]` move it up or down (the top one wins when two
mods change the same file or texture). The list is saved in `mods.ini`.

Loose files put directly in `mods/<disc program>/` still replace disc files
too, below every mod.

A file named `NAME.ga2seg` stands for the disc file `NAME`, assembled from
pieces: bytes stored in the `.ga2seg` file and byte ranges of the (verified)
disc image. The build tools write `STRM.DAT.ga2seg` this way, so a mod that
rebuilds large archives ships only what changed (format in
`ps2xRuntime/src/lib/ps2_disc_mount.h`, writer `SegmentedFile` in
`tools/modding/ga2scripts.py`).

## Language packs

A mod with `"language": "en"` (or any language code) is a language pack: it
is used exactly when that language is chosen in Options > Language, and is not
switched on by hand. Language packs are the way to ship a translation, with
both kinds of content:

- translated scripts as replacement disc files (`files/`),
- translated graphics and button labels as texture replacements
  (`textures/`), drawn at higher resolution if you like.

Switching the language in Options switches scripts and graphics together.
Other mods (HD art, fixes) stack on top of the language pack.

## Textures

### Finding a texture

Turn on Options > **Dump textures (modding)** and play: every texture the
game shows is saved once to `<data>/dumps/textures/` as
`<hash>_<width>x<height>.png` (alpha 255 = opaque). The name identifies
exactly that image (the hash is of its pixels), so the same picture always
gets the same name. Titles, labels, character sprites, eyes and mouths,
backgrounds and UI parts are separate files. Textures the game renders
itself (frame copies, post-process inputs) are not art and are not saved or
replaced.

### Replacing it

Copy the file into your mod's `textures/` folder (keep the name, any
sub-folder is fine) and edit it:

- Any resolution with the same shape: draw it 2x, 4x, 8x larger for a sharper
  result at higher internal resolutions; it still works at native resolution.
- Keep transparency where the original has it.

Replacements are loaded when the game first draws the texture.

## Scripts and text

The story scripts are bytecode files inside the discs' `ALL.DAT`. The tools in
`tools/modding/` (Python 3, no extra packages) turn them into editable text and
back; see [modding/scripts.md](modding/scripts.md):

```
python3 tools/modding/ga2scripts.py extract <disc.iso> <dump-dir>
# edit the .gasm files (the .string lines are the text, in story order)
python3 tools/modding/ga2scripts.py build <disc.iso> <dump-dir> <mod>/files --target strm --wrap 372
```

`--target strm` writes a small `IDX.DAT` + `STRM.DAT` pair for the mod's
`files/<disc program>/` folder (about 200 KB); `--target all` rebuilds the
whole `ALL.DAT`. `ga2scripts.py verify` proves an unedited dump rebuilds the
original bytes.

Translating:

- Write English in the `.string` lines as plain paragraphs and build with
  `--wrap 372`: the tool breaks Latin message text at spaces to fit the
  dialogue box (3 lines), measuring with the game's own half-width font
  metrics, and warns about messages that are too long. Half-width Latin
  text is drawn proportionally by the games themselves; see
  [modding/text.md](modding/text.md) (font, metrics, an optional metrics hook).
- Tighter or redrawn Latin letters: a mod can ship its own font metrics
  as `font/metrics.bin` (the highest-priority active mod's is used). Make it
  from a metrics file (`ga2font.py dump` or `tight` writes one; edit the
  `[u, v, width]` cells) with
  `python3 tools/modding/ga2font.py pack metrics.json <mod>/font/metrics.bin`,
  and wrap with the same metrics (`--font-metrics metrics.json`). A redrawn
  font image goes in `textures/` like any other texture, at any resolution,
  with each letter inside its cell.
- A translation workbook (one .xlsx with every line of all three games,
  speaker tags, scene pictures, and an English column that is imported back
  into a language pack) is made by `tools/modding/script_xlsx.py`; see
  [modding/translation.md](modding/translation.md).
- Character names come from `adv/charNNN.tbl`, UI text from `common/*.tbl`.
  The game reads these tables from load groups (FSTS packs in ADV.DAT and
  SLG.DAT), not from ALL.DAT: `ga2scripts.py build --extra <dir>` takes
  replacement files by archive path (`<dir>/adv/char002.tbl`), rebuilds every
  pack that holds them into the mod's STRM.DAT and repoints the groups in
  IDX.DAT. The translation workbook imports names this way. Text inside
  pictures is replaced through `textures/`
  (`tools/modding/text_textures.py`).

New scenes: a `.gasm` file with a new name is added to the disc index by
`build` (see [modding/scripts.md](modding/scripts.md)); call it from an
existing scene with `CALLG`/`JMPG`. Engine calls are written by name in the
dumps (`say`, `msg`, `bg`, `char_left`, `bgm`, `se`, `color_out`, `wait`,
...); about half are named so far.

## Button prompts

The port replaces the games' low-resolution button icons with its own
high-resolution ones for the device you play with (Options > Button
prompts): PlayStation, Xbox-style, keyboard or VR. The icons are in
`buttons/<style>/<button>.png` next to the engines (256x256, the port's own
art, made by `tools/assets/make_button_icons.py`), and
`buttons/map.json` lists where the games' textures show which button:

```json
{"textures": {"<hash>": [{"button": "triangle", "x": 4, "y": 2, "w": 16, "h": 16}]}}
```

`hash` is the texture hash from a dump file name; `x`, `y`, `w`, `h` are the
icon's place in that texture. The port covers the PS2 icon with the colour
around it and draws the style's icon there, fitted to the rectangle. Buttons
are `cross`, `circle`, `square`, `triangle`, `l1`, `l2`, `r1`, `r2`,
`select`, `start`, `up`, `down`, `left`, `right`. For a rectangle at least
1.5 times wider than tall the engine uses `<button>_wide.png` when the style
has one (keyboard keys for the pill-shaped prompts). The button
`label:<name>` draws the word `buttons/labels/<name>.png` instead, in every
style (the dialogue box's Auto, Back and Menu).

To find a prompt that still shows the PlayStation icon: run with
`GA2_TEX_DUMP=<dir> GA2_BUTTONS=keyboard` (`xbox` and `vr` work too, and
`GA2_BUTTON_DIR=<folder>` tests another icon folder), reach the screen, find the
texture with the icon in the dump and add its hash and the icon's rectangle
(the whole round button, not only the symbol).

Prompts that are *text* in the games' script, battle tutorials and executable
(a button name inside a sentence) cannot be mapped to a texture: a language
pack writes a placeholder there instead (`{circle}`, `{cross}`, `{l1}`, ...;
the list and rules are in [modding/translation.md](modding/translation.md#button-placeholders-in-text)).
The build turns it into reserved characters, and the engine draws the icon of
the current input device in their place, at the text's size, switching live when
the player changes device. The same icon files are used (`l3`, `r3`, `dpad`,
`lstick`, `rstick` are text-only), plus a `playstation/` set for PlayStation
pads. `tools/modding/make_text_icons.py icons native/buttons` draws any
missing icon. `GA2_NO_TEXT_ICONS=1` turns the replacement off (the PlayStation
symbols then show in the text colour).

## Space skybox

The VN side fill and VR use each game's own space sky, rendered from the disc
by `tools/assets/extract_skybox.py` when a disc is imported (needs Python 3
with Pillow); see [assets/skybox.md](assets/skybox.md). The panoramas are in
`<data>/assets/skybox/<disc program>.png`; replace one to use another sky.
