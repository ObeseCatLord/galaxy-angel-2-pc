#!/usr/bin/env python3
"""Button placeholders in translated text: {circle}, {cross}, {l1}, ...

A translator writes `{circle}` (or any name below) where the game's text
shows a controller button.  The build turns each placeholder into one or
two reserved characters (private-use U+E000.., Shift-JIS 0xF040..); the
engine (native/compat/ga2_text.cpp, gs_texpacks.cpp) draws them as the
icon of the input device the player uses, at the text's size, and switches
live when the device changes.  See docs/modding/translation.md.

The table here is the single source for the build tools and for the engine's
glyph header (tools/modding/make_text_icons.py writes ga2_text_icons.h and
the missing icon art from it).

  text_buttons.py list                       the placeholders and their codes
  text_buttons.py expand "Press {circle}."   what a cell becomes (Shift-JIS hex)
"""

from __future__ import annotations

import re
import sys

# (name, cells).  A cell is one full-width character: 1 em wide at the text
# size.  Round and square buttons take 1 cell; pill-shaped ones (shoulder
# buttons, Start, Select) take 2, drawn as the left and right half of one
# icon.  The order fixes the reserved codes: never reorder, only append
# (saved translations and the engine's table depend on it).
BUTTONS = [
    ("circle", 1), ("cross", 1), ("square", 1), ("triangle", 1),
    ("l3", 1), ("r3", 1), ("dpad", 1), ("lstick", 1), ("rstick", 1),
    ("up", 1), ("down", 1), ("left", 1), ("right", 1),
    ("l1", 2), ("r1", 2), ("l2", 2), ("r2", 2), ("start", 2), ("select", 2),
]
FIRST = 0xE000          # U+E000 = Shift-JIS 0xF040 (the user-defined range)

# What each placeholder is (for the documentation and `list`).
MEANING = {
    "circle": "Circle button (confirm)", "cross": "Cross button (cancel)",
    "square": "Square button", "triangle": "Triangle button",
    "l3": "left stick click", "r3": "right stick click",
    "dpad": "the directional pad", "lstick": "the left stick", "rstick": "the right stick",
    "up": "D-pad up", "down": "D-pad down", "left": "D-pad left", "right": "D-pad right",
    "l1": "L1 shoulder button", "r1": "R1 shoulder button",
    "l2": "L2 trigger", "r2": "R2 trigger", "start": "Start button", "select": "Select button",
}

PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_]+)\}")


def cells() -> list[tuple[str, int, int]]:
    """[(name, part, parts)] in code order; the index is the code offset."""
    out = []
    for name, n in BUTTONS:
        out += [(name, part, n) for part in range(n)]
    return out


def code_of(name: str) -> str:
    """The reserved characters of a button (1 or 2)."""
    index = 0
    for n, count in BUTTONS:
        if n == name:
            return "".join(chr(FIRST + index + i) for i in range(count))
        index += count
    raise KeyError(name)


CODES = {name: code_of(name) for name, _ in BUTTONS}


def expand(text: str) -> str:
    """Replace every {name} with the reserved characters.  Raises ValueError
    for a name that is not a button (a typo should not reach the game)."""
    def one(m):
        name = m.group(1).lower()
        if name not in CODES:
            raise ValueError(f"unknown button placeholder {m.group(0)} "
                             f"(known: {', '.join('{' + n + '}' for n, _ in BUTTONS)})")
        return CODES[name]
    return PLACEHOLDER.sub(one, text)


# Button references the games' Japanese text makes ("○ボタン", "Ｌ２ボタン",
# "左スティック", ...) and how the same button reads in a translation that
# kept the PlayStation name instead of writing a placeholder.  `auto` turns
# those into placeholders, but only for the buttons the row's Japanese names
# as a button: "○×△□~..." mumbling or "the ATM's buttons" stay as written.
_FW = str.maketrans("ＬＲ１２３", "LR123")
_B = r"\s*ボタン"            # "... button"
_LEGEND = r"\s*[：:]"          # a key legend: "×：再生終了" (×: Stop)
_JP_BUTTONS = [  # (name, Japanese pattern (full-width digits folded), translation pattern)
    ("circle", r"[○◯](" + _B + "|" + _LEGEND + ")", r"[○◯]"),
    ("cross", r"[×✕╳](" + _B + "|" + _LEGEND + ")", r"[×✕╳]"),
    ("triangle", r"△(" + _B + "|" + _LEGEND + ")", r"△"),
    ("square", r"□(" + _B + "|" + _LEGEND + ")", r"□"),
    ("l1", r"L1" + _B, r"\bL1\b"), ("r1", r"R1" + _B, r"\bR1\b"),
    ("l2", r"L2" + _B, r"\bL2\b"), ("r2", r"R2" + _B, r"\bR2\b"),
    ("l3", r"L3" + _B, r"\bL3\b"), ("r3", r"R3" + _B, r"\bR3\b"),
    ("start", r"(START|スタート)" + _B, r"\b(START|Start)\b(?=\s+button)"),
    ("select", r"(SELECT|セレクト)" + _B, r"\b(SELECT|Select)\b(?=\s+button)"),
    # The games break lines anywhere, even inside a word (左ス / ティック).
    ("lstick", r"左\s*ス\s*テ\s*ィ\s*ッ\s*ク", r"\b[Ll]eft (analog )?stick\b"),
    ("rstick", r"右\s*ス\s*テ\s*ィ\s*ッ\s*ク", r"\b[Rr]ight (analog )?stick\b"),
    ("dpad", r"(方\s*向|十\s*字)\s*(キ\s*ー|ボ\s*タ\s*ン)", r"\b(directional (buttons|keys|pad)|D-?[Pp]ad)\b"),
]


def auto(text: str, japanese: str) -> str:
    """Placeholders for the PlayStation button names a translation kept
    (see _JP_BUTTONS).  Text that already uses placeholders is unchanged."""
    if not text or not japanese:
        return text
    jp = japanese.translate(_FW)
    for name, jp_pattern, text_pattern in _JP_BUTTONS:
        if re.search(jp_pattern, jp):
            text = re.sub(text_pattern, "{" + name + "}", text)
    return text


def contract(text: str) -> str:
    """The reverse of expand (for readable dumps)."""
    back = {c: n for n, c in CODES.items()}
    out, i = [], 0
    while i < len(text):
        for c, n in back.items():
            if text.startswith(c, i):
                out.append("{" + n + "}")
                i += len(c)
                break
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "list":
        for name, n in BUTTONS:
            codes = " ".join(ch.encode("cp932").hex() for ch in CODES[name])
            print(f"{'{' + name + '}':<10} {n} cell{'s' if n > 1 else ' '}  Shift-JIS {codes:<10}  {MEANING[name]}")
        return 0
    if len(argv) >= 3 and argv[1] == "expand":
        print(expand(argv[2]).encode("cp932").hex(" "))
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
