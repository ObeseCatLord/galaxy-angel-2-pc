# Playing on the Steam Frame

The Linux ARM64 build runs natively on Valve's Steam Frame (SteamOS on
ARM64). No emulation is involved. You can play flat, on a screen in the
headset, or in VR.

> **Status:** this build follows Valve's Steam Frame documentation and has
> been tested on ARM64 Linux. It has **not yet been tried on a Steam Frame**.
> Please report what works and what doesn't.

## Installing

1. Download `galaxy-angel-2-pc-<version>-linux-arm64.tar.xz` from
   [Releases](../../../releases) on the headset, or copy it over.
   - To copy it, use Desktop Mode, or SSH once Developer Mode is on.
   - Extract it somewhere in your home folder, for example
     `~/Games/galaxy-angel-2-pc`.
2. Copy your disc images (`.iso`) into `~/.local/share/ga2/isos/`. The
   launcher creates this folder the first time it runs.
3. In Desktop Mode, open Steam, choose **Add a Game > Add a Non-Steam Game**
   and pick `ga2` from the folder you extracted.
   - Leave the compatibility tool unset: this is a native build.
   - It needs glibc 2.31 or newer and the usual X11/OpenGL libraries.
     SteamOS has all of them, so it also runs inside the Steam Linux
     Runtime.
4. Start **ga2** from your library.

## The launcher with Frame controllers

The Frame controllers act as a gamepad in flat games, so the launcher works
without a mouse or keyboard:

| Button | Does |
| --- | --- |
| D-pad / left stick | choose a game |
| A or B | start it |
| Y | add the disc images from the `isos` folder |
| Menu (Start) | Options |
| View (Select) | Mods |

Disc images in `~/.local/share/ga2/isos/` (or in an `isos` folder next to
`ga2`) are added automatically when the launcher starts. Press Y after
copying more.

In the games, the controllers are a PS2 pad: the face buttons are Circle,
Cross, Square and Triangle by position, and the shoulders are L1/R1/L2/R2.
Button prompts show Xbox-style icons, which match the Frame's A/B/X/Y.
Pressing both stick clicks opens the cheat menu, and its **Settings** row
holds the options (F1 and F2 on a keyboard). Message skip (hold Ctrl)
needs a keyboard.

## Settings for the Frame

- **Internal resolution:** Native or 2x. 4x is too heavy for a mobile GPU.
- **Frame rate:** 60 (PS2) is the safe choice. Higher rates draw more frames
  and cost battery.
- **2D picture filter:** Smooth or Bilinear. Anime4K HQ is too heavy.

## VR

1. In the launcher's Options, set **VR mode** to **On**.
2. Start a game. It opens in SteamVR's OpenXR runtime on the headset, with
   the same big screen and 3D scenes as on PC VR (see
   [PLAYING.md](PLAYING.md#vr)).

Details for the Frame:

- **OpenXR loader:** the port uses the system's `libopenxr_loader.so.1`, or
  SteamVR's own loader at `/opt/steamvr/bin/linuxarm64/libopenxr_loader.so`.
  `GA2_OPENXR_LOADER=<file>` overrides both.
- **Controllers:** the port asks for the Frame controller profile
  (`XR_VALVE_frame_controller_interaction`):

  | Frame | PS2 |
  | --- | --- |
  | B / A / X / Y (right) | Circle / Cross / Square / Triangle |
  | D-pad (left) | D-pad |
  | Left stick | left stick and D-pad |
  | Right stick | right stick |
  | Bumpers | L1 / R1 |
  | Grips | L2 / R2 |
  | Stick clicks | L3 / R3 (both together: cheat menu with Settings) |
  | Menu (right) | Start when tapped, Select when held |
  | View (left) | Select |
  | Triggers | point and click on the screen; Circle in 3D scenes |

  SteamVR's binding screen can change any of these. Without the Frame
  profile, SteamVR presents the controllers as Oculus Touch, which the port
  also binds.
- **Graphics:** the port renders through OpenGL (GLX on SteamOS's Xwayland,
  Zink on the Frame) with an sRGB swapchain. It uses its own depth
  buffers, not the runtime's depth swapchains.
