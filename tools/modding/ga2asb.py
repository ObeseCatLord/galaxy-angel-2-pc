#!/usr/bin/env python3
"""Galaxy Angel II ADV scripts (adv/scn/*.asb): parser, disassembler to an
editable UTF-8 text form (".gasm") and assembler back to .asb.

The scripts are compiled for the "ASN" stack VM in the game executable
(asn/asn_vm.c; opcode names and sizes come from its table at 0x2e8ee0 in
SLPM_662.43).  Format (little endian):

  0x00 u32 0
  0x04 char[32] script file name, NUL padded
  0x24 u32 functions_offset (0x44), u32 function_count
  0x2c u32 code_offset, u32 code_size
  0x34 u32 strings_offset, u32 strings_size   (code is 4-byte padded)
  0x3c u32 externs_offset, u32 externs_size
  function entry (20 bytes): u32 name (string offset), u32 flags
      (bit 0 = local; exported "@..." entry points have 0), u16 locals,
      u16 params, u32 code offset, u32 code size
  strings: NUL-terminated Shift-JIS (cp932); externs: NUL-terminated
      script file names used by CALLG/JMPG.

Branch targets are offsets from the start of the code section; CALLL/JMPL
take a function index; CALLG/JMPG take (extern offset, string offset of the
function name).  String arguments are plain PUSHI immediates holding a
string-table offset: the text of builtin 0 (message, arg 0) and every
argument of builtins 1 and 2 (choice menus).  The disassembler finds those
by stack simulation (dead code included) and writes them as string labels,
so edited text can change length.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

OPS = [
    ("NOP", 1), ("PUSHI", 5), ("PUSHL", 2), ("PUSHG", 5), ("PUSHF", 5),
    ("PUSHR", 1), ("POP", 1), ("POPL", 2), ("POPG", 5), ("POPF", 5),
    ("STIL", 6), ("STIG", 9), ("STIF", 9), ("INCL", 2), ("DECL", 2),
    ("INCG", 5), ("DECG", 5), ("EXCH", 1), ("NEG", 1), ("ADD", 1),
    ("SUB", 1), ("MUL", 1), ("DIV", 1), ("MOD", 1), ("AND", 1), ("OR", 1),
    ("NOT", 1), ("EQ", 1), ("NEQ", 1), ("LT", 1), ("GT", 1), ("LEQ", 1),
    ("GEQ", 1), ("B", 5), ("BZ", 5), ("BNZ", 5), ("BTBL", -1), ("JMPL", 5),
    ("JMPG", 9), ("CALLL", 6), ("CALLG", 10), ("CALLB", 6), ("RET", 1),
    ("RETN", 1), ("YIELD", 1),
]
OPCODE = {name: i for i, (name, _) in enumerate(OPS)}

# Builtins whose arguments are string-table offsets: id -> argument
# positions (None = all arguments).
STRING_ARGS = {0x00: (0,), 0x01: None, 0x02: None}

# Engine builtins (CALLB id).  Names and argument lists were worked out from
# the developer test script adv/scn/test.asb (Zettai), whose messages say
# what each call does, and from story scripts; ids are the same on all five
# discs for these.  The disassembler writes the name, the assembler accepts
# a name or a number.  "?" marks a guess.  Arguments are pushed left to
# right; [..] are optional (the VM passes the count).
BUILTINS = {
    0x00: ("msg", "text[, voice] - show a message and wait for the button"),
    0x01: ("menu", "option... - system menu choice; PUSHR = index"),
    0x02: ("choice", "option... - story choice (shown in random order); PUSHR = index"),
    0x10: ("bg", "bg_id, effect[, ...] - change background (bg.tbl id, -1 = none; effect 0 cut, 1 crossfade, 2/3 wipe right/left, 4 fade)"),
    0x11: ("bgm", "track[, fade_ms, ?, volume] - play BGM"),
    0x12: ("bgm_stop", "[fade_ms] - stop BGM"),
    0x13: ("say", "char, face[, voice] - speaker for the next msg (speech balloon; char 0x200000 = narrator)"),
    0x14: ("think", "char[, face, voice] - speaker, monologue balloon"),
    0x15: ("narration_box", "on - large narration balloon (up to 20 lines) on/off"),
    0x16: ("msg_hide", " - close the message balloon"),
    0x19: ("chars", "left_char, left_face, center_char, center_face, right_char, right_face[, effect] - set all three standing characters (-1 = empty)"),
    0x1a: ("char_center", "char, face, effect[, priority] - show a character in the centre (effect 0 cut, 1 fade, 2 move, 3 fade+slide, dir<<16|2 slide from a side)"),
    0x1b: ("char_left", "char, face, effect[, priority] - show a character on the left"),
    0x1c: ("char_right", "char, face, effect[, priority] - show a character on the right"),
    0x1d: ("char_hide", "char, effect - remove one character"),
    0x1e: ("chars_clear", "[effect] - remove all characters (0 cut, 1 fade, 2 slide)"),
    0x1f: ("wait_chars", " - wait until character animations finish"),
    0x20: ("wait", "ms[, mode] - wait"),
    0x21: ("shake", "x, y, speed, duration_ms[, fade_ms] - screen shake (-1 = until stopped)"),
    0x22: ("shake_stop", "fade_ms - end the shake"),
    0x23: ("wait_shake", " - wait until the shake ends"),
    0x24: ("flash", "color, ?, count - screen flash (color 0xAABBGGRR)"),
    0x25: ("color_in", "color, ms[, all] - fade in from a colour"),
    0x26: ("color_out", "color, ms[, all] - fade out to a colour (all = whole screen incl. UI)"),
    0x27: ("game_init", " - reset game state (new game)"),
    0x28: ("ship_move", "chapter, part - go to the ship-movement map"),
    0x2a: ("battle", "chapter, stage - start a battle (SLG)"),
    0x2b: ("chapter_title", "chapter - show the chapter title"),
    0x2c: ("eyecatch", "heroine, ? - eyecatch"),
    0x30: ("ranking", "place - heroine ranking"),
    0x31: ("char_effect", "char_slot, effect, ? - emote effect on a character (adveffect.tbl)"),
    0x32: ("effect", "slot, effect, x, y, z - effect at a screen position"),
    0x33: ("effect_stop", "slot[, fade_ms] - stop an effect"),
    0x34: ("effects_clear", " - remove all effects"),
    0x36: ("bg_trans", "bg_id, type, ms[, curve, angle] - background transition with time, easing and wipe angle"),
    0x37: ("game_end", " - end the game (back to the title)"),
    0x38: ("se", "id[, volume, pan] - play a sound effect"),
    0x39: ("se_loop", "slot, id[, volume, pan, fade_ms] - looping sound effect in a slot"),
    0x3a: ("se_stop", "slot[, fade_ms] - stop a sound slot"),
    0x3c: ("mat_play", "slot, mat_id, ... - play a MAT animation (advmat.tbl)"),
    0x3d: ("mat_stop", "slot[, ...] - stop a MAT animation"),
    0x3e: ("mat_clear", "slot - remove a MAT animation"),
    0x40: ("mat_wait", "slot - wait for a MAT animation"),
    0x4b: ("text_slot", "slot, x, y, align, ?, voice, ?, ? - free text position for the next msg (prologue narration: 0, 320, 65, 2 = centred)?"),
    0x53: ("bgm_volume", "volume - change BGM volume"),
    0x55: ("char_priority", "char, priority - drawing order of a character"),
    0x56: ("say_voice", "char, face, ?, voice - speaker with voice (variant)?"),
    0x61: ("se_volume", "slot, volume, ms - change a sound slot's volume"),
    0x62: ("eyes", "char, closed - close (1) or open (0) a character's eyes"),
}
BUILTIN_IDS = {name: bid for bid, (name, _) in BUILTINS.items()}
BUILTIN_NOTES = {bid: f"{name}({doc.split(' - ')[0].strip()})" for bid, (name, doc) in BUILTINS.items()}

BRANCHES = {"B", "BZ", "BNZ"}


class AsbError(ValueError):
    pass


@dataclass
class Function:
    name: int
    flags: int
    locals: int
    params: int
    off: int
    size: int
    ins: list = field(default_factory=list)   # (code_off, opname, raw_args)


@dataclass
class Script:
    name: bytes
    funcs: list
    code: bytes
    strings: list          # [(offset, bytes)]
    externs: list          # [(offset, bytes)]
    string_refs: dict = field(default_factory=dict)  # PUSHI-string code offset -> builtin

    @property
    def string_at(self) -> dict:
        return {o: s for o, s in self.strings}


def _u32(d: bytes, o: int) -> int:
    return struct.unpack_from("<I", d, o)[0]


def _split_strings(blob: bytes) -> list:
    out, o = [], 0
    while o < len(blob):
        e = blob.index(b"\0", o)
        out.append((o, blob[o:e]))
        o = e + 1
    return out


def parse(data: bytes) -> Script:
    if len(data) < 0x44 or _u32(data, 0) != 0:
        raise AsbError("not an asb script")
    fo, fn, co, cs, so, ss, eo, es = (_u32(data, 0x24 + 4 * i) for i in range(8))
    if fo != 0x44 or co != fo + 20 * fn or eo != so + ss or eo + es != len(data):
        raise AsbError("unexpected asb layout")
    if so != (co + cs + 3) & ~3 or any(data[co + cs:so]):
        raise AsbError("unexpected code padding")
    name = data[4:0x24]
    code = data[co:co + cs]
    if ss and data[so + ss - 1] != 0 or es and data[eo + es - 1] != 0:
        raise AsbError("unterminated string table")
    sc = Script(name, [], code, _split_strings(data[so:so + ss]),
                _split_strings(data[eo:eo + es]))
    for i in range(fn):
        nm, flags, nloc, npar, off, size = struct.unpack_from("<IIHHII", data, fo + 20 * i)
        f = Function(nm, flags, nloc, npar, off, size)
        pc, end = off, off + size
        while pc < end:
            op = code[pc]
            if op >= len(OPS):
                raise AsbError(f"bad opcode {op:#x} at code {pc:#x}")
            opname, ln = OPS[op]
            if ln < 0:
                ln = 2 + 4 * code[pc + 1]
            f.ins.append((pc, opname, code[pc + 1:pc + ln]))
            pc += ln
        if pc != end:
            raise AsbError("function size does not end on an instruction")
        sc.funcs.append(f)
    sc.string_refs = find_string_refs(sc)
    return sc


def find_string_refs(sc: Script) -> dict:
    """Code offsets of PUSHI instructions whose value is a string offset
    consumed as a string argument (STRING_ARGS).  Abstract stack
    simulation along control flow, then over unreachable code.  Returns
    {code offset: builtin id that consumes the string}."""
    refs: dict = {}
    starts = {o for o, _ in sc.strings}
    for f in sc.funcs:
        ins = f.ins
        index = {pc: i for i, (pc, _, _) in enumerate(ins)}
        seen: set = set()
        work: list = [(0, ())]
        while True:
            if not work:
                rest = next((k for k in range(len(ins)) if k not in seen), None)
                if rest is None:
                    break
                work = [(rest, ())]
            i, st = work.pop()
            while i < len(ins) and i not in seen:
                seen.add(i)
                pc, op, a = ins[i]
                st = list(st)
                nxt = [i + 1]

                def pop(n=1):
                    vals = []
                    for _ in range(n):
                        vals.append(st.pop() if st else None)
                    return vals[::-1]

                if op == "PUSHI":
                    st.append((pc, struct.unpack("<i", a)[0]))
                elif op in ("PUSHL", "PUSHG", "PUSHF", "PUSHR"):
                    st.append(None)
                elif op in ("POP", "POPL", "POPG", "POPF"):
                    pop()
                elif op == "EXCH":
                    x, y = pop(2)
                    st += [y, x]
                elif op in ("NEG", "NOT"):
                    pop()
                    st.append(None)
                elif op in ("ADD", "SUB", "MUL", "DIV", "MOD", "AND", "OR",
                            "EQ", "NEQ", "LT", "GT", "LEQ", "GEQ"):
                    pop(2)
                    st.append(None)
                elif op in BRANCHES:
                    if op != "B":
                        pop()
                    t = index.get(_u32(a, 0))
                    nxt = ([] if op == "B" else [i + 1]) + ([t] if t is not None else [])
                elif op == "BTBL":
                    pop()
                    nxt = [index[t] for t in struct.unpack_from(f"<{a[0]}I", a, 1) if t in index]
                elif op in ("JMPL", "JMPG", "RETN"):
                    nxt = []
                elif op == "RET":
                    pop()
                    nxt = []
                elif op in ("CALLB", "CALLL", "CALLG"):
                    n = a[-1]
                    args = pop(n)
                    if op == "CALLB":
                        bid = _u32(a, 0)
                        if bid in STRING_ARGS:
                            pos = STRING_ARGS[bid]
                            for k, v in enumerate(args):
                                if (pos is None or k in pos) and v is not None and v[1] in starts:
                                    refs[v[0]] = bid
                st = tuple(st)
                if not nxt:
                    break
                for j in nxt[1:]:
                    work.append((j, st))
                i = nxt[0]
                st = tuple(st) if isinstance(st, list) else st
    return refs


def message_strings(sc: Script) -> set:
    """String offsets shown as messages (builtin 0x00, argument 0)."""
    out = set()
    for f in sc.funcs:
        for pc, op, a in f.ins:
            if sc.string_refs.get(pc) == 0x00:
                out.add(struct.unpack("<i", a)[0])
    return out


# ---------------------------------------------------------------- text form

_ESC_OUT = {0x0A: "\\n", 0x09: "\\t", 0x22: '\\"'}
CONTROL_LETTERS = "scv"   # game control codes \s.. \c.. \v.. kept verbatim


def sjis_to_text(b: bytes) -> str:
    """cp932 bytes -> quoted-string body.  Bytes that do not survive a
    cp932 round trip are written as \\xHH."""
    out = []
    i = 0
    while i < len(b):
        c = b[i]
        if c in _ESC_OUT:
            out.append(_ESC_OUT[c])
            i += 1
            continue
        if c == 0x5C:  # backslash
            nxt = b[i + 1:i + 2]
            if nxt and nxt.decode("ascii", "replace") in CONTROL_LETTERS:
                out.append("\\")
            else:
                out.append("\\\\")
            i += 1
            continue
        if c < 0x20 or c == 0x7F:
            out.append(f"\\x{c:02x}")
            i += 1
            continue
        n = 2 if (0x81 <= c <= 0x9F or 0xE0 <= c <= 0xFC) and i + 1 < len(b) else 1
        chunk = b[i:i + n]
        try:
            ch = chunk.decode("cp932")
            if ch.encode("cp932") != chunk or ch == "\\":
                raise UnicodeError
            out.append(ch)
        except UnicodeError:
            out.append("".join(f"\\x{x:02x}" for x in chunk))
        i += n
    return "".join(out)


def text_to_sjis(s: str) -> bytes:
    out = bytearray()
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\":
            nx = s[i + 1:i + 2]
            if nx == "n":
                out.append(0x0A)
            elif nx == "t":
                out.append(0x09)
            elif nx == '"':
                out.append(0x22)
            elif nx == "\\":
                out.append(0x5C)
            elif nx == "x":
                out.append(int(s[i + 2:i + 4], 16))
                i += 4
                continue
            elif nx and nx in CONTROL_LETTERS:
                out.append(0x5C)
                i += 1
                continue
            else:
                raise AsbError(f"bad escape \\{nx} in {s!r}")
            i += 2
            continue
        try:
            out += ch.encode("cp932")
        except UnicodeEncodeError:
            raise AsbError(f"character {ch!r} (U+{ord(ch):04X}) has no Shift-JIS code")
        i += 1
    return bytes(out)


def quote(b: bytes) -> str:
    return '"' + sjis_to_text(b) + '"'


_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[^\s,]+')


def tokens(line: str) -> list:
    """Split an instruction line; ';' outside quotes starts a comment."""
    out = []
    pos = 0
    line = line.strip()
    while pos < len(line):
        if line[pos] in " \t,":
            pos += 1
            continue
        if line[pos] == ";":
            break
        m = _TOKEN.match(line, pos)
        out.append(m.group())
        pos = m.end()
    return out


def unquote(tok: str) -> bytes:
    if len(tok) < 2 or tok[0] != '"' or tok[-1] != '"':
        raise AsbError(f"expected a quoted string, got {tok}")
    return text_to_sjis(tok[1:-1])


def disassemble(sc: Script, file_label: str = "", speakers: dict | None = None) -> str:
    strings = sc.string_at
    sid = {o: i for i, (o, _) in enumerate(sc.strings)}
    xid = {o: i for i, (o, _) in enumerate(sc.externs)}
    targets = set()
    for f in sc.funcs:
        for pc, op, a in f.ins:
            if op in BRANCHES:
                targets.add(_u32(a, 0))
            elif op == "BTBL":
                targets.update(struct.unpack_from(f"<{a[0]}I", a, 1))
    notes = _string_notes(sc, speakers or {})

    L = []
    L.append(f"; Galaxy Angel II ADV script {file_label}".rstrip())
    L.append("; Text is in the .string lines (\\n = line break, \\s.. \\c.. \\v.. = game")
    L.append("; control codes).  Comments start with ';'.  See docs/modding/scripts.md.")
    nm = sc.name
    end = nm.index(b"\0") if b"\0" in nm else len(nm)
    if any(nm[end:]):
        L.append(".name_raw " + nm.hex())
    else:
        L.append(".name " + quote(nm[:end]))
    L.append("")
    for o, s in sc.externs:
        L.append(f".extern x{xid[o]} {quote(s)}")
    if sc.externs:
        L.append("")
    for o, s in sc.strings:
        note = notes.get(o)
        L.append(f".string s{sid[o]} {quote(s)}" + (f"   ; {note}" if note else ""))
    L.append("")

    def lab(t: int) -> str:
        return f"L{t:04x}"

    for fi, f in enumerate(sc.funcs):
        L.append(f".func F{fi} s{sid[f.name]} flags={f.flags} locals={f.locals} params={f.params}"
                 f"   ; {sjis_to_text(strings[f.name])}")
        for pc, op, a in f.ins:
            if pc in targets:
                L.append(f"{lab(pc)}:")
            arg = ""
            com = ""
            if op == "PUSHI":
                v = struct.unpack("<i", a)[0]
                if pc in sc.string_refs:
                    arg = f"s{sid[v]}"
                    com = sjis_to_text(strings[v])[:40]
                else:
                    arg = str(v)
            elif op in ("PUSHL", "POPL", "INCL", "DECL"):
                arg = str(a[0])
            elif op in ("PUSHG", "PUSHF", "POPG", "POPF", "INCG", "DECG"):
                arg = str(_u32(a, 0))
            elif op == "STIL":
                arg = f"{a[0]} {struct.unpack_from('<i', a, 1)[0]}"
            elif op in ("STIG", "STIF"):
                arg = f"{_u32(a, 0)} {struct.unpack_from('<i', a, 4)[0]}"
            elif op in BRANCHES:
                arg = lab(_u32(a, 0))
            elif op == "BTBL":
                arg = ", ".join(lab(t) for t in struct.unpack_from(f"<{a[0]}I", a, 1))
            elif op == "JMPL":
                arg = f"F{_u32(a, 0)}"
            elif op == "CALLL":
                arg = f"F{_u32(a, 0)} {a[4]}"
            elif op in ("JMPG", "CALLG"):
                xo, so_ = struct.unpack_from("<II", a)
                arg = f"x{xid[xo]} s{sid[so_]}" + (f" {a[8]}" if op == "CALLG" else "")
                com = f"{sjis_to_text(sc.externs[xid[xo]][1])} {sjis_to_text(strings[so_])}"
            elif op == "CALLB":
                bid = _u32(a, 0)
                name = BUILTINS.get(bid, (None,))[0]
                arg = f"{name or hex(bid)} {a[4]}"
                com = BUILTINS[bid][1].split(" - ")[-1] if bid in BUILTINS else ""
            line = f"    {op:<6} {arg}".rstrip()
            if com:
                line = f"{line:<28} ; {com}"
            L.append(line)
        L.append(".end")
        L.append("")
    return "\n".join(L)


def _string_notes(sc: Script, speakers: dict) -> dict:
    """Comment per string: how it is used and, for messages, the speaker
    set by the last builtin 0x13/0x14 before it."""
    strings = sc.string_at
    use: dict = {}
    for f in sc.funcs:
        use.setdefault(f.name, "function name")
        speaker = None
        pending = []
        for pc, op, a in f.ins:
            if op == "PUSHI":
                pending.append(struct.unpack("<i", a)[0])
            elif op == "CALLB":
                bid, n = _u32(a, 0), a[4]
                args = pending[-n:] if n else []
                if bid in (0x13, 0x14) and args:
                    speaker = args[0]
                elif bid == 0x00 and args and args[0] in strings:
                    if speaker is None:
                        who = ""
                    elif speaker in speakers:
                        who = speakers[speaker]
                    else:
                        who = f"char {speaker:#x}" if speaker >= 0x10000 else f"char {speaker}"
                    use.setdefault(args[0], f"msg {who}".strip())
                elif bid in (0x01, 0x02):
                    for v in args:
                        if v in strings:
                            use.setdefault(v, "choice")
                pending = []
            elif op in ("JMPG", "CALLG"):
                use.setdefault(_u32(a, 4), "external function")
                pending = []
            else:
                pending = []
    return use


# ---------------------------------------------------------------- assembler

def _int(tok: str) -> int:
    return int(tok, 0)


def assemble(text: str) -> bytes:
    name = b""
    externs: list = []        # (label, bytes)
    strings: list = []        # (label, bytes)
    funcs: list = []          # dicts
    cur = None
    for lineno, raw in enumerate(text.splitlines(), 1):
        t = tokens(raw)
        if not t:
            continue
        try:
            head = t[0]
            if head == ".name":
                name = unquote(t[1])
            elif head == ".name_raw":
                name = bytes.fromhex(t[1])
            elif head == ".extern":
                externs.append((t[1], unquote(t[2])))
            elif head == ".string":
                strings.append((t[1], unquote(t[2])))
            elif head == ".func":
                kv = dict(x.split("=", 1) for x in t[3:])
                cur = dict(label=t[1], name=t[2], flags=_int(kv["flags"]),
                           locals=_int(kv["locals"]), params=_int(kv["params"]), body=[])
                funcs.append(cur)
            elif head == ".end":
                cur = None
            elif head.endswith(":") and len(t) == 1:
                cur["body"].append(("label", head[:-1], lineno))
            else:
                if cur is None:
                    raise AsbError("instruction outside .func")
                if head not in OPCODE:
                    raise AsbError(f"unknown instruction {head}")
                cur["body"].append((head, t[1:], lineno))
        except AsbError as e:
            raise AsbError(f"line {lineno}: {e}") from None
        except (IndexError, KeyError, ValueError) as e:
            raise AsbError(f"line {lineno}: cannot parse {raw.strip()!r} ({e})") from None

    # string table; inline "literal" operands are appended (deduplicated)
    str_label: dict = {}
    table = bytearray()
    by_text: dict = {}

    def add_string(b: bytes) -> int:
        if b in by_text:
            return by_text[b]
        off = len(table)
        table.extend(b + b"\0")
        by_text.setdefault(b, off)
        return off

    for label, b in strings:
        if label in str_label:
            raise AsbError(f"duplicate string label {label}")
        off = len(table)
        table.extend(b + b"\0")
        by_text.setdefault(b, off)
        str_label[label] = off
    ext_label: dict = {}
    ext = bytearray()
    for label, b in externs:
        ext_label[label] = len(ext)
        ext.extend(b + b"\0")
    func_label = {f["label"]: i for i, f in enumerate(funcs)}

    def sref(tok: str) -> int:
        if tok.startswith('"'):
            return add_string(unquote(tok))
        if tok not in str_label:
            raise AsbError(f"unknown string {tok}")
        return str_label[tok]

    # pass 1: sizes and labels
    labels: dict = {}
    pc = 0
    for f in funcs:
        f["off"] = pc
        for kind, args, lineno in f["body"]:
            if kind == "label":
                if args in labels:
                    raise AsbError(f"line {lineno}: duplicate label {args}")
                labels[args] = pc
                continue
            size = OPS[OPCODE[kind]][1]
            if size < 0:
                size = 2 + 4 * len(args)
            pc += size
        f["size"] = pc - f["off"]

    def target(tok: str) -> int:
        if tok in labels:
            return labels[tok]
        if tok.startswith("@"):
            return _int(tok[1:])
        raise AsbError(f"unknown label {tok}")

    def fidx(tok: str) -> int:
        if tok in func_label:
            return func_label[tok]
        raise AsbError(f"unknown function {tok}")

    code = bytearray()
    for f in funcs:
        for kind, a, lineno in f["body"]:
            if kind == "label":
                continue
            try:
                op = OPCODE[kind]
                code.append(op)
                if kind == "PUSHI":
                    tok = a[0]
                    v = sref(tok) if tok.startswith(("s", '"')) and not tok.lstrip("-").isdigit() else _int(tok)
                    code += struct.pack("<i", v)
                elif kind in ("PUSHL", "POPL", "INCL", "DECL"):
                    code.append(_int(a[0]))
                elif kind in ("PUSHG", "PUSHF", "POPG", "POPF", "INCG", "DECG"):
                    code += struct.pack("<I", _int(a[0]))
                elif kind == "STIL":
                    code.append(_int(a[0]))
                    code += struct.pack("<i", _int(a[1]))
                elif kind in ("STIG", "STIF"):
                    code += struct.pack("<Ii", _int(a[0]), _int(a[1]))
                elif kind in BRANCHES:
                    code += struct.pack("<I", target(a[0]))
                elif kind == "BTBL":
                    code.append(len(a))
                    for x in a:
                        code += struct.pack("<I", target(x))
                elif kind == "JMPL":
                    code += struct.pack("<I", fidx(a[0]))
                elif kind == "CALLL":
                    code += struct.pack("<IB", fidx(a[0]), _int(a[1]))
                elif kind in ("JMPG", "CALLG"):
                    if a[0] not in ext_label:
                        raise AsbError(f"unknown extern {a[0]}")
                    code += struct.pack("<II", ext_label[a[0]], sref(a[1]))
                    if kind == "CALLG":
                        code.append(_int(a[2]))
                elif kind == "CALLB":
                    bid = BUILTIN_IDS[a[0]] if a[0] in BUILTIN_IDS else _int(a[0])
                    code += struct.pack("<IB", bid, _int(a[1]))
                elif a:
                    raise AsbError(f"{kind} takes no operands")
            except AsbError as e:
                raise AsbError(f"line {lineno}: {e}") from None
            except (IndexError, ValueError) as e:
                raise AsbError(f"line {lineno}: bad operands for {kind} ({e})") from None

    name_offs = [sref(f["name"]) for f in funcs]
    fn = len(funcs)
    fo = 0x44
    co = fo + 20 * fn
    so = (co + len(code) + 3) & ~3
    eo = so + len(table)
    out = bytearray(struct.pack("<I", 0))
    if len(name) > 32:
        raise AsbError("script name longer than 32 bytes")
    out += name.ljust(32, b"\0")
    out += struct.pack("<8I", fo, fn, co, len(code), so, len(table), eo, len(ext))
    for f, nmo in zip(funcs, name_offs):
        out += struct.pack("<IIHHII", nmo, f["flags"], f["locals"], f["params"],
                           f["off"], f["size"])
    out += code
    out += b"\0" * (so - len(out))
    out += table
    out += ext
    return bytes(out)
