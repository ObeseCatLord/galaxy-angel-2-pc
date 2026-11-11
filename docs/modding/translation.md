# Translation workbook (all three games)

Status 2026-10-15. `tools/modding/script_xlsx.py` writes every story text
of the five discs into one spreadsheet for translating. It also feeds the
English column back into the script dumps and builds a language-pack mod.
It uses the Python standard library only: the .xlsx is written and read as
zip + XML, so neither openpyxl nor a spreadsheet program is needed.

The workbook contains game text, so it is kept in the private root only:
`<private>/research/translation/ga2_script.xlsx` (about 17 MB).

## Commands

```sh
# 1. (Re)create the workbook from the discs (about 30 s)
python3 tools/modding/script_xlsx.py export <private> <private>/research/translation/ga2_script.xlsx
# 2. Check a translated copy against the discs and dumps (IDs, Source, encoding)
python3 tools/modding/script_xlsx.py check my_translation.xlsx <private>
# 3. Import the English and build the language pack
python3 tools/modding/script_xlsx.py import my_translation.xlsx <private> <work-dir> \
        --build <mods-dir> [--mod-id ga2-en] [--wrap 372] [--font-metrics metrics.json]
```

- **Inputs read by `export`:**
  - `<private>/discs/<disc>/*.iso`, for the scripts and the tables
    `adv/bg.tbl`, `adv/charNNN.tbl` and (Zettai only) `bgm/bgm.tbl`;
  - `<private>/research/assets-dump/<game>/index.csv` and
    `sprites/names.json` from `tools/assets/dump_images.py`
    ([../assets/images.md](../assets/images.md)), for the picture links and
    the romanised character names.
- **Inputs read by `check` and `import`:** the `.gasm` dumps in
  `<private>/research/modding/dumps/<disc>/` (made by
  `ga2scripts.py extract`).
- **Re-exporting overwrites the file.** Translate in a copy, or merge by ID.

## Workbook layout

There are five sheets: README, Zettai, Mugen, Eigou and Names. A two-disc
game keeps both discs in one sheet: Mugen has mugen-1, then mugen-2 (the
Zettai Extra Route), and Eigou has eigou-1, then eigou-2.

**Rows.** There is one row per string-table entry that the game shows as
text: argument 0 of `msg`, and every argument of `menu` and `choice`.
- Rows are in script file order, then in order of first use inside the
  script. `Seq` restores that order after sorting.
- A string used several times appears once. Example: a choice option whose
  text the next message repeats is one shared string. Such rows get
  `shown Nx` in Flags.

Rows per sheet:

| Sheet | Discs | Scripts | Rows | In unreachable code | In debug scripts |
| --- | --- | --- | --- | --- | --- |
| Zettai | zettai | 409 | 27,844 | 125 | 1,059 |
| Mugen | mugen-1 / mugen-2 | 451 / 458 | 31,555 / 31,935 (63,490) | 29 / 125 | 2,550 |
| Eigou | eigou-1 / eigou-2 | 622 / 45 | 37,582 / 4,976 (42,558) | 0 / 0 | 3,202 |
| Names | all | - | 509 | - | - |

The debug rows come from the developer test menus (`test*.asb`).

**Columns** (also explained on the README sheet):

| Column | Content |
| --- | --- |
| ID | `<disc>:<script>:s<N>`. N is the string-table index, which is the `.string sN` label in `<dumps>/<disc>/<script>.gasm`. The table order is fixed by the disc, so IDs do not change between exports. |
| Seq, Disc, Script | position; Script is `adv/scn/<script>.asb` |
| Scene (dev label) | the name of the function that uses the string (developer label, not shown in the game) |
| Type | `dialogue` (say), `thought` (think), `narration` (say with the 0x20 no-name flag), `narration (box)` (after `narration_box 1`), `caption` (after `text_slot`), `message` (no say/think before the msg), `choice`, `menu` |
| Name tag (game) | `NAMEnn` from the speaker's `adv/charNNN.tbl`. nn is the low nibble of bits 16-23 of the say/think char argument, so for example Tequila is NAME01 of char 2. Empty for narration. |
| Speaker | empty, for the translator |
| Japanese | the stored text; cell line breaks are the game's line breaks, and `\sNN` `\cAABBGGRR` `\vNNN` are kept as written |
| Ruby | `base《reading》` for every kanji run followed by a kana-only `（...）` |
| Codes | the control codes found in the text |
| English, Notes | empty, for the translator; only English is imported |
| Face icon | the speaker's message-window face (`[FACE_S]`, or `facNNN_xxxx` on Eigou, whose `[FACE_S]` is empty), linked to the PNG |
| Background, CG | the last `bg`/`bg_trans` picture (`ad_bk_*` or `iv*`), linked |
| Sprite left/centre/right | the standing portraits, linked |
| BGM | Zettai: `#N*10 file (title)` from `bgm.tbl`; Mugen/Eigou: `track N` |
| Voice, Speaker code | the raw say/think voice and char/face values, plus the second `msg` argument on Mugen/Eigou |
| Flags | `unreachable code`, `shown Nx`, `debug script`, `also a function name: do not translate` |
| Source | the exact `.gasm` string body; `check` and `import` refuse a row whose Source no longer matches the dump |

**Formatting:**
- Each sheet has a frozen header, an auto-filter and fixed column widths.
- Text wraps, and rows are sized to the Japanese line count.
- The font is Noto Sans CJK JP.
- The English, Speaker and Notes cells are tinted.
- Links are `HYPERLINK("file:///...png", label)` formulas with absolute
  paths into the asset dump. They work in LibreOffice and Excel on the same
  machine, and LibreOffice keeps them when it re-saves the file.

## How the columns are derived

**Ruby.** The games have no ruby (furigana). The message parser
(`0x1ce0a0` on Zettai, and its counterparts on the other discs; see
[text.md](text.md)) knows only `\s`, `\c` and `\v`. A scan of every string on
all five discs found:
- no `《》` and no `｜`;
- no other backslash codes, apart from `\ ` in test strings.

A few lines print a reading in full-width brackets after a word. The
Japanese column keeps those brackets, because the game prints them. The
Ruby column lists them in `base《reading》` form: 6 rows on Zettai, 22 on
Mugen and 14 on Eigou.

**Speaker.** The say/think handler (`FUN_001ed6e8`, Zettai) stores:
- `char & 0xffff` as the character;
- `(char >> 16) & 0xff` as a name/flag byte;
- the face, the voice and a volume (0-100).

`say_voice` (0x56) has one extra flags argument first.
- The low nibble of the flag byte selects `NAMEnn`; char 999 has NAME00-07.
- 0x20 is used with `0x200000` and is read as the no-name/narrator flag.
  This is an inference: the drawing side was not traced.
- The flags 0x10 and 0x30 are left as raw values in Speaker code.
- The Speaker column is left empty on purpose.

**Scene state.** Each script is interpreted in code order with a small
argument stack; values pushed by variables become `(computed)`. The
calls used are:
- `bg`/`bg_trans`: the id is looked up in the disc's own `bg.tbl`
  `[BG]`. Ids of 100 and above are the `iv*` CGs, which go in the CG
  column; -1 clears.
- `chars`: sets all three slots.
- `char_left`/`char_center`/`char_right`: place a character, removing it
  from any other slot. The face argument is looked up in `[FACE_L]`
  (`& 0xffff`).
- `char_hide`, `chars_clear`.
- `bgm`, `bgm_stop`.

Character ids are resolved against each disc's own tables. Pictures are
linked through `index.csv` (disc + archive path).

**Limits:**
- **Branches.** The code after a `choice` is read branch after branch, so a
  later branch starts with the state the earlier one left.
- **Script boundaries.** State is carried into the next script only when
  the previous script lists it as an extern (a jump target). Otherwise it
  starts empty until the script's first call.
- **Expressions.** say/think change the face icon. They may also change
  the standing sprite's expression, which is not modelled.
- **Unknown face ids.** Between about 40 and 240 sprite cells per disc name a face id
  missing from the table (for example values with bit 0x4000). They show
  as `face #N (not in FACE_L)`, without a link.
- **BGM.** The BGM id mapping (#N*10) is inferred from the character
  themes on Zettai. Mugen and Eigou have no `bgm.tbl` in IDX.DAT or in the
  packs, so they show only the track number.
- **Face icons on Eigou** are linked only where the dump has a
  `facNNN_xxxx` file for the portrait id (7,346 rows).

## Import and the language pack

The full pack (scripts, names and translated pictures) is built with:

```sh
python3 tools/modding/script_xlsx.py import ga2_script.xlsx <private> <work> --build <mods>
python3 tools/modding/text_textures.py <private>/research/translation/images \
        <translated-images> <mods>/ga2-en [--dumps <texture dump folders>]
```

`text_textures.py` copies each translated picture under its texture-pack
name from `images/index.csv`; with `--dumps` it also cuts the matching
piece of the translation for any dumped texture that is part of a
picture (pictures drawn in pieces).

`import` does three things:
1. It reads the ID, English and Source columns of the three game sheets,
   by header name. The reader handles shared strings, inline strings and
   rich text, so a file saved by Excel or LibreOffice works.
2. It converts each English cell to `.gasm` string notation:
   - a cell line break becomes `\n`;
   - `\s`/`\c`/`\v` codes pass through, and any other backslash is
     doubled;
   - quotes are escaped;
   - curly quotes, `…`, `–`/`—` and no-break spaces become ASCII;
   - accented Latin letters that Shift-JIS lacks lose their accent
     (`café` → `cafe`, `â` → `a`); letters with no plain form (`ß`) are
     reported.
   It then validates the text against Shift-JIS.
   A cell holding only `<omitted>` counts as untranslated: the line keeps
   its Japanese text, and `check`/`import` report how many there are.
3. It copies each affected dump script to `<work-dir>/<disc>/` and
   replaces those `.string sN` lines. Only the string text changes, so code
   offsets and saves stay valid (see [scripts.md](scripts.md)). Each copy
   must still assemble.

4. **Layout.** Cell line breaks follow the Japanese layout, so for
   dialogue, thoughts, narration and messages with Latin text the cell is
   reflowed (a blank line stays a paragraph break) and word-wrapped to the
   box. When it needs more than 3 lines at size 18, the largest size that
   fits is chosen with the game's own `\sNN` code (line height is spacing
   4 + the tallest glyph, so the 62-pixel box takes 3 lines down to size 13
   and 4 lines at 12). An authored `\sNN` at the start is kept as the
   starting size. The narration balloon is only reflowed; choices, menus
   and captions keep their breaks. `--no-fit` turns all of this off.
   (Full workbook, 2026-10-19: about 50 of 133,678 lines needed a smaller
   size; 11 warnings remain, all developer storyboard notes that overflow
   in Japanese too.)
5. **Character names.** The Names sheet's English column replaces
   `NAMEnn` in each `adv/charNNN.tbl`. The game reads the copies inside the
   `advinit` load group (an FSTS pack in ADV.DAT), not the ALL.DAT files:
   the build appends a rebuilt pack to the mod's STRM.DAT and points the
   group in IDX.DAT at it (`ga2scripts.py build --extra`), so ADV.DAT is not
   shipped.

With `--build <mods-dir>`, it then:
- runs, for each disc,
  `ga2scripts.py build <iso> <work-dir>/<disc> <mods-dir>/<mod-id>/files --target strm --wrap 372`.
  This writes `files/<SLPM>/IDX.DAT` and `STRM.DAT` (about 200 KB per
  disc);
- writes `mod.json` with `"language": "en"`, unless it already exists.
  [../MODDING.md](../MODDING.md) describes how the launcher uses language
  packs.

The build word-wraps Latin text to 372 pixels with the disc's font metrics,
or with `--font-metrics` when the pack ships its own `font/metrics.bin`.
It warns about messages longer than 3 lines. The budget is the same for
every message of a build. Narration in the large balloon could use more
width, but 372 is safe there too.

**Tested** (scratch files, since removed):
1. Three English cells were put into an exported workbook:
   - a long paragraph;
   - a `\s20` narration line;
   - a line with curly quotes, an ellipsis and a forced break.
2. The workbook was re-saved through headless LibreOffice.
3. `check` and `import --build` ran on the re-saved file.

The scripts in the built `STRM.DAT` (Zettai `SLPM_662.43` and Mugen 1
`SLPM_667.79`) decode to the wrapped English with the codes and breaks
intact. This was not run in the game; the in-game proof of the same build
path is in [scripts.md](scripts.md).

### Messages restored from a save

A save keeps the message that was on screen and shows it again when it is
loaded, in the language it was made in: a save from a Japanese session shows
its first line in Japanese under a language pack. `import --build` therefore
also writes `<mod>/text/<program>.msg` (`restore_text.py`, which can be run
on its own for an existing pack): every message string the pack changed, as
a digest of the original bytes (SHA-256, first 16 bytes) -> pack bytes. The
pack carries none of the original text. The engine (`ga2_text.cpp`) swaps such a text
for the pack's when the game lays it out. A Japanese line translated
differently in two scenes takes the first scene's English.

## Verification of the export

- **Count:** every string that the dumps use as message or choice text
  (stack analysis of the assembled `.gasm`, dead code included) appears
  exactly once, with an identical Source. That is 133,892 of 133,892, with
  no duplicate IDs and no extra rows. This analysis also agrees with the
  dumps' own `; msg`/`; choice` comments for every script.
- **LibreOffice:** headless LibreOffice opens the file in about 6 s.
  Its CSV export of all four data sheets is cell-for-cell identical to the
  tool's own reader.
- **Stability:** two exports gave the same IDs in the same order.
- **Spot checks:** rows on all five discs were traced by hand against the
  `.gasm` call sequence: background, sprite slots and faces (including flag
  bits such as `0x207d7` → face 2007), slot moves, BGM, the face icon and the
  ruby split. They matched.

## Button placeholders in text

The games print some button names as text: the ship-map tutorial
("赤く表示された施設の上で○ボタンを押すと..."), the battle tutorials
(○ × △ □ L1 R1 L2 L3 R3 in `slg_opdemo.dat`), the movie hint "×：再生終了"
in the executable. In the English cell write a **placeholder** instead of the
button's name; the port draws the icon of the input device the player uses
(PlayStation, Xbox, keyboard or VR headset), at the text's size, and changes
it live when the player switches device.

```
Press {circle} over a location shown in red to go there.
To change floors, press {l1} or {r1}.
{cross}: Stop
```

| Placeholder | Icon | Width |
| --- | --- | --- |
| `{circle}` `{cross}` `{square}` `{triangle}` | the four face buttons (PlayStation shapes; Xbox B A X Y; keyboard Enter X Z V; VR A B X Y) | 1 em |
| `{l1}` `{r1}` `{l2}` `{r2}` `{start}` `{select}` | shoulder buttons, triggers, Start, Select (keyboard: Q E, L-Shift R-Shift, P, Tab) | 2 em |
| `{l3}` `{r3}` | left / right stick click (keyboard R, F) | 1 em |
| `{lstick}` `{rstick}` | the sticks (keyboard: WASD; the right stick has no key) | 1 em |
| `{dpad}` | the whole directional pad (keyboard: arrows or WASD) | 1 em |
| `{up}` `{down}` `{left}` `{right}` | one D-pad direction | 1 em |

- **Kept PlayStation names become placeholders.** When the English keeps the
  game's own button name where the row's Japanese names a button, the build
  writes the placeholder for it (`text_buttons.auto`, used by `import` for
  the story, Battle, UI and Program sheets): `○ × △ □` when the Japanese has
  `○ボタン` (or a key legend such as `×：`), `L1`..`R3` for `Ｌ１ボタン` etc.,
  `Start`/`Select` before "button" for `スタートボタン`/`セレクトボタン`,
  "left stick"/"right stick" for `左スティック`/`右スティック` and "directional
  buttons" or "D-pad" for `方向キー`. So "Press the × button!" ships as
  "Press the {cross} button!". Rows whose Japanese does not name a button are
  left alone ("○×△□~..." mumbling, "the ATM's buttons").
- **The games' own text gets it too.** The engine swaps the same prompts in
  the Japanese text when it draws them (`ga2_text.cpp`), so playing in
  Japanese also shows the player's buttons.
- **Case** does not matter (`{L1}` = `{l1}`). Any other `{word}` is an error
  (`check` and `import` list the valid names), so a typo cannot reach the game.
- **Width.** A placeholder is a full-width character: one em (the text size,
  18 pixels in the dialogue box) or two for the pill-shaped buttons. The build
  turns it into that many reserved characters *before* it fits the text to the
  box, so the line fit and the wrapping count the icon's width, not the letters
  of the name. Put a space on each side as you would around a word; the icon
  has a little margin inside its cell.
- **Where it works.** Every text the games draw with the message and string
  drawers: story messages (dialogue, thoughts, narration, captions, choices),
  the battle balloons (Battle sheet), the UI sheet values and the Program
  sheet strings (the executable's text), on all five discs. The Program sheet's
  room limit counts bytes: a placeholder is 2 bytes (4 for a pill) like the
  `○` it replaces (2 bytes). Not in pictures (replace those through
  `textures/`), and not in the Names sheet.
- **Colour.** The icons keep their own colours (the engine draws them
  untinted, with the text's fade-in alpha), also in the dark text of the
  dialogue box and the colour codes `\c...` do not change them.
- **What the player sees.** The PlayStation set is the port's drawn version of
  the games' own prompts; Xbox, keyboard and VR use the same icons as the
  texture swap for the dialogue box and tutorials (`native/buttons`). With the
  software renderer (`GA2_GS=cpu`, no texture replacement) each placeholder
  shows its PlayStation symbol in the text colour instead (as does a build
  with `GA2_NO_TEXT_ICONS=1`).
- **Without the feature** (a build before it): the placeholder characters
  draw as blank cells.

The reserved characters are Shift-JIS 0xF040-0xF058 (private use U+E000..).
`python3 tools/modding/text_buttons.py list` prints the table;
`ga2_text.cpp`, `make_text_icons.py` and `gs_texpacks.cpp` are the engine
side (see [text.md](text.md)).

## Not covered

- **Battle and UI text** is in a second workbook, made by
  `tools/modding/extra_text.py export <private> ga2_battle_ui.xlsx`
  (2026-10-19: Battle 1,109 rows / 2,081 uses, UI 663 rows / 3,056 uses):
  - Battle: the dialogue of the battle opening demos and tutorials,
    `slg/stage/<stage>/slg_opdemo.dat`. These are bytecode files: each
    instruction word holds 1 + its operand count in bits 10-13, opcode
    0x85D takes the file offset of a string, and the string pool follows the
    code. The import appends the English strings and repoints those
    operands, so nothing else moves. Line breaks are kept as written.
  - UI: `KEY =value` lines of the pack tables that show Japanese outside
    comments (save-slot chapter titles, extras, Angel select, the tutorial
    menu, battle commands, unit and stage names). File names and folders are
    left out.
  One row per distinct Japanese text; `Where` lists every place it occurs.
  `extra_text.py check` validates a filled copy; `script_xlsx.py import ...
  --extra-xlsx <file>` builds it into the same mod, rebuilding the packs
  (changed files appended, the rest kept as disc ranges in
  `STRM.DAT.ga2seg`).
  Fitting (2026-10-19): the balloon's text builder (`0x1ce0a0` on Zettai)
  takes the story codes `\sNN`, `\cRRGGBB` and `\vN`. A Battle cell keeps
  its line breaks when it fits 3 lines at size 13 (180 px); otherwise it is
  reflowed, and if that is still too long it is set at size 12 or 11, which
  give 4 lines (62 px of height, a line being 4 px plus its glyphs). The
  first translation: 962 rows as written, 105 reflowed, 42 at size 12.
- **Staff roll (end credits)**: `adv/roll_s*.txt` (in the `ending` load
  group of ADV.DAT and in ALL.DAT; 662 distinct texts, 10,043 uses over the
  five discs). Each line is the text (quoted or bare), font size, alignment
  L/R/C, x and an optional y. `extra_text.py export-credits <private>
  ga2_credits.xlsx` writes a Credits sheet (Kind = column, Key = size and
  room in pixels; names that exactly match the story workbook's Names sheet
  are pre-filled); import it with a second `--extra-xlsx` (the option can be
  repeated). The import keeps the layout and warns when a text is wider than
  its room. Curly quotes stay full-width (“ ”) there, since the file's own
  field quotes are ASCII. Mod 1.3 (2026-10-19) carries the user's Credits and
  Program translations (731 rows, 298 program patches; verified: save list
  "Ch.1 ...", 71 Zettai strings applied, `research/run57/en-launcher2`).
- **Program text**: strings inside the game executables: Zettai's battle
  command names (Stay, Move, Attack, H-Weapon, Follow, Supply, Repair,
  Empty, On/Off, Standby, Activate; Mugen and Eigou read theirs from
  `slg_general.tbl`), memory-card and save messages, the save-slot chapter
  format (`%d章 %s - 終 -`), the extras cast list and a few developer
  strings. `extra_text.py export-more` writes them to the Program sheet of
  the same workbook as the Credits (71 distinct texts over the five
  programs; only the string pool at the start of `.rodata` is listed, binary
  tables after it decode to chance kanji). Each English text must fit its
  room (the original's bytes plus the zero padding after it, minus the
  NUL; shown in Key) and keep the original's `%` codes in order; the import
  refuses anything else. The build writes `<mod>/exe/<program>.tsv`
  (`address`, original bytes, new bytes, in hex), and the runtime
  (`native/compat/ga2_exetext.cpp`) applies each line right after the
  program is loaded, only where the loaded bytes are the original and the
  new string fits; code keeps pointing at the same address. Verified with
  placeholder text on Zettai's unit panel action label
  (`research/run57/en-exetest`, 72 strings replaced).
- **Unquoted table values with spaces**: the game's table parser ends an
  unquoted value at a space (Zettai's unit panel showed "Cross" for "Cross
  Caliber"), so the import quotes any translated value that contains one;
  quoted values with spaces exist in the stock tables ("Wing of Destiny").
- **Eigou disc 2** carries only 45 scripts (the opening events, chapters 8-9
  of route X and the endings). Its `adv/scn/list.txt` names 777 scripts in an
  older naming scheme, but none of the other 732 exist on either Eigou disc;
  its larger voice bank holds clips no script plays. Nothing is missing from
  the workbook.
- **UI tables (`common/*.tbl`).** The ones checked (`sysdlg.tbl`) point at
  `.a2a`/`.agi` pictures; their Japanese is only in developer comments. UI
  text is in images, which are replaced through `textures/`. Other
  `common/` tables were not checked one by one.
- **Chapter titles and eyecatches** are pictures, not strings.
- **Battle (SLG) dialogue** is the Battle sheet above (`slg_opdemo.dat`).
  A scan of every other file in the SLG packs (2026-10-19, Zettai and
  Mugen 1) found no further text, only binary model/texture data; the
  `slg/machines/*.tbl` files are model tables (see [scripts.md](scripts.md)).

## Pictures with Japanese text

Menus, buttons, system messages, chapter and stage titles, place names and a
few captioned CGs are pictures, not strings. They are translated by
replacing textures (see [../MODDING.md](../MODDING.md)).
`tools/assets/text_images.py` collects all of them, from all five discs, into
one folder for the translator:

```bash
python3 tools/assets/text_images.py <private> [--curation CSV] [--out DIR] [--jobs N] [--no-contact]
```

It takes about 10 minutes (most of it the pure-Python texture hash). Input:
the ISOs and a curation list, `<private>/research/translation/images/curation.csv`
(`sha256, archive_path, category, japanese, note`). `sha256` is the pixel hash
of `dump_images.py`, so each row selects exactly one picture whatever its file
name, archive or disc. The list stays in the private root because it quotes
the game's text. Output, under `<private>/research/translation/images/`:

| Path | Content |
| --- | --- |
| `<game>/<category>/*.png` | pictures used by only one game |
| `shared/<category>/*.png` | pictures used by several games, written once |
| `index.csv`, `ga2_images.xlsx` | file, games, discs, category, archive paths, size, Japanese, English (empty), texture-pack name, match status, notes, pixel sha256 |
| `contact/<folder>.png` | contact sheets |
| `README.md` | editing and install notes for the translator |

Categories: `buttons`, `menus`, `system`, `titles`, `chapter`, `location`,
`battle`, `tutorial`, `cg`, `other`.

**How the list was made.** Every AGI picture on the five discs except
portraits and face icons was decoded and looked at in contact sheets (4,903
distinct pictures outside CGs and backgrounds, plus the CG contact sheets of
`dump_images.py`). Pictures with Japanese were transcribed by eye.
Not listed: pure art, sprites, face icons, battle effects, 3D textures, map
radar images, and UI parts that have English only (BACK LOG, ship names on
the briefing map, GAME OVER). A picture that is only partly text (logo with
subtitle, character tile with name, stage preview with a Special Rule plate)
says so in `notes`. The font is a texture too, but it is replaced through
the font route, not here. The runtime dumps in `research/buttons/dump-*` were
also checked. Every dump that matches no disc picture is a font glyph, a 3D
or battle texture, or a render target, so the dumps showed no text pictures
outside the list.

Result (2026-10-15): 841 pictures, of which 364 are used by several games
(shared). Eigou has 351 game-only pictures, Mugen 44 and Zettai 82. By
category: 186 location, 209 menus, 119 system, 109 cg (Eigou storyboard
placeholders, ending sketches with voice-actor credits), 75 chapter,
74 battle, 27 tutorial, 17 titles, 11 buttons and 14 other.

**Texture-pack names.** The port names a texture `<hash>_<w>x<h>.png`, where
the hash is `hashPixels` in `gs_texpacks.cpp` over the sampled GS region in
PS2 RGBA (raw alpha, 0x80 = opaque). The tool rebuilds those words from the
AGI itself, without the PNG alpha round trip. It uses the CSM1 CLUT order and
the port's 5-bit expansion for PSMCT16. The hash was checked against the
file names of runtime dumps. The `match` column means:

- `dumped`: a runtime dump has exactly these pixels. Its name is used, and
  it always equals the predicted name. 40 pictures.
- `predicted`: the name is computed for the whole picture. This is right
  when the game samples the whole picture as one region, which every dumped
  UI picture so far did. A picture that the game draws in pieces gets
  different names. The search for dumps that are a sub-region of a listed
  picture found none. Take the name from a texture dump in that case
  (Options > Dump textures).

**Not checked in game.** No translated replacement was loaded and shown in
the game by this tool. The predicted names are proven only by the 40 dumped
matches.
