# Playing Galaxy Angel II PC

## What you need

Your own disc images (`.iso`) of the Japanese PlayStation 2 releases:

| Game | Discs |
| --- | --- |
| Galaxy Angel II: Zettai Ryouiki no Tobira | 1 |
| Galaxy Angel II: Mugen Kairou no Kagi | 2 |
| Galaxy Angel II: Eigou Kaiki no Toki | 2 |

A GPU with Vulkan 1.3 (the PS2 graphics run on the GPU through paraLLEl-GS).

## First start

1. Run `ga2` (`ga2.exe` on Windows).
2. Drag your `.iso` files onto the window. Each disc is checked once and
   remembered; the images stay where they are. Without a mouse (Steam
   Frame, Steam Deck game mode, a TV), put them in the `isos` folder of the
   data folder (below) instead: they are added when the launcher starts, or
   when you press R / Triangle (Y).
3. Pick a game. Discs are never swapped by hand: the port finds the right disc
   when a game asks for one (Mugen's and Eigou's second discs, the Zettai disc
   that unlocks the Extra Route, and so on), so add every disc you own up front.

## Controls

| PS2 | Keyboard | Gamepad | Mouse |
| --- | --- | --- | --- |
| D-pad | Arrows / WASD | D-pad | wheel = up/down (menus), on-screen pad |
| Circle (confirm, advances messages) | Enter (or C, Esc) | right face button | left click |
| Cross (cancel) | X or Space | bottom face button | right click |
| Square / Triangle | Z / V | left / top face button | on-screen pad |
| L1 / R1 | Q / E | shoulders | on-screen pad |
| L2 / R2 | Left / Right Shift | triggers | on-screen pad |
| Start (pause) | P | Start | on-screen pad |
| Select (system menu) | Tab | Back | middle click |
| L3 / R3 (stick clicks) | R / F | stick clicks | in battles: back / forward button (see below) |

Hold **Ctrl** to skip messages and press **Esc** to skip a movie (below). F11 or Alt+Enter switches fullscreen (Alt+Enter is not Circle). **F2** opens the settings menu, **F1** the cheat menu (see Options and Cheats).

### Skipping messages

Enter advances a message (it completes the typing first, then turns the page).
**Hold Ctrl** (either one) to fast-forward through the story: the game is
given Circle presses every other frame, 30 a second, so the text completes
and the page turns about as fast as the game takes presses (the first
chapter's several hundred messages take about a minute and a half). It works
with a gamepad plugged in too.

Skipping only runs while a story script is on and nothing needs a decision:

- it stops at **choices** (the options stay up while you hold Ctrl; release it
  and choose, then hold it again), and never picks one;
- it does nothing in the **system menu**, the **backlog**, the options, the
  load and save screens, the title, the ship map or a battle;
- pressing any other button while holding Ctrl (Tab for the system menu, Z for
  the backlog, V for Auto, X, P, ...) stops the skipping until you let go of
  Ctrl and press it again, so a menu opened mid-skip is never confirmed.

Transitions and effects between messages are not shortened (Circle does not
cut them in the game either). `GA2_NO_SKIP=1` turns the feature off.

### Skipping movies

Press **Esc** during a movie (the hangar launch before a battle, the story
movies) to skip it. This is the games' own movie skip, the Start button (P on
the keyboard, Start on a gamepad, which work too): the game ends the movie as
if it had played out and carries on to what follows (the battle, the next
scene). Esc stays the Circle button everywhere else.

### Playing with the mouse

The whole game can be played with the mouse alone:

- **Menus follow the pointer**: on the title screens, the system menu, yes/no
  prompts and story choices, pointing at an item selects it and clicking
  chooses it.
- **Dialogue**: click anywhere to advance; the Auto, Back (backlog) and Menu
  buttons next to the text box work as buttons.
- **On-screen pad**: move the mouse and a pad appears in the black bars beside
  the picture (D-pad, the four face buttons, L1/L2/R1/R2, Select, Start).
  Click its buttons for anything without a menu, such as scrolling the ship
  map with the D-pad.
- Right click cancels (Cross), the wheel moves up/down, the middle button
  opens the system menu.
- **Battles**: the mouse works in the tactical map, the cockpit and the camera
  view. Everything goes through the game's own buttons and functions, so
  sounds, restrictions (busy units, the tutorial's gates) and the story's
  scripts apply (the tutorial's disabled buttons stay disabled for the mouse too),
  and keyboard and pad play is unchanged (the mouse takes over
  only while you move or click it, and it follows a remapped pad layout).

  *Tactical map* (the grid view with the unit icons; a gamepad opens it with
  L3 from the cockpit, the keyboard with R): the map's cursor is normally the
  centre of the screen; with the mouse the cursor ring follows the pointer.

  | Mouse | Does |
  | --- | --- |
  | Left click on a unit icon | select it (its unit panel appears while you point at it) |
  | Left click on empty map / an enemy / a friend | move the selected unit there / attack / support (the unit itself: Stay) |
  | Ctrl + left click | the same for all your angels (Square, then Circle) |
  | Double click on a unit | the camera focuses on it (the game's own focus) |
  | Right click | cancel (Cross) |
  | Right drag | pans the map: the ground follows the pointer |
  | Pointer at the picture's edge | scrolls the map (the game's D-pad scroll; set `GA2_BATTLE_EDGE_SCROLL=0` to turn it off) |
  | Wheel | zooms the map camera (what R1 / R2 do), toward the point under the pointer |
  | Middle click on a unit | its H-Weapon (Triangle) |
  | Middle button held on empty map | L2: the game's angel list ("Direct Jump Menu") opens; the wheel moves its cursor, releasing the button jumps the camera to that angel |
  | Ctrl + middle click | Start (pause; then the middle button is Select, the system menu) |
  | Back button | the pilot (cockpit) view (L3) |
  | Ctrl + right click, or the forward button | camera view of the unit under the pointer (R3) |

  The keyboard keeps working with the pointer: point at a unit and press V
  (H-Weapon), F (camera view), Z (order all), R (cockpit), Shift (angel list).
  The pointer takes over while you move it or click; after six seconds
  without moving, and while it is outside the picture, the map goes back to
  its centre cursor for pad and keyboard play.

  *Cockpit* (the pilot's view): the reticle follows the pointer; the left
  button held fires, the right button held charges and, released, fires the
  charged shot; the middle button (or the back button) leaves for the map
  (L3); Ctrl + middle click pauses.

  *Camera view* (a unit seen through its own camera): dragging with either
  button turns the camera, the wheel zooms, a left click selects the unit for
  orders and returns to the map (Circle), a right click leaves (R3).

  *Menus in battle*: the pause menu (Start; the options and quit items) and
  the other menus follow the pointer like the rest of the game: point at an
  item and click.

## Options (launcher: Options button or O; in a game: F2)

Saved in `settings.ini` in the data folder. The launcher passes them to the
game when it starts it; the same options can also be changed while playing
(next section).

| Option | Choices |
| --- | --- |
| Language | Japanese / English: the window title, and which language pack mod is used |
| Internal resolution | Native, 2x, 4x: detail of the 3D scenes (the 2D art is native 640x448 art) |
| Display | Window / Fullscreen (F11 or Alt+Enter while playing) |
| Screen shape | 16:9, 16:10, 21:9 ultrawide, 4:3, or fill the window |
| 3D widescreen (hor+) | On: 3D scenes show more to the sides at 16:10, 16:9, 21:9 or the window's own shape (Fill), same height as on the PS2; HUD, menus and text keep their proportions, and the battle HUD sits at the screen edges (radar top left, communications bottom left, unit panel and pilot portrait bottom right) |
| Sides of 4:3 scenes | Black, blurred picture, or the game's own space skybox (rendered from your disc after import; needs Python 3 with Pillow) |
| 2D scene size | Fill, or whole multiples (2x, 3x) for crisp pixels; 3D scenes always fill |
| 2D picture filter | Bilinear, Smooth (removes the art's dither grain), Anime4K, Anime4K HQ, Sharp pixels, Nearest; 2D scenes only |
| Frame rate | 60 (PS2, default), 75, 90, 100, 120, 144, 165, 170, 175, 180, 200 or 240 frames per second; **Monitor refresh rate** follows the monitor the window is on (read at the start and again when you move the window to another monitor or its rate changes; a monitor at 60 Hz or below means the PS2 rate, above 240 is capped at 240); **Custom** is any whole number from 61 to 240 (launcher: type it into the box, or use the mouse wheel over it; in game: Left / Right change it by 1, Shift by 10, or type the digits and press Enter; Enter or PageDown goes on to the next choice). The whole game (story, menus, ship map, battles) is drawn that often while it keeps its own speed. The rate drops automatically when the PC can't keep up, so the game never runs slower than at 60; can also be changed while playing (F2). The battle's 30 fps camera views keep 30 fps. In VR the rate follows the headset. Stored in `settings.ini` as `fps=120`, `fps=173` (custom) or `fps=monitor` |
| PS2 dithering | Off (default) or the original checkerboard |
| Button prompts | Automatic (follows the controller or keyboard you use), PlayStation, Xbox, Keyboard |
| VR mode | Off / On (see below) |
| On-screen pad | Off (default) / clickable pad buttons beside the picture |
| Dump textures (modding) | Saves every texture shown, for making mods (docs/MODDING.md) |

### Changing options while playing

Press **F2** in a game (or click both sticks on a gamepad or in VR, then choose
**Settings** in the menu that opens) to see the same options. Up / Down choose,
Left / Right change the value (Enter / Space / Circle: next value; with the mouse
click the left or right half of the value, or turn the wheel over a row), F2,
Esc or Cross close. A row is highlighted only while the mouse is over it or after
you chose it with the keys or pad (until the mouse moves again); Enter and Left /
Right act on the row the keys chose, a click on the row under the pointer. The
Custom frame rate takes digits and Enter (Esc drops them, Backspace deletes one);
PageUp / PageDown are previous / next value on any row. The launcher's screens
follow the same rule. The game does not see your input while the menu is open. Every
change is saved to the launcher's `settings.ini` at once, so the launcher and the game
always agree; the file is `GA2_DATA_DIR/settings.ini` (the launcher sets it), or
`~/.local/share/ga2/settings.ini` (`%APPDATA%\ga2` on Windows) for an engine
started without the launcher.

- **Take effect at once:** Display, Screen shape (the window takes the new shape
  too), 3D widescreen (hor+; the 3D cameras follow within a frame), Sides of 4:3
  scenes, 2D scene size, 2D picture filter, PS2 dithering, Debanding, Button prompts,
  On-screen pad, and the Frame rate (any choice, up to 240). Dump textures applies to
  the textures the game loads from then on.
- **Marked "restart" and applied the next time a game starts:** Language, Internal
  resolution and VR mode. The menu shows such a value in orange
  with "after restart" until the game is restarted. After a disc change inside a
  game (the next disc's engine) the options as saved are used.
- In VR the menu is drawn like the cheat menu (on the screen, or on a panel in 3D
  scenes): left stick up / down / left / right, A or the trigger = next value, B closes.

## VR

1. Start your VR runtime: SteamVR, or Monado (e.g. through Envision). It must
   be the active OpenXR runtime.
2. In the launcher's Options set **VR mode** to On, then start a game.

The game appears on a large screen floating in the game's own space sky.
Each controller shows a clear holographic reticle where your hand is.
Point either controller at the screen: a blue laser shows where you point,
and menus follow it like the mouse; the **trigger** clicks. The monitor keeps
showing the flat game.

Every PS2 input is a separate action, so SteamVR's binding screen lists them
all and you can rebind any of them. By default, on Quest, Rift, Pico, Reverb
G2 and Index controllers:

| Controller | PS2 |
| --- | --- |
| A / B (right hand) | Circle / Cross |
| X / Y (left hand; on Index the left A / B) | Square / Triangle |
| Left stick | left analog stick and D-pad (also scrolls the ship map) |
| Right stick | right analog stick (turns the camera when you watch a unit); pushed left / right also L2 / R2 |
| Grips | L1 / R1 (a light squeeze of R1 raises the battle map camera, R2 lowers it) |
| Left / right stick click | L3 (cockpit from the map) / R3 (watch a unit); both together open the cheat menu (which has a Settings row) |
| Menu button | Start when tapped, Select when held (Index: hard press on the left trackpad; right trackpad = Select) |
| Trigger | clicks on the screen; in 3D scenes Circle (select on the tactical map, advance dialogue); in the cockpit it fires; in the unit camera view the triggers zoom |

Windows Mixed Reality and Vive wands have no face buttons: the right
trackpad's click is Circle / Cross / Square / Triangle by direction. The
cheat menu appears in front of you (on the screen, or on a panel in 3D
scenes): move with the left stick, choose with A or the trigger, close with
B. Hold **both grips** (on the Steam Frame, both bumpers) for a second to
bring the screen back in front of you.

The 3D scenes (space flights, the ship map, battles) surround you instead:
the game draws them from your head's position and direction in true stereo,
and you can look and lean around (6DoF). Optional (test setting, environment
variable `GA2_VR_HUD_PANEL=1`): the game's 2D overlay (dialogue box, battle
HUD) is shown on a flat panel 1.6 m in front of you instead of at the depth
of the background. Your hands stay visible there as
their reticles with a short guide beam. The 2D story scenes, menus and
movies stay on the screen. If the world feels too big or too small, set
`GA2_VR_SCALE` (game units per metre, default 50) before starting; set
`GA2_VR_STEREO=0` to keep every scene on the screen.

**Pointing in battles.** On the tactical map (a 3D stereo scene) the hand you
used last is the cursor: aim at a unit and pull the trigger (or press A) to
select it, then aim at empty map and pull the trigger to send it there (Move),
or at an enemy to attack; B (Cross) cancels, X (Square) orders all angels,
Y (Triangle) is the H-Weapon, as with the buttons. The game's own order logic
does the rest. This is built from the game's own camera and the controller's
aim, checked with synthetic cameras (the hand and mouse paths agree to 0.0001
map units), but not yet tried on a headset. The other battle views (cockpit,
chase and camera views) play with the buttons as in the table above.

## Cheats

Press **F1** (or click both sticks) in a game to open the cheat menu (its last row,
**Settings**, leads to the options above):

- Skip this battle (ends the battle being fought as a win with the best
  result the battle gives),
- Invincible units, Infinite energy and spirit, One-hit kills, Stop the time
  limit.

The game is paused for input while the menu is open.

## Mods

The launcher's **Mods** screen (M) turns installed mods on and off and orders
them; translations come as language packs that follow the Language option
(how to install one: [LANGUAGE_PACKS.md](LANGUAGE_PACKS.md)). How to make
mods: [docs/MODDING.md](MODDING.md).

## Where things are

The data folder is `~/.local/share/ga2` on Linux and `%APPDATA%\ga2` on
Windows:

- `memcards/` - your saves (one memory card shared by the three games, so
  Zettai and Mugen saves carry over as on the PS2)
- `logs/` - one log per game, useful when reporting a problem
- `isos/` - disc images to add without drag and drop
- `mods/` - installed mods (see Mods above)
- `assets/skybox/` - the space skies rendered from your discs
- `dumps/textures/` - textures saved by the Dump textures option
