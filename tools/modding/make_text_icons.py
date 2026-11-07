#!/usr/bin/env python3
"""Art and glyph data for the button placeholders in text (text_buttons.py).

  make_text_icons.py icons <native/buttons>   draw the icons the placeholders
        need and native/buttons does not have yet: the playstation style (the
        games' own prompt look: coloured symbols on dark discs) and, in every
        style, l3 r3 dpad lstick rstick.  Existing PNGs are kept unless
        --force.  Uses tools/assets/make_button_icons.py for the look.
  make_text_icons.py header <out.h>           the engine's glyph table
        (ps2xRuntime/include/runtime/ga2_text_icons.h): for every reserved
        character a 24x24 4-bit monochrome bitmap of the PlayStation symbol.
        The game draws that bitmap as an ordinary glyph (so without the GPU
        renderer the PlayStation symbols show, in the text colour); with it,
        the texture replacement recognises the bitmap and draws the input
        style's full-colour icon instead.

Needs Pillow and a bold monospace font (--font, default Hack-Bold).
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "assets"))

import make_button_icons as mb  # noqa: E402
import text_buttons as tb       # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

S = mb.S
WHITE = (240, 244, 255, 255)
PS_COLORS = {"circle": (255, 85, 85, 255), "cross": (120, 170, 255, 255),
             "square": (255, 150, 210, 255), "triangle": (90, 225, 170, 255)}
STYLES = ("playstation", "xbox", "keyboard", "vr")
NEW_BUTTONS = ("l3", "r3", "dpad", "lstick", "rstick")


# ---------------------------------------------------------------- symbols

def box_metrics(inner):
    return (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2, inner[2] - inner[0]


def sym_circle(inner):
    cx, cy, w = box_metrics(inner)
    def draw(d, color):
        r = w * 0.29
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=color, width=int(w * 0.085))
    return draw


def sym_cross(inner):
    cx, cy, w = box_metrics(inner)
    def draw(d, color):
        r = w * 0.27
        for a, b in (((cx - r, cy - r), (cx + r, cy + r)), ((cx - r, cy + r), (cx + r, cy - r))):
            d.line([a, b], fill=color, width=int(w * 0.095))
    return draw


def sym_square(inner):
    cx, cy, w = box_metrics(inner)
    def draw(d, color):
        r = w * 0.27
        d.rectangle((cx - r, cy - r, cx + r, cy + r), outline=color, width=int(w * 0.085))
    return draw


def sym_triangle(inner):
    cx, cy, w = box_metrics(inner)
    def draw(d, color):
        r = w * 0.32
        pts = [(cx, cy - r * 1.02), (cx + r * 1.04, cy + r * 0.78), (cx - r * 1.04, cy + r * 0.78)]
        d.line(pts + [pts[0], pts[1]], fill=color, width=int(w * 0.085), joint="curve")
    return draw


def sym_dpad(inner):
    cx, cy, w = box_metrics(inner)
    def draw(d, color):
        a, b = w * 0.31, w * 0.105
        d.rectangle((cx - a, cy - b, cx + a, cy + b), fill=color)
        d.rectangle((cx - b, cy - a, cx + b, cy + a), fill=color)
        d.ellipse((cx - b * 0.55, cy - b * 0.55, cx + b * 0.55, cy + b * 0.55), fill=(20, 24, 36, 255))
    return draw


def sym_stick(inner, letter, fnt):
    cx, cy, w = box_metrics(inner)
    def draw(d, color):
        r = w * 0.33
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=color, width=int(w * 0.05))
        r = w * 0.22
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
        l, t, rr, bb = d.textbbox((0, 0), letter, font=fnt)
        d.text((cx - (rr - l) / 2 - l, cy - (bb - t) / 2 - t), letter, font=fnt, fill=(14, 18, 30, 255))
    return draw


def sym_text(text, fnt, inner):
    return mb.text_symbol(text, fnt, inner)


def make_custom(kind, symbol, color, out, accent=None):
    img, inner = mb.glossy_base(kind, accent or color[:3])
    mb.glowing(img, symbol(inner), color)
    mb.sheen(img, kind, inner)
    mb.finish(img, out)


# ---------------------------------------------------------------- icon sets

def draw_icons(root: Path, ttf: str, force: bool) -> list[str]:
    big, mid, small = mb.font(ttf, int(S * .50)), mb.font(ttf, int(S * .30)), mb.font(ttf, int(S * .19))
    tiny = mb.font(ttf, int(S * .13))
    stick_font = mb.font(ttf, int(S * .20))
    made = []

    def target(style, name, wide=False):
        path = root / style / (name + ("_wide" if wide else "") + ".png")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not force:
            return None
        made.append(f"{style}/{path.name}")
        return path

    for style in STYLES:
        # ---- the buttons native/buttons already had (playstation is new)
        if style == "playstation":
            symbols = {"circle": sym_circle, "cross": sym_cross, "square": sym_square, "triangle": sym_triangle}
            for name, fn in symbols.items():
                if p := target(style, name):
                    make_custom("disc", fn, PS_COLORS[name], p)
            labels = {"l1": "L1", "r1": "R1", "l2": "L2", "r2": "R2", "start": "START", "select": "SELECT"}
            for name, label in labels.items():
                if p := target(style, name):
                    mb.make("pill", label, WHITE, mid if len(label) == 2 else tiny if len(label) > 5 else small, p,
                            accent=(150, 170, 200))
            for name in ("up", "down", "left", "right"):
                if p := target(style, name):
                    mb.make("disc", "", WHITE, big, p, accent=(140, 180, 255), arrow=name)
        # ---- the new buttons, in every style
        for name in NEW_BUTTONS:
            p = target(style, name)
            if not p:
                continue
            if style == "keyboard":
                if name == "dpad":
                    make_custom("key", sym_dpad, WHITE, p, accent=(140, 180, 255))
                else:
                    label = {"l3": "R", "r3": "F", "lstick": "WASD", "rstick": "MOUSE"}[name]
                    fnt = big if len(label) == 1 else small if len(label) <= 4 else tiny
                    make_custom("key", lambda inner, t=label, f=fnt: sym_text(t, f, inner), WHITE, p,
                                accent=(140, 180, 255))
                continue
            color = {"playstation": WHITE, "xbox": WHITE, "vr": (140, 205, 255, 255)}[style]
            if name == "dpad":
                make_custom("disc", sym_dpad, color, p, accent=(140, 180, 255))
            elif name in ("lstick", "rstick"):
                make_custom("disc", lambda inner, n=name: sym_stick(inner, "L" if n == "lstick" else "R", stick_font),
                            color, p, accent=(140, 180, 255))
            else:  # l3 / r3: the stick, pressed
                label = "L3" if name == "l3" else "R3"
                make_custom("disc", lambda inner, t=label: sym_text(t, mid, inner), color, p, accent=(150, 170, 200))
    return made


# ---------------------------------------------------------------- engine header

CELL = 24


def mono_font(ttf, px):
    try:
        return ImageFont.truetype(ttf, px)
    except OSError:
        return ImageFont.load_default(px)


def draw_mono(name: str, parts: int, ttf: str) -> Image.Image:
    """The PlayStation symbol as white-on-transparent (parts*24 x 24), at 8x."""
    k = 8
    W, H = CELL * parts * k, CELL * k
    img = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(img)
    cx, cy = W / 2, H / 2
    lw = int(1.9 * k)
    r = 8.0 * k

    def label(text, size, dy=0.0, x=None):
        f = mono_font(ttf, int(size * k))
        l, t, rr, bb = d.textbbox((0, 0), text, font=f)
        d.text(((cx if x is None else x) - (rr - l) / 2 - l, cy + dy * k - (bb - t) / 2 - t), text, font=f, fill=255)

    if name == "circle":
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=255, width=lw)
    elif name == "cross":
        d.line([(cx - r, cy - r), (cx + r, cy + r)], fill=255, width=lw)
        d.line([(cx - r, cy + r), (cx + r, cy - r)], fill=255, width=lw)
    elif name == "square":
        d.rectangle((cx - r * .95, cy - r * .95, cx + r * .95, cy + r * .95), outline=255, width=lw)
    elif name == "triangle":
        pts = [(cx, cy - r * 1.1), (cx + r * 1.1, cy + r * .85), (cx - r * 1.1, cy + r * .85)]
        d.line(pts + [pts[0], pts[1]], fill=255, width=lw, joint="curve")
    elif name in ("l3", "r3"):
        d.ellipse((cx - r * 1.2, cy - r * 1.2, cx + r * 1.2, cy + r * 1.2), outline=255, width=int(1.4 * k))
        label(name.upper(), 9.5)
    elif name == "dpad":
        a, b = 9.5 * k, 3.0 * k
        d.rectangle((cx - a, cy - b, cx + a, cy + b), fill=255)
        d.rectangle((cx - b, cy - a, cx + b, cy + a), fill=255)
    elif name in ("lstick", "rstick"):
        d.ellipse((cx - r * 1.2, cy - r * 1.2, cx + r * 1.2, cy + r * 1.2), outline=255, width=int(1.4 * k))
        d.ellipse((cx - r * .62, cy - r * .62, cx + r * .62, cy + r * .62), fill=255)
        label("L" if name == "lstick" else "R", 8.0)
        # the letter is knocked out of the cap
        f = mono_font(ttf, int(8.0 * k))
        l, t, rr, bb = d.textbbox((0, 0), "L" if name == "lstick" else "R", font=f)
        d.text((cx - (rr - l) / 2 - l, cy - (bb - t) / 2 - t), "L" if name == "lstick" else "R", font=f, fill=0)
    elif name in ("up", "down", "left", "right"):
        a = {"up": -90, "down": 90, "left": 180, "right": 0}[name]
        rad = r * 1.15
        pts = [(cx + math.cos(math.radians(a + t)) * rad, cy + math.sin(math.radians(a + t)) * rad) for t in (0, 140, 220)]
        d.polygon(pts, fill=255)
    else:                                    # pills: outline + the PlayStation label
        lw2 = int(1.6 * k)
        d.rounded_rectangle((1.5 * k, 3.5 * k, W - 1.5 * k, H - 3.5 * k), radius=int(8.5 * k), outline=255, width=lw2)
        text = {"start": "START", "select": "SEL"}.get(name, name.upper())
        label(text, 10.5 if len(text) == 2 else 7.0)
    return img.resize((CELL * parts, CELL), Image.LANCZOS)


def pack4(img: Image.Image) -> bytes:
    """24x24 L image -> PSMT4 bytes (low nibble = left pixel), 16 alpha levels."""
    px = [min(15, (v * 15 + 127) // 255) for v in img.tobytes()]
    return bytes(px[i] | (px[i + 1] << 4) for i in range(0, len(px), 2))


def write_header(path: Path, ttf: str) -> None:
    rows = []
    index = 0
    for name, parts in tb.BUTTONS:
        mono = draw_mono(name, parts, ttf)
        for part in range(parts):
            cell = mono.crop((part * CELL, 0, (part + 1) * CELL, CELL))
            data = pack4(cell)
            assert len(data) == 288 and any(data), name
            hexed = ", ".join(f"0x{b:02x}" for b in data)
            rows.append(f'    {{"{name}", {index}, {part}, {parts}, {{{hexed}}}}},')
            index += 1
    text = f"""// Generated by tools/modding/make_text_icons.py header (do not edit): the
// reserved characters for button placeholders in text (text_buttons.py).
// Character i is Shift-JIS 0xF040 + i. Its bitmap is a 24x24 PSMT4 glyph of
// the PlayStation symbol (the game draws it like any full-width glyph); the
// texture replacement identifies the bitmap and draws the input style's icon.
#pragma once

#include <cstdint>

namespace ga2_text_icons
{{
    inline constexpr uint32_t kFirstCode = 0xF040u;   // Shift-JIS lead 0xF0, trail 0x40 + index
    inline constexpr uint32_t kGlyphBytes = 288u;     // 24 x 24 pixels, 4 bits each
    struct Glyph
    {{
        const char *button;   // text_buttons.py name (also the icon file name)
        uint8_t index;        // character offset from kFirstCode
        uint8_t part;         // 0 = left or only cell
        uint8_t parts;        // 1 or 2 cells wide
        uint8_t bitmap[288];
    }};
    inline constexpr Glyph kGlyphs[] = {{
{chr(10).join(rows)}
    }};
    inline constexpr unsigned kGlyphCount = sizeof(kGlyphs) / sizeof(kGlyphs[0]);

    // True once the GPU renderer's texture replacement is running (gs_texpacks.cpp):
    // it draws the style's icons over these bitmaps, so the engine hook also
    // makes the glyphs draw untinted (white, so the icon keeps its colours in
    // dark dialogue text too). Without it the bitmaps show as symbols in the text colour.
    bool replacementActive();
    void setReplacementActive(bool active);
}}
"""
    path.write_text(text)
    print(f"{path}: {index} glyphs")


def preview(path: Path, ttf: str) -> None:
    """A contact sheet of the monochrome glyphs at 8x."""
    cells = tb.cells()
    sheet = Image.new("L", (CELL * 8 * 8, CELL * 8 * ((len(cells) + 7) // 8)), 40)
    index = 0
    for name, parts in tb.BUTTONS:
        mono = draw_mono(name, parts, ttf)
        for part in range(parts):
            cell = mono.crop((part * CELL, 0, (part + 1) * CELL, CELL))
            data = pack4(cell)
            img = Image.new("L", (CELL, CELL))
            img.putdata([v for b in data for v in ((b & 15) * 17, (b >> 4) * 17)])
            sheet.paste(img.resize((CELL * 8, CELL * 8), Image.NEAREST), ((index % 8) * CELL * 8, (index // 8) * CELL * 8))
            index += 1
    sheet.save(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("icons")
    a.add_argument("out")
    a.add_argument("--force", action="store_true")
    b = sub.add_parser("header")
    b.add_argument("out")
    c = sub.add_parser("preview")
    c.add_argument("out")
    for p in (a, b, c):
        p.add_argument("--font", default="/usr/share/fonts/TTF/Hack-Bold.ttf")
    args = ap.parse_args()
    if args.cmd == "icons":
        made = draw_icons(Path(args.out), args.font, args.force)
        print(f"{len(made)} icon(s) written to {args.out}: {' '.join(made)}")
    elif args.cmd == "header":
        write_header(Path(args.out), args.font)
    else:
        preview(Path(args.out), args.font)


if __name__ == "__main__":
    main()
