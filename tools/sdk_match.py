#!/usr/bin/env python3
"""Locate Zettai's verified SDK functions in another GA2 executable.

All five discs link the same Sony SDK build. Each binding in
configs/sdk-bindings.json (Zettai) is matched instruction for instruction in
the target executable with immediates and jump targets masked; a binding is
accepted only when the match is unique. The SIF RPC state block is read from
the same instructions of sceSifInitRpc. Writes configs/sdk-bindings-<disc>.json
and prints the native override row (native/compat/ga2_sdk.cpp).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ga2  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class Code:
    """The executable words of an ELF: every executable PT_LOAD segment, addressed by virtual address."""

    def __init__(self, elf: bytes):
        phoff, = struct.unpack_from("<I", elf, 0x1C)
        phentsize, phnum = struct.unpack_from("<HH", elf, 0x2A)
        self.segments = []  # (vaddr, words)
        for i in range(phnum):
            p_type, offset, vaddr, _, filesz, _, flags, _ = struct.unpack_from("<8I", elf, phoff + i * phentsize)
            if p_type == 1 and flags & 1:
                self.segments.append((vaddr, struct.unpack_from(f"<{filesz // 4}I", elf, offset)))
        if not self.segments:
            raise ValueError("no executable segment")

    def words(self, address: int, count: int):
        """`count` words from `address`; they must lie inside one executable segment."""
        for vaddr, words in self.segments:
            first = (address - vaddr) // 4
            if address >= vaddr and address % 4 == 0 and first + count <= len(words):
                return words[first:first + count]
        raise ValueError(f"0x{address:08x}+{count} words are not inside an executable segment")


def mask(word: int) -> int:
    op = word >> 26
    if op in (2, 3):
        return op << 26          # J/JAL target
    if op in (0, 0x1C):
        return word              # SPECIAL/MMI: register-only
    if op in (0x10, 0x11, 0x12) and (word >> 21) & 31 != 8:
        return word              # COP0/1/2 register and function fields; only the BC* branches carry an offset
    return word & 0xFFFF0000     # I-type immediate


def function_ends(analysis: Path):
    ends = {}
    for line in (analysis / "functions.csv").read_text().splitlines()[1:]:
        fields = line.rsplit(",", 3)
        if len(fields) == 4:
            ends[int(fields[1], 16)] = int(fields[2], 16)
    return ends


def absolute_constants(code, address, count):
    """(instruction index, value) for lui-based absolute addresses."""
    out, high = [], {}
    for k, w in enumerate(code.words(address, count)):
        op, rs, rt, imm = w >> 26, (w >> 21) & 31, (w >> 16) & 31, w & 0xFFFF
        if op == 0x0F:
            high[rt] = imm << 16
        elif rs in high and op in (0x09, 0x0D, 0x20, 0x21, 0x23, 0x24, 0x25, 0x28, 0x29, 0x2B, 0x37, 0x3F, 0x1E, 0x1F):
            signed = imm - 0x10000 if (imm & 0x8000 and op != 0x0D) else imm
            out.append((k, (high[rs] + signed) & 0xFFFFFFFF))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-root", type=Path, default=ga2.DEFAULT_PRIVATE)
    parser.add_argument("--output", type=Path, help="write the profile here (default: configs/sdk-bindings-<disc>.json)")
    parser.add_argument("disc", choices=[d for d in ga2.DISCS if d != "zettai"])
    args = parser.parse_args()
    private = args.private_root.resolve()

    reference = json.loads((ROOT / "configs/sdk-bindings.json").read_text())
    _, zettai_boot = ga2.manifest(private, "zettai")
    _, boot = ga2.manifest(private, args.disc)
    zelf, elf = Path(zettai_boot).read_bytes(), Path(boot).read_bytes()
    zcode, code = Code(zelf), Code(elf)
    # Index every four-word window of the target by its masked words.
    index = {}
    for segment, (vaddr, words) in enumerate(code.segments):
        masked = [mask(x) for x in words]
        for i in range(len(masked) - 3):
            index.setdefault(tuple(masked[i:i + 4]), []).append((segment, i))
    zends = function_ends(private / "analysis/zettai")

    matches = {}
    for binding in reference["bindings"]:
        address = int(binding["address"], 16)
        count = min(max((zends.get(address, address + 128) - address) // 4, 4), 64)
        pattern = [mask(x) for x in zcode.words(address, count)]
        hits = []
        for segment, i in index.get(tuple(pattern[:4]), []):
            masked = [mask(x) for x in code.segments[segment][1][i:i + count]]
            if masked == pattern:
                hits.append(code.segments[segment][0] + 4 * i)
        if len(hits) != 1:
            raise SystemExit(f"{binding['name']}: {len(hits)} matches; refusing an ambiguous binding")
        matches[binding["name"]] = (hits[0], count)

    # RPC state block: Zettai's sceSifInitRpc builds 0x832400 (queue root at +0x28).
    zinit = int(next(b["address"] for b in reference["bindings"] if b["name"] == "sceSifInitRpc"), 16)
    init, count = matches["sceSifInitRpc"]
    target = dict(absolute_constants(code, init, count))
    states = {target[k] for k, value in absolute_constants(zcode, zinit, count) if value == 0x00832400 and k in target}
    if len(states) != 1:
        raise SystemExit(f"RPC state block not uniquely derived: {states}")
    state = states.pop()

    profile = dict(reference)
    profile.update(disc_id=args.disc, elf_sha256=hashlib.sha256(elf).hexdigest(),
                   elf_crc32=f"{zlib.crc32(elf):08x}", entry=f"{struct.unpack_from('<I', elf, 0x18)[0]:08x}")
    # Nothing about Zettai's provenance carries over: the source and limits say what this
    # profile is, and the Zettai verification record does not describe this disc.
    profile["source"] = (f"tools/sdk_match.py: configs/sdk-bindings.json (Zettai) matched into {args.disc}'s executable; "
                         "tools/ga2.py generates the runtime stubs from it")
    profile["limits"] = ["Addresses are derived by unique signature match against Zettai's verified bindings; "
                         "nothing in this profile has been verified on this disc by itself",
                         "CLI import/launch verifies SHA-256; runtime descriptor uses filename, entry and CRC32"]
    profile.pop("verification", None)
    profile["bindings"] = []
    for binding in reference["bindings"]:
        item = dict(binding)
        item["address"] = f"{matches[binding['name']][0]:08x}"
        item["evidence"] = "signature-matched-to-zettai-binding (immediates and jump targets masked, unique)"
        item["runtime_status"] = "derived-unverified"
        profile["bindings"].append(item)
    output = args.output or ROOT / f"configs/sdk-bindings-{args.disc}.json"
    output.write_text(json.dumps(profile, indent=2) + "\n")

    def at(name):
        return f"0x{matches[name][0]:08X}u"
    print(f'    {{"{args.disc}", "{Path(boot).name}", 0x{struct.unpack_from("<I", elf, 0x18)[0]:08X}u, 0x{zlib.crc32(elf):08X}u, '
          f'0x{state + 0x28:08X}u, {at("sceSifInitRpc")}, {at("sceSifBindRpc")}, {at("sceSifCallRpc")}, '
          f'{at("sceSifCheckStatRpc")}, {at("sceCdInit")}}},')


if __name__ == "__main__":
    main()
