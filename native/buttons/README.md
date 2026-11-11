# Button-prompt icons

The port's own icons (no game art), drawn by
`tools/assets/make_button_icons.py` in the look of the games' prompts:
`<style>/<button>.png`, 256x256 (sharp up to an 8x icon scale), for the
`xbox`, `keyboard` and `vr` styles; buttons are `circle cross square triangle l1 r1 l2 r2 start select
up down left right` (the PS2 button each icon stands for). The keyboard
style also has `<button>_wide.png` (l1 r1 l2 r2 start select): a wide keycap
the engine uses instead for pill-shaped spots (width at least 1.5 times the
height, e.g. the dialogue box's Menu button), so `TAB` stays readable there.

`map.json` lists where the games' textures show PS2 button icons (texture
region hash from a texture dump, icon rectangle, button); the engines draw
the icon for the player's input device there (docs/MODDING.md), in every
style: with a PlayStation controller the `playstation/` set replaces the
games' own low-resolution icons.

The button `label:<name>` draws `labels/<name>.png` instead, the same in
every style: the port's own lettering of a prompt word, for the dialogue
box's Auto, Back and Menu (11 pixels tall in the games' textures). Its
rectangle is the games' lettering plus a one-pixel margin; the label fills
the lettering's box. `make_button_icons.py` draws the labels too (white, Noto
Sans Bold, as wide as the games' word). The button
`blank` leaves the spot empty instead (any style but PlayStation): the
dialogue box's continue marker pulses a glow copy of the puck
(`if_tbox_005_b`, `006_b`) over the puck itself, and an icon there flickered
over the puck's icon.

The keyboard icons show the keys of the port's keyboard map (ps2_pad_keys.h
in the runtime; docs/PLAYING.md): Circle = ENTER (C and Esc also work),
Cross = X, Square = Z, Triangle = V, L1/R1 = Q/E, L2/R2 = left/right Shift,
Start = P, Select = TAB, L3/R3 = R/F, arrows = D-pad. Enter is the confirm
button because it advances messages; Start moved to P (Alt+Enter stays the
fullscreen shortcut and presses nothing in the game). Change a key here and in
`make_button_icons.py` together, then draw the icons again with
`python3 tools/assets/make_button_icons.py native/buttons`.

## What is covered

Every entry is a whole-texture hash (the games draw these UI textures whole),
found by decoding all UI textures of the five discs and looking for the games'
glyphs (a coloured symbol on a dark disc, and the grey `L1/R1/L2/R2` pills),
then confirmed in game screenshots where the screen can be reached.

| Screen | Textures (`common/` AGI names) | Prompts |
| --- | --- | --- |
| Dialogue box | if_btn_000, if_btn_001, if_tbox_005/006(_b), if_okr_000/001 | Auto, Back, Menu, continue puck |
| Backlog | if_log_001 | Read, Jump, Return |
| Options (all games) | if_opt_000, if_opt_300, slg_opt_000a, if_opt_016/017 | default, OK, cancel, L1/R1 page tabs |
| Load/save | if_kan_000 | OK, cancel |
| System/disc messages | if_dbox_m126..m129 | inline OK/cancel in the disc-check boxes |
| Ship map | if_ido_000 | R1/R2 floor panel |
| Pilot select, map loading | if_slg_psel_000, if_nowload_003 | OK, start |
| Tutorials | if_slg_tut_015, if_tut002, tut_bun04..07 | controller, back to title, attack prompts |
| Gallery (extras) | if_omk015/016, if_omk022..025, if_omk504/512 | OK, cancel, L1/R1/L2/R2 page |
| Battle option buttons | slg_optbu0/2/4 | default, OK, cancel |

## Prompts that are text

Some prompts are characters in the games' text: the script line
"赤く表示された施設の上で○ボタンを押すと..." (Zettai ship-map tutorial,
`m010_1005_s0`), the battle tutorials in each stage's `slg_opdemo.dat` (○ × △
□ L1 R1 L2 L3 R3 in the lines), and the movie hint "×：再生終了" in the
executable. These cannot be replaced by texture mapping, so the translation
writes a placeholder (`{circle}`, `{cross}`, `{l1}`, ... see
docs/modding/translation.md) and the engine draws the icon of the current
input style there: the reserved character's glyph bitmap is recognised by the
texture replacement and replaced with `<style>/<button>.png` (or `_wide`),
cut into one or two 1-em cells. The text-only buttons `l3`, `r3`, `dpad`,
`lstick`, `rstick` and the whole `playstation/` set (the port's drawn version
of the games' own prompts, used in text and over the games' icons) are drawn by `tools/modding/make_text_icons.py icons`
(`--force` to draw them again after `make_button_icons.py`).
