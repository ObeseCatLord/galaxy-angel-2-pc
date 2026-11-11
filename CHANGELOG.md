# Changelog

## 0.9.0

- English translation included (Options > Language > English): story
  scripts, character names, battle dialogue, menus, the text inside the game
  programs, the staff roll and the translated pictures. It ships as a
  language pack in `mods/ga2-en`.
- Widescreen battle HUD (16:9 to 21:9):
  - unit markers stay on their units and keep their shape
  - the minimap's blips stay inside the minimap
  - the message balloon stays beside its portrait
- Sharp text and HD button prompts now reach the screen in 2D story scenes
  at 2x and 4x. Those scenes are composed off screen, and the copy to the
  screen used to drop back to 640x448.
- The Anime4K filter at 2x and 4x keeps the full-resolution text and icons
  instead of redrawing them from a 640x448 copy.
- New high-resolution button prompts (256x256, sharp up to 8x), also for
  PlayStation controllers, which used to get the games' own low-resolution
  icons. The dialogue box's Auto, Back and Menu words are redrawn sharp too.
- Button prompts blend into the art behind them. The PlayStation icon used
  to be covered with a flat block of colour.
- Debanding is on by default (Low) in 2D and 3D scenes, and text and other
  sharp edges are left untouched.
- The README is also in Japanese (README.ja.md).
- The launcher also reads mods from a `mods` folder next to it, so a release
  can ship language packs. A pack installed in the data folder still takes
  precedence.
- Language packs' save-message tables are keyed by digests, so a pack
  carries none of the games' original text. Older packs still work.
- Steam Frame:
  - the Frame controller profile, including its D-pad
  - SteamVR's own OpenXR loader
  - disc import from an `isos` folder
  - a launcher that works entirely with a gamepad
  - a guide (docs/STEAM_FRAME.md)
- VR controller table in the README.
- Linux builds need only glibc 2.30 and libGL (no libGLU or libatomic).

## 0.8.0

The first public release. All three games are playable on all five
discs.

- One launcher for the trilogy, with automatic disc changes and saves that
  carry over between the games.
- 2x and 4x internal resolution, widescreen up to 21:9 (hor+ 3D), and frame
  rates up to 240 fps at the original game speed.
- Keyboard, gamepad and mouse play, including battles. Button prompts
  follow the device you're using.
- Message skip (hold Ctrl) and movie skip (Esc).
- Language packs, file mods and HD texture replacement.
- 2D filters (Smooth, Anime4K, sharp pixels), debanding, and the PS2 dither
  turned off.
- Cheats menu (F1) and in-game settings (F2).
- VR through OpenXR, with stereo 3D scenes.
- Builds for Windows x86-64, Linux x86-64 and Linux ARM64.
