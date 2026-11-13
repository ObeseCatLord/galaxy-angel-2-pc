#!/usr/bin/env python3
"""Writes native/installer/installer-font.otf: the port's UI font (Noto Sans JP, SIL OFL) cut down to what the
installer shows: Latin-1, some punctuation and arrows, and every non-ASCII character of native/installer/main.cpp
(its Japanese texts). Run after changing those texts. Needs fontTools (pip install fonttools)."""
from pathlib import Path

from fontTools import subset

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / 'native/fonts/NotoSansJP-Medium.otf'
OUT = ROOT / 'native/installer/installer-font.otf'

codepoints = set(range(0x20, 0x7F)) | set(range(0xA0, 0x100))
codepoints |= {0x2013, 0x2014, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2026, 0x2190, 0x2191, 0x2192, 0x2193, 0x21B5, 0x2713}
codepoints |= {ord(c) for c in (ROOT / 'native/installer/main.cpp').read_text(encoding='utf-8') if ord(c) > 0x7F}

options = subset.Options()
options.layout_features = ['kern', 'palt']
options.name_IDs = ['*']
options.notdef_outline = True
font = subset.load_font(str(SOURCE), options)
subsetter = subset.Subsetter(options)
subsetter.populate(unicodes=sorted(codepoints))
subsetter.subset(font)
subset.save_font(font, str(OUT), options)
print(f'{OUT.relative_to(ROOT)}: {len(codepoints)} characters, {OUT.stat().st_size} bytes')
