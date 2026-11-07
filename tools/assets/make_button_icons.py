#!/usr/bin/env python3
"""Draws the port's own button-prompt icons (no game art), in the look of
the games' prompts: glossy dark buttons with a bevelled rim, a coloured
symbol with a soft glow and a highlight across the top. One 128x128 PNG per
PS2 button for each prompt style (native/buttons/<style>/<button>.png, see
native/buttons/README.md).

Keyboard also gets <button>_wide.png for pill-shaped spots (L1/R1/L2/R2/Start/Select).

Styles follow the port's input mappings (ps2_pad.cpp, ga2_vr.h):
  xbox      Circle=B Cross=A Square=X Triangle=Y, LB/RB/LT/RT, Menu/View, D-pad
  keyboard  Circle=Enter Cross=X Square=Z Triangle=V, Q/E, L-Shift/R-Shift, P/Tab, arrows
  vr        Circle=A Cross=B Square=X Triangle=Y, grips, menu, stick click, stick

usage: make_button_icons.py <out-dir> [--font <ttf>]
"""
import argparse
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

BUTTONS = ["circle", "cross", "square", "triangle", "l1", "r1", "l2", "r2", "start", "select",
           "up", "down", "left", "right"]
SIZE = 128
SS = 4              # supersampling
S = SIZE * SS


def font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default(size)


def vertical_gradient(size, top, bottom):
    w, h = size
    g = Image.new("RGBA", size)
    d = ImageDraw.Draw(g)
    for y in range(h):
        t = y / max(1, h - 1)
        d.line([(0, y), (w, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(4)))
    return g


def shape_mask(kind, box):
    m = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(m)
    if kind == "disc":
        d.ellipse(box, fill=255)
    elif kind == "pill":
        d.rounded_rectangle(box, radius=(box[3] - box[1]) // 2, fill=255)
    elif kind == "keywide":
        d.rounded_rectangle(box, radius=int(S * 0.09), fill=255)
    else:  # key
        d.rounded_rectangle(box, radius=int(S * 0.16), fill=255)
    return m


def glossy_base(kind, accent=(90, 170, 255)):
    """Bevelled dark button with a top highlight, like the games' prompts."""
    if kind == "disc":
        outer, inner = (int(S * .04), int(S * .04), int(S * .96), int(S * .96)), (int(S * .11), int(S * .11), int(S * .89), int(S * .89))
    elif kind == "pill":
        outer, inner = (int(S * .02), int(S * .20), int(S * .98), int(S * .80)), (int(S * .07), int(S * .26), int(S * .93), int(S * .74))
    elif kind == "keywide":  # wide keycap for pill-shaped prompt spots (about 1.6:1)
        outer, inner = (int(S * .02), int(S * .20), int(S * .98), int(S * .80)), (int(S * .07), int(S * .26), int(S * .93), int(S * .74))
    else:
        outer, inner = (int(S * .05), int(S * .05), int(S * .95), int(S * .95)), (int(S * .12), int(S * .11), int(S * .88), int(S * .86))
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # Soft accent glow behind the rim.
    glow = Image.new("RGBA", (S, S), accent + (0,))
    glow.putalpha(shape_mask(kind, outer).filter(ImageFilter.GaussianBlur(S * 0.03)).point(lambda v: v * 0.55))
    img.alpha_composite(glow)
    # Rim: light at the top, dark at the bottom (bevel).
    rim = vertical_gradient((S, S), (200, 210, 225, 255), (40, 44, 58, 255))
    rim.putalpha(shape_mask(kind, outer))
    img.alpha_composite(rim)
    # Face: deep navy/black, a touch lighter at the top.
    face = vertical_gradient((S, S), (46, 52, 72, 255), (6, 7, 12, 255))
    face.putalpha(shape_mask(kind, inner))
    img.alpha_composite(face)
    return img, inner


def sheen(img, kind, inner):
    """White highlight over the upper half of the face."""
    w = inner[2] - inner[0]
    hl = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(hl)
    d.ellipse((inner[0] + w * 0.08, inner[1] - (inner[3] - inner[1]) * 0.35, inner[2] - w * 0.08,
               inner[1] + (inner[3] - inner[1]) * 0.48), fill=255)
    hl = hl.filter(ImageFilter.GaussianBlur(S * 0.02))
    grad = vertical_gradient((S, S), (255, 255, 255, 120), (255, 255, 255, 0)).getchannel("A")
    alpha = ImageChops.multiply(ImageChops.multiply(hl, grad), shape_mask(kind, inner))
    layer = Image.new("RGBA", (S, S), (255, 255, 255, 0))
    layer.putalpha(alpha)
    img.alpha_composite(layer)


def glowing(img, draw_symbol, color):
    """Draws a symbol with a soft glow in its colour."""
    sym = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    draw_symbol(ImageDraw.Draw(sym), color)
    glow = sym.filter(ImageFilter.GaussianBlur(S * 0.025))
    img.alpha_composite(glow)
    img.alpha_composite(glow)
    img.alpha_composite(sym)


def text_symbol(text, fnt, inner):
    def draw(d, color):
        l, t, r, b = d.textbbox((0, 0), text, font=fnt)
        x = (inner[0] + inner[2] - (r - l)) / 2 - l
        y = (inner[1] + inner[3] - (b - t)) / 2 - t
        d.text((x, y), text, font=fnt, fill=color)
    return draw


def arrow_symbol(direction, inner):
    def draw(d, color):
        cx, cy = (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2
        r = (inner[2] - inner[0]) * 0.30
        angle = {"up": -90, "down": 90, "left": 180, "right": 0}[direction]
        pts = [(cx + math.cos(math.radians(angle + a)) * r, cy + math.sin(math.radians(angle + a)) * r)
               for a in (0, 140, 220)]
        d.polygon(pts, fill=color)
    return draw


def finish(img, out):
    img.resize((SIZE, SIZE), Image.LANCZOS).save(out)


def make(kind, label, color, fnt, out, accent=None, arrow=None):
    img, inner = glossy_base(kind, accent or color[:3])
    draw = arrow_symbol(arrow, inner) if arrow else text_symbol(label, fnt, inner)
    glowing(img, draw, color)
    sheen(img, kind, inner)
    finish(img, out)


def draw_style(style, out, ttf):
    big, mid, small = font(ttf, int(S * .50)), font(ttf, int(S * .30)), font(ttf, int(S * .19))
    white = (240, 244, 255, 255)
    for name in BUTTONS:
        path = out / f"{name}.png"
        if name in ("up", "down", "left", "right"):
            make("key" if style == "keyboard" else "disc", "", white, big, path, accent=(140, 180, 255), arrow=name)
            continue
        if style == "xbox":
            face = {"circle": ("B", (255, 82, 82, 255)), "cross": ("A", (110, 220, 90, 255)),
                    "square": ("X", (80, 150, 255, 255)), "triangle": ("Y", (255, 214, 60, 255))}
            if name in face:
                make("disc", face[name][0], face[name][1], big, path)
            else:
                label = {"l1": "LB", "r1": "RB", "l2": "LT", "r2": "RT", "start": "MENU", "select": "VIEW"}[name]
                make("pill", label, white, mid if len(label) == 2 else small, path, accent=(150, 170, 200))
        elif style == "vr":
            blue = (140, 205, 255, 255)
            face = {"circle": "A", "cross": "B", "square": "X", "triangle": "Y"}
            if name in face:
                make("disc", face[name], blue, big, path)
            else:
                label = {"l1": "L GRIP", "r1": "R GRIP", "l2": "L2", "r2": "R2", "start": "MENU", "select": "STICK"}[name]
                make("pill", label, blue, mid if len(label) == 2 else small, path)
        else:  # keyboard
            keys = {"circle": "ENTER", "cross": "X", "square": "Z", "triangle": "V", "l1": "Q", "r1": "E",
                    "l2": "L⇧", "r2": "R⇧", "start": "P", "select": "TAB"}
            label = keys[name]
            make("key", label, white, big if len(label) == 1 else (mid if len(label) == 2 else small), path,
                 accent=(140, 180, 255))
            # Wide variants for pill-shaped spots (the engine uses <button>_wide.png for spots at least 1.5:1).
            if name in ("l1", "r1", "l2", "r2", "start", "select"):
                wide = {"l1": "Q", "r1": "E", "l2": "L-SHIFT", "r2": "R-SHIFT", "start": "P", "select": "TAB"}[name]
                wfont = font(ttf, int(S * (.36 if len(wide) == 1 else .26 if len(wide) <= 3 else .19 if len(wide) <= 5 else .155)))
                make("keywide", wide, white, wfont, out / f"{name}_wide.png", accent=(140, 180, 255))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("out")
    parser.add_argument("--font", default="/usr/share/fonts/TTF/Hack-Bold.ttf")
    args = parser.parse_args()
    for style in ("xbox", "keyboard", "vr"):
        out = Path(args.out) / style
        out.mkdir(parents=True, exist_ok=True)
        draw_style(style, out, args.font)
    print(f"icons written to {args.out}")


if __name__ == "__main__":
    main()
