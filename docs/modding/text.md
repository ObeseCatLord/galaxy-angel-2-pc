# Text layout, the half-width font and word wrapping

Status 2026-10-14. All three games, all five discs.

Correction to the first notes: **the games already draw half-width
Latin text proportionally.** English renders well without any engine change:

- every message drawer advances each ASCII character by its own width;
- the widths come from a per-character table in the executable;
- the glyphs come from a second, separate built-in font.

What English needs is line wrapping, now done by the build tool with the
same metrics (below). An optional runtime hook is designed for mods that
replace the font or its metrics. It was proven with gdb.

Evidence is under `<private>/research/modding/`:
- `font/` (exported fonts and metrics per disc)
- `runs/font-tight`, `runs/edit3` (default metrics), `runs/newscene`
- screenshots in `proof/`
- decompiled functions in the session's Ghidra output

## How a message is laid out and drawn (Zettai addresses)

1. **Parse** `0x1ce0a0`. Call:
   `(a0 records, a1 text, a2 max_chars=0x200, a3 size, t0 colour, t1 speeds)`.
   - Turns Shift-JIS text into 8-byte records
     `{u8 0, u8 size, u16 code, u32 colour}`.
   - `code` is the Shift-JIS pair (lead byte in the low byte), a single
     byte, or 0x0A for a line break.
   - It handles `\sNN` (size), `\cXXBBGGRR` (colour) and `\vNNN` (speed).
   - At most 512 characters; the default size is 18.
2. **Set message** `0x1ce6f0(msg, text, size, colour, ...)`:
   - parses into the message object: records at +0, per-character timers
     at +0x1000, speeds at +0x1800;
   - fields: +0x1c00 alignment flags, +0x1c08 character count, +0x1c0c
     characters revealed (typewriter).
3. **Draw message** `0x1cea28(a0 msg, a1 z, f12 x, f13 y)` calls the glyph
   drawer with line spacing 4.0, the revealed count, and the alignment from
   `+0x1c00 & 3`.
   - The ADV layer `0x1dd5c8` draws its four message slots this way
     (x, y at slot +4/+8).
   - The backlog (`0x1de498`) uses x = 122.
4. **Glyph drawer** `0x111c70`. Arguments:
   - f12 x, f13 y
   - f14 line spacing
   - a0 z
   - a1 records
   - a2 count
   - a3 revealed
   - t0 alignment: 0 left, 1 right (x is the right edge), 2 centred on x
   
   For each line, the drawer first measures the line width with the same
   advances (that is how right and centre alignment work). It then draws
   every character:
   - **Full-width (Shift-JIS lead 0x81-0x9F, 0xE0-0xFC):**
     - `0x110a30(code, 0x2eeb30)` returns the glyph from the built-in 24x24
       4-bit JIS font in the executable.
     - The glyph is uploaded with `0x22bcc8` into the 24x24 "font_tex"
       texture (set up by `0x110b48`).
     - It is drawn as a `size x size` sprite (texel setter `0x2331d8`,
       screen setter `0x233308`, emit `0x233380`).
     - **Advance = size.** The full-width space 0x8140 is skipped.
   - **Half-width 0x21-0x7E:**
     - Drawn from the "asciipfont" texture: built-in, 128x144 4-bit,
       18-pixel-high glyphs.
     - Texel rectangle `(u, v, width, 18)` comes from the **metrics table**
       (12-byte entries `{int u, int v, int width}` for 0x20..0x7E).
     - Drawn at `width * size/18` by `18 * size/18`.
     - **Advance = (width + 1) * size / 18.**
   - **Space 0x20 and any other single byte:** advance 10 * size / 18,
     nothing drawn.
   - **Line break 0x0A:** next line at `y + spacing + tallest size on the
     line`.
5. **Other drawers.** `0x110c78`, `0x111298` and `0x1117a8` draw plain
   strings (names, menus, backlog) with the same rules. `0x110c78` has an
   option (a2) that pads half-width characters to at least size/2.

**Nothing wraps or clips.** Only explicit line breaks move to the next
line, so text past the box simply continues past it.

**Box geometry (dialogue box, 640x480 virtual 2D space):**
- Text starts at x = 122. The next-page marker sits at about x = 505 and
  the frame at about x = 520.
- Usable width is **about 372 pixels** before the marker (396 to the
  frame).
- 3 lines at size 18. Measured from screenshots: the window maps the
  640-wide space to 597 pixels.
- The large narration balloon (`narration_box 1`) holds up to 20 lines.
- Centred free text (`text_slot ... 320, 65, 2`) has the screen width.

### Addresses on every disc

| Function | zettai | mugen-1 | mugen-2 | eigou-1 | eigou-2 |
| --- | --- | --- | --- | --- | --- |
| parse text (`\s \c \v`) | 0x1ce0a0 | 0x1e5ba8 | 0x1cfb40 | 0x1efae8 | 0x1e6788 |
| set message | 0x1ce6f0 | 0x1e61f8 | 0x1d0190 | 0x1f0138 | 0x1e6dd8 |
| draw message | 0x1cea28 | 0x1e6530 | 0x1d04c8 | 0x1f0480 | 0x1e7110 |
| ADV message slots | 0x1dd5c8 | 0x1f5c50 | 0x1df070 | 0x2000a0 | 0x1f6830 |
| **glyph drawer (messages)** | **0x111c70** | 0x111cd8 | 0x111cf0 | 0x111cd8 | 0x111cd8 |
| string drawers | 0x110c78, 0x111298, 0x1117a8 | 0x110ce0, 0x111300, 0x111810 | 0x110cf8, 0x111318, 0x111828 | as mugen-1 | as mugen-1 |
| Shift-JIS glyph lookup | 0x110a30 | 0x110a98 | 0x110ab0 | 0x110a98 | 0x110a98 |
| glyph upload (`font_tex`) | 0x22bcc8 | 0x245720 | 0x22d778 | 0x252420 | 0x246790 |
| font textures init | 0x110b48, 0x112198 | 0x110bb0, 0x112200 | 0x110bc8, 0x112218 | 0x110bb0, 0x112200 | 0x110bb0, 0x112200 |
| **ASCII metrics table** (95 x 12 bytes) | **0x2d96b8** | 0x2f5ec8 | 0x2db238 | 0x304148 | 0x2f6fc8 |
| asciipfont texture (0x30-byte header + 128x144 PSMT4) | 0x4e2140 | 0x4ff8e0 | 0x4e3de0 | 0x50e150 | 0x500d70 |
| script VM interpreter | 0x21cf58 | 0x236950 | 0x21ea08 | 0x243650 | 0x2379c0 |
| IDX path lookup (binary search) | 0x10a848 | 0x10a8a8 | 0x10a8c8 | 0x10a8a8 | 0x10a8a8 |

- The non-Zettai addresses come from masked signature matches of the
  Zettai function bodies (`research/rendering/tools/sigmatch.py`).
- The metrics table and texture are byte-identical on all discs (found by
  content).

## The font and its metrics

`tools/modding/ga2font.py`:

```sh
python3 tools/modding/ga2font.py dump  <SLPM elf> <out-dir>    # asciipfont.png, -4x.png, metrics.json
python3 tools/modding/ga2font.py tight <SLPM elf> <out.json>   # metrics trimmed to the glyph ink
python3 tools/modding/ga2font.py measure <metrics.json|elf> 18 "Some text"   # pixel width
```

`metrics.json` is `{"space_advance": 10, "glyph_height": 18, "glyphs":
{"A": [u, v, width], ...}}`, in game order.

- The stock widths are generous: e.g. `i` 5, `l` 5, `n` 9, `W` 14, `M` 14.
- `tight` measures each glyph's inked columns inside its cell and narrows
  37 of the 94 glyphs by 1-2 pixels.
- Notes on the glyphs:
  - `\` is drawn as ¥ (a Japanese ASCII font), and is the control-code
    escape in messages anyway.
  - There are no accented letters. Shift-JIS has no Latin-1, so é etc.
    need the font hook below plus repurposed codes.

## Optional runtime hook: replaceable metrics (and font)

**Implemented** in `native/compat/ga2_text.cpp`: a language pack's
`font/metrics.bin` (`ga2font.py pack metrics.json <mod>/font/metrics.bin`)
reaches the engine as `GA2_FONT_METRICS` (the launcher picks the
highest-priority active mod that has one). The table is validated (95
entries, every glyph cell inside the 128x144 font) and written over the
stock table, after checking that the stock table is at the expected
address, before the first drawer call. The design notes follow.

**Purpose.** A language pack can ship its own metrics.json (tighter
spacing, kerning-like adjustments, or a redrawn font with different
widths). The font *image* is already replaceable without code: the port's
texture replacement (`textures/`; the asciipfont should dump as a
`<hash>_128x144.png`, not yet checked) can supply a sharper or redrawn asciipfont at any resolution, as long as each
glyph stays inside its `(u, v, width, 18)` cell.

**Hook (pre-call only):**
- Override the message glyph drawer and the three string drawers (table
  above) with `PS2_REGISTER_GAME_OVERRIDE` / `runtime.replaceFunction`.
- On the first call, before calling the original, `memcpy` the mod's
  95 x 12-byte table to `rdram + metrics_va`, then call the original.
- Nothing else changes: the drawers read the table on every character, so
  the layout, the alignment and the measuring pass all follow.
- Registers are untouched. The hook needs no argument rewriting, so an
  early return of the recompiled function is harmless.

```cpp
template <size_t I> void glyphDrawer(uint8_t *rdram, R5900Context *ctx, PS2Runtime *rt) {
    if (!g_applied[I] && g_metrics) {            // loaded from the active language pack
        std::memcpy(rdram + kDiscs[I].metricsVa, g_metrics->data(), 95 * 12);
        g_applied[I] = true;                     // the table lives in the loaded ELF image
    }
    g_original<I>(rdram, ctx, rt);
}
```

- The ELF data is loaded once, so one write per boot is enough. Do it in
  the first drawer call rather than at registration, which runs before the
  ELF is in RAM.
- The four drawer entry points per disc are in the table above.

**What the table cannot change:**
- The space advance, 10 * size/18 (an immediate in the drawers).
- The glyph height (18 texels).
- Full-width advances (= size).

A narrower space would need a native replacement of the whole drawer (a
pre-hook cannot change an immediate inside the original). It is not needed
for readable English.

**Proof (gdb prototype)** of the same pre-call write:
- Script: `research/modding/gdb/gdb_font_table.py`, breakpoint on
  `FUN_00111c70`, writes `font/tight.json`'s table at 0x2d96b8 on the first
  call.
- `proof/font-metrics-hook-compare.png`:
  - top two rows: the game with the ink-tight table; "This is a legend I
    was told again and again when" now fits before the box edge;
  - row three: tight three-line text;
  - bottom two rows: the stock table (`runs/edit3`), where the same line
    is cut at "w".
- Run with:
  `GA2_FONT_METRICS=<json> GA2_WRAP="gdb -q -batch -x gdb_font_table.py --args" tools/run_owned.py ga2_zettai ...`.

## Button placeholders (reserved characters)

Status 2026-10-21. All five discs. Translated text can name a controller
button with `{circle}`, `{l1}`, ... (list and rules in
[translation.md](translation.md#button-placeholders-in-text)); the icon is the
one of the player's input device and follows it live.

**Design.** The build tools (`tools/modding/text_buttons.py`) turn each
placeholder into one or two reserved full-width characters, Shift-JIS
0xF040-0xF058 (private use U+E000-): one cell for round and square buttons, two
for the pill-shaped ones (left and right half of the icon). The message parser
and every drawer already pass such a pair on (lead bytes 0xE0-0xFC are
full-width everywhere), a full-width glyph advances `size` and is drawn as a
`size x size` sprite, so the line fit counts the width correctly and nothing in
the layout code is touched.

**Engine** (`native/compat/ga2_text.cpp`, `gs_texpacks.cpp`):
1. The Shift-JIS glyph lookup (`0x110a30` on Zettai; table above) is hooked.
   For a reserved code it answers with a monochrome 24x24 PSMT4 bitmap of the
   PlayStation symbol (`runtime/ga2_text_icons.h`, generated by
   `tools/modding/make_text_icons.py header`) in kernel RAM at 0x78000 (below
   the SDK pools' end 0x51000 + the invocation stack arena 0x60000-0x70000; 25
   glyphs x 288 bytes). The game uploads it to its 24x24 `font_tex` like any
   glyph. Without the GPU renderer that is what shows: a PlayStation symbol in
   the text colour.
2. With paraLLEl-GS the texture replacement hashes the region it samples
   (`gs_texpacks.cpp`). The hash of each placeholder bitmap is computed from the
   header at start, and a hit is replaced by the current style's icon
   (`native/buttons/<style>/<button>.png`, `_wide` for pills), fitted into the 1
   or 2 cells at 4x and cut into 96x96 pieces. A style change drops the cached
   textures, and the text is redrawn every frame, so the icons switch live.
3. The game colours every glyph sprite with the text's colour (dark in the
   dialogue box), which would turn a coloured icon black. The lookup hook
   therefore sets the colour's RGB to the neutral 0x80 for these glyphs (the
   sprite colour is four global bytes written by the colour setter just before
   the lookup; the hook finds them in the setter's code: `0x233280` on Zettai)
   and leaves the alpha, so fades still work. Done only when the replacement
   is active.

| Per disc | zettai | mugen-1 | mugen-2 | eigou-1 | eigou-2 |
| --- | --- | --- | --- | --- | --- |
| glyph lookup | 0x110a30 | 0x110a98 | 0x110ab0 | 0x110a98 | 0x110a98 |
| colour setter | 0x233280 | 0x24cce0 | 0x234d30 | 0x2599e0 | 0x24dd50 |
| colour bytes (parsed) | 0x2cf4b8 | 0x2ebab8 | 0x2d0fb8 | 0x2f9bb8 | 0x2ecb78 |

**Where it was verified** (`research/run58/prompts`): Zettai story dialogue and
battle balloon (all four styles, live switching with `GA2_BUTTONS_CYCLE=<s>`),
save-slot titles from a Program-sheet string, the software renderer
(`GA2_GS=cpu`), and Mugen 1's opening narration (wide icons, style cycle).
Mugen 2 and the Eigou discs use the same code at the addresses above (the
colour bytes parse on all five ELFs); they were not run.

**The games' own prompts** (2026-10-22): the message parser (Zettai `0x1ce0a0`,
mugen-1 `0x1e5ba8`, mugen-2 `0x1cfb40`, eigou-1 `0x1efae8`, eigou-2 `0x1e6788`; text
in a1) and the two string drawers (text in t0) are given a copy of their text in
kernel RAM (`0x7C000`, `0x7D000`) with each `○ × △ □` followed by ボタン or a
colon, and `Ｌ１`..`Ｒ３`, `L1`..`R3`, スタート, セレクト followed by ボタン, replaced by
the placeholder characters: the Japanese battle tutorial's "×ボタンを押して！"
shows the player's button (`research/run58/work/battleja3`, trace
`GA2_TEXT_TRACE=1`; `GA2_NO_PROMPT_SWAP=1` turns it off). Translations get the
same at build time (`text_buttons.auto`). The parser also swaps a message a save
restored in the language it was made in for the language pack's line
(`<pack>/text/<program>.msg`, `restore_text.py`).

**Not covered:** text drawn into pictures (texture replacement and
`buttons/map.json` cover those).

## Sharp text (vector glyphs at the internal resolution)

Status 2026-10-22. All five discs (same hooks, addresses in the tables above).
The games draw their text from low-resolution font bitmaps; at 2x/4x internal
resolution that looks blocky next to the sharp 3D and the 4x texture packs. With
sharp text on, every glyph texture the GS samples is replaced by the same
character drawn from the bundled vector font (`native/fonts/NotoSansJP-Medium.otf`,
shipped as `<app>/fonts/`; `GA2_UI_FONT=<file>` overrides) at the internal
resolution. Full-width (Japanese) text keeps the game's layout. Latin text is
laid out natively: the font's own advances and kerning, placed to 1/16 pixel
(below); line breaks stay the game's.

**Mechanism** (runtime `gs_hitext.cpp`, `gs_texpacks.cpp`, game side
`native/compat/ga2_text.cpp`):
1. The Shift-JIS glyph lookup hook (the one the button placeholders use) reports
   `(code, bitmap)` of every full-width glyph the game fetches to
   `ga2_hitext::noteGlyph` (one flag per code, so repeated lookups cost a load).
   The bitmap's decoded-texture hash is registered (white, alpha = level * 8, the
   same hash the placeholder icons use) and a worker thread renders the glyph.
2. When the GS samples a 24x24 region whose hash is registered, the texture
   replacement answers with the vector glyph (96x96 at 4x scan-out, 48x48 at 2x;
   white with the game's own full intensity, so the text colour, shadow and fades
   work as before). Texture packs and placeholder icons keep precedence.
3. The drawers (always hooked now) report the half-width metrics table in use
   (stock, or the language pack's `font/metrics.bin`). The games sample each
   Latin character through a region clamp to its atlas cell, so the GS asks for
   one small 4-bit region per character (`w x 18`: 9x18, 5x18, ...). A region
   whose position and width are a metrics cell, in a texture that looks like the
   atlas (128 wide, ink inside the cells, decoded from the same registers), is
   answered with that character drawn from the vector font at 4x/2x, at its own
   shape with its ink centred in the cell (narrowed only if wider than the
   cell). The rest of the atlas (the solid block below the last row) is never
   replaced.
4. **Native text.** With sharp text on, the port draws the text itself instead
   of the games' glyph sprites. The drawers place half-width characters by the
   bitmap font's widths + 1 and every space 10 pixels apart, in whole pixels,
   and the GS snaps a sprite's coverage to whole PS2 pixels, so per-glyph
   sprites can be neither spaced nor placed finely. The message drawer and the
   two string drawers (`0x111c70`, `0x110c78` without padding, `0x111298` on
   Zettai) are laid out again before they run: Latin characters with the font's
   advances and kerning (`ga2_hitext::latinLayout`), full-width ones at the
   game's advance, keeping the drawer's anchor (left, right edge or centre) and
   its line breaks. Each piece of a line in one colour (the typewriter's fading
   characters are pieces of their own) is rendered whole from the vector font at
   the internal resolution, Japanese at the size it is drawn and Latin with full
   descenders (`ga2_hitext::noteRun` / `runImage`). The original drawer then
   draws one "ticket" character per piece: a reserved code (lead 0xF8, trail
   0x40 + slot) whose 24x24 bitmap, written to a ring of 184 slots at
   0x52000 (kernel RAM; the games upload glyphs by DMA reference, so a slot
   must outlive the frame), the texture replacement answers with the piece's
   image. The screen-rect setter (`0x233308`; 0x88 past the colour setter on
   every disc) hook moves each ticket sprite onto its piece's rectangle (by
   difference, so the widescreen HUD placement still applies). The games'
   texel setter puts u, v half a texel in, so a ticket stretched across a piece
   shows its image from 1/48 of the piece in, and the glyph texture is clamped
   at texel 23 of 24: the glyphs are rendered 1/48 further in and the piece is
   48/45 of its content, so everything drawn lies where the image maps cleanly
   (exact texel coordinates instead make the GS read past the image's start,
   next to the glyph texture). Each
   piece (its rectangle, colour and layout) is also reported to the
   presentation (`ga2_hitext::notePiece`, below). The GS still
   composes the text, so colours, fades, layers and VR work as before. Button
   icons and characters the font lacks stay the game's glyphs at their new
   place. The string drawers' return value (end x / width), which callers use
   to place what follows (the dots after a speaker's name), is the native one.
   The number drawer keeps its fixed digit slots. Measured over the English
   script: wrapped at 372 pixels with the game's widths, no dialogue line
   overflows with the font's (the 12 that do, unbreakable "Momomomo...!" lines,
   overflow with the game's widths too). `GA2_NATIVE_TEXT_LAYOUT=0` keeps the
   game's layout and glyph sprites.
5. **Window-resolution text** (`ga2_present.cpp`, 2D pictures above 640x448
   on the desktop). The GS's rendering of text is bound to the internal
   resolution and goes through the GS's sampling, the 2D filters and the
   scaling to the window; text is instead drawn again at the window's own
   pixels, like any desktop text:
   - The pieces of the last frame are drawn into a layer at the internal
     resolution exactly as the GS drew them (same images, colours, the GS's
     half-pixel sample offset), so it is known what the GS put where.
   - Occlusion, per PS2 pixel: inside the strokes (coverage k >= 0.8) a
     visible text pixel lies within 1 - k of colour * k, whatever lies behind;
     each stroke sample is matched within half a PS2 pixel (the GS's own
     rendering has sub-pixel combs), and an area vote (PS2 pixels within 3)
     decides, so a menu or a fade over the text counts and a stray mismatch
     does not.
   - Where not occluded, the text's footprint (half a PS2 pixel wider) is
     filled from its surroundings: the 2D filters, debanding and the scaling
     then work on the art alone.
   - Each piece is rendered at the window's scale from the vector font and
     drawn over the shown picture pixel for pixel, in its colour, except where
     occluded (there the GS's text stays).
   `GA2_TEXT_OVERLAY=0` keeps the GS's text; `GA2_TEXT_OVERLAY_DEBUG=1` draws
   the overlay in magenta over the picture with the text taken out;
   `GA2_TEXT_OVERLAY_DUMP=<dir>` writes the layers now and then. In 3D scenes
   and in VR the GS's text is shown.
6. Software GS (`GA2_GS=cpu`) never runs the replacement; nothing changes there.

**Fit.** Calibrated by comparing all 6,355 ideographs of the games' font with
Noto Sans JP Medium (`tools/modding/make_sjis_table.py` makes the Shift-JIS to
Unicode table, code page 932):
- Full-width: the game's glyphs are the em box rendered into 24x24: em = 23.8
  px, pen origin x = 0.05, baseline row 21.05; kanji ink areas match the
  game's within 1.4% (median) and centres within 0.23 px, so one transform is
  used for every character. Only characters whose ink centre is more than
  0.75 px from the game's glyph (punctuation the game font draws elsewhere) are
  moved to the game's position; nothing is rescaled.
- Half-width: one vertical transform (em 17.1 px, baseline row 15.75 of the
  18-pixel cell), each glyph at its own width (with `GA2_NATIVE_TEXT_LAYOUT=0`:
  fitted to the game's ink box, stretch 0.5 to 1.15), descenders longer than the game's
  are compressed below the baseline, glyphs drawn at another height than the font
  does (tilde, caret, grave accent) go to the game's height. The backslash cell is a yen sign.
- Characters the font lacks keep the game's own glyph.

**Settings.** Launcher/in-game option "Sharp text" (Automatic = on at 2x and 4x
internal resolution, On, Off; live) or `GA2_SHARP_TEXT=0|1|auto`.
`GA2_HITEXT_DUMP=<dir>` writes each replacement glyph beside the game's own
(PGM), `GA2_HITEXT_BOLD=<px>` / `GA2_HITEXT_BOLD_LATIN` grow the strokes.

## Word wrapping in the build tool

`ga2scripts.py build ... --wrap PIXELS [--max-lines 3] [--font-metrics metrics.json]`:

- **Which strings:** every message string (text argument of `msg`,
  builtin 0x00) that contains Latin letters and is wider than PIXELS in
  some line. Strings without Latin text (the Japanese originals) are never
  touched, so a dump wraps to the same bytes when nothing is translated.
- **Widths:** measured exactly as the game does (table above). The default
  is the disc's own table, read from the boot ELF in the image; pass
  `--font-metrics` when a pack ships its own. Control codes take no width,
  and `\sNN` scales what follows.
- **Breaks:**
  - Greedy, at spaces only; an existing `\n` is a hard break.
  - A single word wider than the budget is left as it is.
- **Warnings** are printed for messages with more than `--max-lines` lines
  (3 for the dialogue box).
- **Budget:** use **372** for dialogue (stops before the next-page marker;
  396 reaches the frame).
  - A whole script of narration in the large balloon or centred text can be
    built separately with a larger budget. The budget is per build; split
    the dump directory if needed.

Proven in the game: `proof/new-scene-file-zettai.png` (`runs/newscene`).
The new scene's English paragraphs were written as single lines in the
`.gasm` and wrapped by `--wrap 396`. The third line reaches the
next-page marker, which is why 372 is the recommended dialogue budget.

## Remaining limits

- Texture replacement of the asciipfont was not tried with a redrawn font
  yet. The cell layout must stay the same unless the metrics hook also
  ships.
- Characters outside ASCII (accents, typographic quotes) have no glyphs.
  Options:
  - map them to unused ASCII codes plus metrics and a redrawn texture;
  - or use full-width glyphs (fixed advance = size).
- Text drawn into textures (title buttons, eyecatches, UI images) is
  graphics work: dump and replace through `textures/`.
