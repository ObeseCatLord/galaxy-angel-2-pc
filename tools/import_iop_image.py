#!/usr/bin/env python3
"""Inventory reached IOP reboot images; keep all firmware bytes outside source Git."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sys

from ga2 import DEFAULT_PRIVATE, DISCS, ROOT
from import_discs import BoundedIso9660, InventoryError, find_iso, parse_elf32_little


def parse_romdir(data: bytes):
    if not 32 <= len(data) <= 16 * 1024 * 1024:
        raise InventoryError("IOP image size is outside supported bounds")
    for start in range(0, min(len(data) - 15, 512 * 1024), 16):
        if data[start:start + 10] != b"RESET\0\0\0\0\0":
            continue
        entries = []
        names = set()
        file_offset = 0
        for index in range(512):
            position = start + index * 16
            if position + 16 > len(data):
                raise InventoryError("truncated ROMDIR")
            raw_name, ext_size, size = struct.unpack_from("<10sHI", data, position)
            if raw_name == bytes(10):
                if ext_size or size:
                    raise InventoryError("invalid ROMDIR terminator")
                break
            name_bytes = raw_name.rstrip(b"\0")
            if not re.fullmatch(rb"[A-Z0-9_]{1,10}", name_bytes):
                raise InventoryError("unsafe ROMDIR file name")
            name = name_bytes.decode("ascii")
            if name in names or file_offset > len(data) or size > len(data) - file_offset:
                raise InventoryError("duplicate ROMDIR name or out-of-bounds file extent")
            names.add(name)
            entries.append({"name": name, "offset": file_offset, "size": size,
                            "extinfo_size": ext_size})
            file_offset += (size + 15) & ~15
        else:
            raise InventoryError("ROMDIR entry limit exceeded")
        by_name = {item["name"]: item for item in entries}
        directory = by_name.get("ROMDIR")
        extension = by_name.get("EXTINFO")
        if not directory or directory["offset"] != start or directory["size"] < (len(entries) + 1) * 16:
            raise InventoryError("ROMDIR self extent does not cover its records")
        if not extension or sum(item["extinfo_size"] for item in entries) != extension["size"]:
            raise InventoryError("ROMDIR extension metadata size mismatch")
        return entries
    raise InventoryError("ROMDIR RESET record not found")


def import_image(private: Path, disc: str):
    image = find_iso(private / "discs" / disc)
    with BoundedIso9660(image) as iso:
        found = [item for item in iso.manifest_files() if item.path.endswith("/IOPRP300.IMG")]
        if len(found) != 1:
            raise InventoryError("expected one IOPRP300.IMG on the supplied disc")
        item = found[0]
        data = iso.read_small_file(item, 16 * 1024 * 1024)
    entries = parse_romdir(data)
    output = private / "firmware" / disc
    output.mkdir(parents=True, exist_ok=True)
    image_path = output / "IOPRP300.IMG"
    if image_path.exists() and image_path.read_bytes() != data:
        raise InventoryError("existing private firmware image differs; use another private root")
    image_path.write_bytes(data)
    modules = 0
    for entry in entries:
        payload = data[entry["offset"]:entry["offset"] + entry["size"]]
        entry["sha256"] = hashlib.sha256(payload).hexdigest()
        if payload.startswith(b"\x7fELF"):
            entry["elf"] = parse_elf32_little(payload)
            path = output / "rom0" / (entry["name"] + ".IRX")
            path.parent.mkdir(exist_ok=True)
            if path.exists() and path.read_bytes() != payload:
                raise InventoryError("existing private ROM module differs")
            path.write_bytes(payload)
            modules += 1
    report = {"disc_id": disc, "image_path": item.path, "image_size": len(data),
              "image_sha256": hashlib.sha256(data).hexdigest(), "modules": modules,
              "entries": entries, "runtime_status": "inventoried-not-booted"}
    (output / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"{disc}: {modules} private ELF modules, image SHA-256 {report['image_sha256']}")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("disc", nargs="?", choices=DISCS)
    parser.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE)
    args = parser.parse_args()
    private = args.private_root.resolve()
    if private == ROOT or ROOT in private.parents:
        parser.error("private firmware must stay outside source Git")
    failures = 0
    for disc in [args.disc] if args.disc else DISCS:
        try:
            import_image(private, disc)
        except (InventoryError, OSError) as error:  # one disc without the image must not stop the rest
            print(f"{disc}: {error}", file=sys.stderr)
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
