# Story scripts and text (all three games)

Status 2026-10-14: the format is understood well enough to extract, edit and
rebuild every story script on all five discs. Unchanged dumps rebuild to the
same bytes, and an edited Zettai opening was shown in the game through the
engine's mods folder. Tools are in `tools/modding/` (standard-library
Python). Disc data, dumps and screenshots stay in the private root, under
`research/modding/`.

## Where the text is

| Disc file | Container | Contents |
| --- | --- | --- |
| `IDX.DAT` | PIDX0 master index (7 archives) | the file table the game loads files through: name tree, archive, offset, sizes |
| `ALL.DAT` | PIDX0 tree archive | single files: `adv/scn/*.asb` (**story scripts**), `adv/*.tbl` (character, chapter, BG tables), faces, BGs, models, SE banks |
| `ADV.DAT`, `ADVMAT.DAT`, `SLG.DAT`, `STRM.DAT` | PIDX0 "flat" archives of FSTS packs | load groups (`advinit`, `title`, `config`, `slginit010100`, ...) holding copies of tables and textures (UI tables like `common/sysdlg.tbl`, `common/config.tbl`) |
| `STRM_R*.DAT` | streams | voice/BGM/movies (listed in IDX.DAT, not script related) |

Script counts (`adv/scn/*.asb`): Zettai 409, Mugen disc 1 451, Mugen
disc 2 458, Eigou disc 1 622, Eigou disc 2 45. **All five discs use the same
archive, compression and script formats.** All 1,985 scripts parse, and the
same tools work on every disc. The executable's script VM was only examined
on Zettai. The other discs call builtin IDs up to 0x77, while Zettai
registers only 0x00-0x02 and 0x10-0x62, so their builtin sets differ.

Battle (SLG) dialogue was not examined. It is probably in `slg/...` tables
inside SLG.DAT packs, not in `.asb` files.

### PIDX0 (IDX.DAT and every archive)

The header layout is documented at the top of `tools/modding/ga2arc.py`.
Summary:
- Each archive descriptor (32 bytes at 0x30) holds a name offset and a
  16-byte ID. The same ID appears in the archive's own header.
- Tree records are 24 bytes:
  - directory: `{1, name, child_count, first_child, 0, 0}`
  - file: `{0, name, archive_name, offset, size, stored_size}`;
    `stored_size` 0 means the data is stored raw, not as a RAIC blob (e.g.
    `se/*.bnk`)
  - each directory's children are one contiguous block sorted by name
    (byte order), which the game binary-searches
- In IDX.DAT, `archive_name` is the string offset of the archive that holds
  the file (`all.dat`, `strm_r.dat`, ...).
- IDX.DAT's tree is ALL.DAT's tree plus the stream files. For every ALL.DAT
  file, the offset and sizes are identical in both indexes.
- Flat archives also have a group table at 0x18: `u32 n`, then n offsets to
  20-byte `{name, 0, offset, size, file_count}` entries, each pointing at an
  FSTS pack. An FSTS pack is `"FSTS", count, entry_off, str_off, str_size`,
  followed by 16-byte entries `{name, offset, size, stored_size}`.
- **The game finds files through IDX.DAT, including which archive file to
  read.** Proof: pointing a script's IDX record at `strm.dat` and appending
  the script to STRM.DAT works in the game (see the edit proof below).

### RAIC blobs (every stored file)

- The first 4 bytes are `"RAIC" ^ 0x72` (on disc: `20 33 3b 31`) for LZSS,
  or `"RAIB" ^ 0x72` for a stored file.
- Next comes `u32 size` (not XORed), then the payload XOR 0x72.
- The LZSS is Okumura's: 4096-byte ring, zero-filled, start position 4078,
  match length 3-18, flag byte LSB first, 1 = literal.
- `ga2arc.lzss_encode` is Okumura's binary-tree encoder. It reproduces all
  1,985 stored script blobs (and the 532 Zettai tables tested) **byte for
  byte**, so unchanged files repack identically.

## Script format (`.asb`, "ASN" stack VM)

The VM is `asn/asn_vm.c` in the executable. Its interpreter is
`FUN_0021cf58` in SLPM_662.43, and its opcode name/size table is at
0x2e8ee0.

- **Header (0x44 bytes):** `u32 0`, then `char[32]` file name, then
  offset/size pairs for functions, code, strings and externs.
- **Functions (20 bytes each):** `name` (string offset), `flags`
  (1 = local `@L...` helper, 0 = exported `@...` scene entry), `u16 locals`,
  `u16 params`, `code_off`, `code_size`.
- **Code:** 45 opcodes, listed below.
  - Branch targets are code-section offsets.
  - `CALLL`/`JMPL` take a function index.
  - `CALLG`/`JMPG` take an (extern name offset, function name string
    offset) pair, which is how one scene jumps to the next scene file.
  - `BTBL n, t0..t(n-1)` is a switch on the popped value; the last entry is
    the default. It follows choices.
  - `CALLB id, nargs` calls an engine builtin, and `PUSHR` pushes its
    result.
- **Strings:** NUL-terminated cp932. Identical texts are shared (a choice
  label and the line that repeats it point at one string).
- **Externs:** NUL-terminated script names used by CALLG/JMPG.

Opcodes: `NOP PUSHI PUSHL PUSHG PUSHF PUSHR POP POPL POPG POPF STIL STIG
STIF INCL DECL INCG DECG EXCH NEG ADD SUB MUL DIV MOD AND OR NOT EQ NEQ LT
GT LEQ GEQ B BZ BNZ BTBL JMPL JMPG CALLL CALLG CALLB RET RETN YIELD`. Sizes
are in `ga2asb.OPS`; `G` = global variable, `F` = flag, `L` = local.

### How a scene is encoded

Arguments are pushed left to right, then `CALLB id, n`. The `.gasm` form
writes known builtins by name (`CALLB say 3`); the assembler accepts a name
or a number. Names come from the developer test menu `adv/scn/test.asb`,
whose own messages say what each call does ("次は、クロスフェードです。"
followed by `bg 22, 1`, and so on), and from story scripts. The full list,
with argument notes, is `BUILTINS` in `tools/modding/ga2asb.py`. `?` marks
guesses.

| id | name | arguments - meaning |
| --- | --- | --- |
| 0x00 | `msg` | text[, voice] - show a message, wait for the button |
| 0x01 | `menu` | option... - system/debug menu; `PUSHR`, `BTBL` |
| 0x02 | `choice` | option... - story choice (shown in random order); `PUSHR`, `BTBL` |
| 0x13 | `say` | char, face[, voice] - speaker for the next `msg`: char-table `ID` (0 Kazuya, 13 Forte, 0x200000 narrator), face `#NNNN` from `charNNN.tbl` (0 通常, 1 笑い, ...), voice clip |
| 0x14 | `think` | char[, face, voice] - monologue balloon |
| 0x15 | `narration_box` | on - large narration balloon (up to 20 lines) |
| 0x16 | `msg_hide` | - close the balloon |
| 0x4b | `text_slot` | slot, x, y, align, ?, voice, ?, ? - free text position (prologue: centred at 320, 65)? |
| 0x10 | `bg` | bg_id, effect[, ...] - background (`bg.tbl` id, -1 none; effect 0 cut, 1 crossfade, 2/3 wipe right/left, 4 fade) |
| 0x36 | `bg_trans` | bg_id, type, ms[, curve, angle] - background transition with time, easing (0-12) and wipe angle |
| 0x19 | `chars` | lchar, lface, cchar, cface, rchar, rface[, effect] - set all three standing characters (-1 empty) |
| 0x1a / 0x1b / 0x1c | `char_center` / `char_left` / `char_right` | char, face, effect[, priority] - show a character (effect 0 cut, 1 fade, 2 move, 3 fade-slide, dir<<16\|2 slide in) |
| 0x1d | `char_hide` | char, effect |
| 0x1e | `chars_clear` | [effect] |
| 0x1f | `wait_chars` | - wait for character animations |
| 0x55 | `char_priority` | char, priority |
| 0x62 | `eyes` | char, closed |
| 0x31 / 0x32 / 0x33 / 0x34 | `char_effect` / `effect` / `effect_stop` / `effects_clear` | emotes (`adveffect.tbl`) on a character or at x, y |
| 0x3c / 0x3d / 0x3e / 0x40 | `mat_play` / `mat_stop` / `mat_clear` / `mat_wait` | MAT animations (`advmat.tbl`) |
| 0x11 / 0x12 / 0x53 | `bgm` / `bgm_stop` / `bgm_volume` | track[, fade_ms, ?, volume] / [fade_ms] / volume |
| 0x38 / 0x39 / 0x3a / 0x61 | `se` / `se_loop` / `se_stop` / `se_volume` | id[, vol, pan] / slot, id[, vol, pan, fade] / slot[, fade] / slot, vol, ms |
| 0x20 | `wait` | ms[, mode] |
| 0x21 / 0x22 / 0x23 | `shake` / `shake_stop` / `wait_shake` | x, y, speed, ms(-1 endless)[, fade] / fade_ms / - |
| 0x24 | `flash` | color, ?, count |
| 0x25 / 0x26 | `color_in` / `color_out` | color(0xAABBGGRR), ms[, whole screen] - fades |
| 0x27 / 0x37 | `game_init` / `game_end` | new-game reset / back to the title |
| 0x28 / 0x2a | `ship_move` / `battle` | chapter, part - ship map / chapter, stage - SLG battle |
| 0x2b / 0x2c / 0x30 | `chapter_title` / `eyecatch` / `ranking` | chapter / heroine, ? / place |

Still unnamed (numbers in dumps): 0x18, 0x29, 0x2d, 0x2e, 0x2f, 0x35 (taken
at the start of scenes, e.g. 0x35 25), 0x3b, 0x3f, 0x41-0x4a except 0x4b,
0x4c-0x52, 0x54, 0x56 (a `say` variant with voice?), 0x57-0x60 (0x60 0 opens
every scene), and the Mugen/Eigou-only ids up to 0x77. The ids above keep
their meaning in Mugen and Eigou scripts as far as their use matches, but
those discs were not checked call by call.

- **Speaker names are not in the script.** They come from `NAME00` in
  `adv/charNNN.tbl` (cp932 INI text in ALL.DAT) and are drawn as text in
  the name box.
- No player-name substitution exists: the hero (Kazuya) has a fixed name.
- **Only builtins 0x00 (argument 0), 0x01 and 0x02 take string
  arguments.** Stack simulation over all 1,985 scripts, dead code included,
  finds every string in every string table referenced by one of these,
  by a function entry or by CALLG/JMPG. No string is left unexplained.
- Some scenes start with a `B` jump over leftover dialogue (dead code);
  those strings are kept too.

Zettai builtin handlers (ID: function), useful for naming the remaining
builtins:
```
00:1e9ef0 01/02:1ea330 10,36,54:1ea7f8 11:1ecfb0 12:1ed0a0 13,14,56:1ed6e8 15:1ee340
16:1eded8 18:1eb4c8 19-1c:1eb8d8 1d:1ebb48 1e:1ebc60 1f:1eda08 20:1eda80 21:1edbb8
22:1edca0 23:1edd20 24:1edd50 25/26:1eddb8 27:1ee2e0 28:1edf88 29:1edfd0 2a:1ebec8
2b:1ebd60 2c:1ee250 2d:1ec948 2e:1ee370 2f:1ec028 30:1ec170 31:1ee4b0 32:1ee5a0
33:1ee6b0 34:1ee7b0 35:1ec548 37:1eecb8 38:1ed180 39:1ed2a0 3a:1ed538 3b:1ed638
3c:1ec2e8 3d:1ee938 3e:1eea30 3f:1eeb00 40:1eebc0 41:1eadb0 42:1eb0f8 43:1eaef8
44:1ee090 45:1ee120 46:1ee1b8 47:1ee860 48:1ee030 49:1eece8 4a:1eede0 4b:1eeec0
4c:1eefe0 4d:1ef108 4e:1eb348 4f:1ef218 50:1ef338 51:1ef410 52:1ef448 53:1ed0f0
55:1ed820 57:1ef4e8 58:1ef5b0 59:1ef468 5a:1ef628 5b:1ecbe0 5c:1ef6f8 5d:1ef780
5e:1ef7d0 5f:1ef828 60:1ef908 61:1ed408 62:1ed900
```

### Text encoding and control codes

The message parser is `FUN_001ce0a0`. It turns a message into per-character
records of {size, code, colour}, with at most 512 characters per message
and a default size of 18.

- **Encoding:** Shift-JIS (cp932). Byte 0x0A is a line break.
- **No automatic wrapping:** a long line runs past the box (screenshot
  below), so every break has to be written as `\n`.
- **`\sNN`:** glyph size for the following text (1-2 digits). The opening
  narration uses `\s20`.
- **`\cXXBBGGRR`:** colour (up to 8 hex digits). The low byte is red, then
  green, then blue; the top byte is ignored. `\cff0000ff` is red and
  `\cffffffff` is white.
- **`\vNNN`:** typing speed in percent (1-1000).
- **Other backslashes** are printed as-is.
- **Tabs** appear only in debug and test menus.
- **Never used in the shipped text:** player-name or variable substitution,
  ruby (furigana), and inline waits.

## Font and what it means for English

Details: [text.md](text.md). Summary:

- **Full-width text** uses a 24x24 4-bit JIS font built into the
  executable: symbols, full-width Latin, kana, Greek, Cyrillic, JIS level
  1/2 kanji; in SLPM_662.43 roughly vaddr 0x2fc000-0x4df000. Each glyph
  advances `size` pixels.
- **Half-width ASCII is proportional already.** It uses a second built-in
  font, "asciipfont" (128x144, 18-pixel glyphs), and a per-character
  metrics table; the advance is `(width+1)*size/18`, and a space is
  `10*size/18`. (The first notes here said the opposite; that was a
  misreading of the screenshots.)
- **No wrapping or clipping:** a long line runs past the box. `build
  --wrap 372` breaks Latin message text at spaces using the same metrics.
- **Dialogue box:** text from x = 122 (640x480 virtual space), about 372
  pixels usable, 3 lines at size 18. That is about 20 full-width
  characters, or about 40-45 typical English characters per line.
- **Colour codes work** (`\cff0000ffRed` renders red).
- The disc's only font files (`common/dumfont16.agi`, `dumfont18.agi`) are
  dummies.

What English still needs:
1. Names and UI. Character names (`NAME00` in `adv/charNNN.tbl`), chapter
   titles, and menu/dialog tables (`common/*.tbl` in the ADV.DAT and
   SLG.DAT packs, duplicated in ALL.DAT) are cp932 text. The game reads
   the pack copies: a rebuilt ALL.DAT with English `adv/char000.tbl` still
   showed Japanese names, the repointed `advinit` pack shows English
   (2026-10-19). `build --extra` handles this.
2. Text baked into images (title buttons, eyecatches, `if_*.agi` UI):
   replace them through the mod `textures/` folder.
3. Optional: a better or redrawn Latin font (texture replacement), plus the
   metrics hook in [text.md](text.md) when glyph widths change. There are
   no accented letters.

## Tools

```sh
cd tools/modding
# dump every adv/scn script of a disc to editable UTF-8 (one .gasm per script)
python3 ga2scripts.py extract <disc.iso|dir> <dump-dir>
# prove the round trip (text -> asb identical, recompressed blobs identical)
python3 ga2scripts.py verify  <disc.iso|dir> [<dump-dir>]
# rebuild changed and new scripts into a mods folder for the engine ($GA2_MODS)
python3 ga2scripts.py build   <disc.iso|dir> <dump-dir> <mods-root> [--target all|strm]
        [--wrap 372 [--max-lines 3] [--font-metrics metrics.json]]
# half-width font: export, ink-tight metrics, measure text
python3 ga2font.py dump|tight|measure ...
```

- `ga2arc.py`: ISO root reader, PIDX0/FSTS parsers, RAIC/LZSS
  decoder/encoder.
- `ga2asb.py`: `parse`, `disassemble`, `assemble`, builtin names.
- `ga2font.py`: half-width font metrics, text widths, word wrap.
- `ga2scripts.py`: the command-line interface.
- `script_xlsx.py`: translation workbook export/check/import (all discs in
  one .xlsx; see [translation.md](translation.md)).

### The `.gasm` text form

```
.name "a010_0001_00.asb"
.extern x0 "a010_0002_00.asb"
.string s9 "\s20宇宙はただ１つではない"   ; msg
.string s21 "なんだい、カズヤ？"   ; msg フォルテ
...
.func F5 s6 flags=0 locals=0 params=0   ; @カズヤとフォルテ
    PUSHI  13
    PUSHI  0
    PUSHI  5244
    CALLB  say 3             ; speaker for the next msg ...
    PUSHI  s21               ; なんだい、カズヤ？
    CALLB  msg 1             ; show a message and wait for the button
    ...
.end
```

**The `.string` lines are the editable text.**
- They appear in story order, and each comment names its use and the
  speaker.
- Quoting: `\n` is a line break, `\"` a quote, `\\` a backslash.
  `\s..`, `\c..` and `\v..` are the game's control codes and are written
  as-is.
- Any character must be encodable in cp932; the assembler reports any that
  are not.

**Code lines are the script.**
- Labels replace branch offsets, so instructions can be added or removed.
- `PUSHI "new text"` adds a new string (deduplicated). Example: add a line
  with `PUSHI 0`, `PUSHI 0`, `CALLB say 2`, `PUSHI "..."`, `CALLB msg 1`.
  `CALLG x1 "@function" 0` calls a function in another script.
- Function entries, externs and the string table are rebuilt from the
  listing. Unchanged files come out identical.

`build` assembles every `.gasm` in the dump directory and keeps only the
scripts whose bytes differ from the disc. A dump directory may hold just
the edited files.

**New scripts.** A `.gasm` whose name is not on the disc is added as a new
file (lower-case name, at most 27 characters, `.name` matching the file
name).

- **How the game looks names up.** Each directory's records are a
  contiguous block sorted by byte order, searched by binary search
  (`0x10a848`, path lower-cased).
- **What `build` does** (`Pidx.insert_file`):
  - inserts the record at its sorted place in `adv/scn`;
  - moves every later directory block down one record;
  - appends the name to the string table and shifts the group and string
    tables.
- **Which index changes:**
  - `--target strm`: only IDX.DAT changes; the data is appended to
    STRM.DAT.
  - `--target all`: the record also goes into ALL.DAT's own tree, and the
    header must still end before the first file at 0x21000; Zettai has
    about 1.9 KB to spare.
- **Calling it.** Reach the new scene from an existing script with an
  `.extern` line plus `CALLG`/`JMPG`.
- **Template.** A minimal new script is in "What was proven" below
  (`edit-newscene/a010_9999_00.gasm`): a `__main` function plus one
  exported `@...` function.

**Wrapping.** `--wrap PIXELS` word-wraps translated message text (see
[text.md](text.md)). `--max-lines` warns about messages that will not fit
the box.

- `--target all` (default) writes `<mods>/<SLPM>/ALL.DAT` (a full-size
  copy) and `IDX.DAT`.
  - An edited script goes back into its own 2048-byte-aligned slot when it
    fits, otherwise it is appended.
  - Both indexes are updated.
- `--target strm` leaves ALL.DAT alone. It appends the edited scripts to a
  copy of the small STRM.DAT (24 KB on Zettai) and points their IDX.DAT
  records at `strm.dat`. **The mod is then two small files (about 180 KB)
  instead of 140 MB.**

## What was proven

Paths are under `<private>/research/modding/`.

- **Round trip on all five discs** (`verify-all.log`): 409 + 451 + 458 +
  622 + 45 scripts are byte-identical:
  - disassembly to text, then assembly, gives the original `.asb`;
  - the dump files on disk assemble to the original;
  - recompression gives the stored RAIC blob.
  `build` on an unmodified dump writes nothing.
- **Relocation test:** a dummy string was inserted at the start of every
  table and every text was lengthened in all 1,985 scripts. After
  reassembly, every string reference pointed at the edited text and no
  other value changed.
- **In-game edit, `--target all`** (`runs/edit2`, mods in `mods-all/`):
  - Zettai New Game: the first narration line was changed to
    「改造テスト：この行はツールで書き換えた」, and three more narration lines
    to ASCII, full-width and colour tests.
  - Screenshots: `proof/allmod-frame-00062.png`,
    `proof/zettai-edit-crop2x.png` (narration), and
    `proof/zettai-edit-dialogue-crop2x.png` (the following dialogue box,
    unchanged Japanese text and the speaker name).
- **In-game edit, `--target strm`** (`runs/edit3`, mods in `mods-strm/`,
  only `IDX.DAT` and `STRM.DAT`):
  - The same narration plus dialogue edits: an English line too long for
    the box, three English lines, and a 30-character full-width line.
  - Screenshots: `proof/zettai-dialogue-width-tests-crop2x.png`,
    `proof/zettai-fullwidth-width-crop2x.png`,
    `proof/strm-mod-frame-0006{6,70}.png`.
- **How the runs were made:**
  - Command:
    `GA2_MODS=<mods> GA2_MC_ROOT=<blank card> tools/run_owned.py ga2_zettai <out> 92`.
  - Keys came from a separate XTest sender (private
    `research/modding/sendkeys.py <run-dir> <keys.json>`, run alongside
    `run_owned.py`): Circle at 22 s, then every 3 s.
  - At the time, `run_owned.py`'s own key injection failed with
    `no visible game/reference window on task display; titles=[]`. The
    recent `research/run56/plain*` and `a4k` runs show the same error. The
    window is not found by WM_NAME; this needs fixing in
    `tools/x11_input.py`.

- **New script file and wrapping** (`runs/newscene`, mods in
  `mods-newscene/`, sources in `edit-newscene/`):
  - A new script `a010_9999_00.asb`, not on the disc, holds a scene
    `@追加シーン` with two Kazuya lines. Its English paragraphs were
    written as single lines and wrapped by `--wrap 396`.
  - The opening script calls it with `CALLG x1 "@追加シーン" 0` right after
    New Game; the prologue continues afterwards.
  - Screenshots: `proof/new-scene-file-zettai.png`,
    `proof/new-scene-frame-000{27,37}.png`.
  - `--target all` with the same new file was checked offline: both
    indexes list it, and the data decodes. It was not run in the game.
- **Half-width metrics hook prototype:** see [text.md](text.md)
  (`proof/font-metrics-hook-compare.png`).

## Open questions and blockers

- **English:** see "Font" above and [text.md](text.md): UI tables, texture
  text, and (optionally) a redrawn Latin font remain.
- **Saves:** the VM snapshot in a save stores script positions (pc, stack).
  Changing code layout in a script can break saves made inside that script.
  Text-only edits keep code offsets stable, because PUSHI operands keep
  their size. Shipping translation updates is safe for saves; inserting
  instructions is not, unless the save is made at a scene boundary.
- **New scenes:**
  - New script files work (`build` adds them to the index).
  - New functions and lines can be added inside an existing script.
  - About half the builtins are named; the rest (table above) are numbers.
  - A scene's first calls (`0x60 0`, `chapter_title`, `0x35 n`) are
    copied from existing scenes without being fully understood.
  - New backgrounds, characters or voices need new table entries
    (`bg.tbl`, `charNNN.tbl`) and assets; not attempted.
- **One script mod per disc:** `--target strm` and `--target all` mods each
  ship a whole `IDX.DAT` (plus `STRM.DAT` or `ALL.DAT`), so two script mods
  for the same disc override each other (the higher one in the Mods list
  wins). Build combined packs from one dump directory.
- **Copies of tables:** for tables that exist both in ALL.DAT and in FSTS
  packs (e.g. `adv/advparam.tbl` in `rmbinit`), it is not yet known which
  copy the game reads.
- **Eigou disc 2** has only 45 scripts. The rest of its story was not
  located (possibly loaded from disc 1's set or from elsewhere).
- **Voice cues** (`0x13` argument 3, or `0x00` argument 1) index
  `voice/*.vac`/`.msh` and the `STRM_R*.DAT` streams. That mapping was not
  examined.
