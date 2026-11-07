#!/usr/bin/env python3
"""Create small, reproducible inventories from local PS2 ISO9660 images.

The tool intentionally reads directory metadata and only streams the BOOT ELF and
``.IRX`` modules.  It never copies the disc image or game assets into this repo.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator, Sequence


SECTOR_SIZE = 2048
# The private root (ga2.default_private_root: $GA2_PRIVATE_ROOT, the older
# sibling folder, or private/ in the checkout); --private-root overrides it.
_SOURCE = Path(__file__).resolve().parents[1]
_SIBLING = _SOURCE.parent.parent / "galaxy-angel-ii-private"
DEFAULT_PRIVATE_ROOT = Path(os.environ.get("GA2_PRIVATE_ROOT") or
                            (_SIBLING if _SIBLING.is_dir() else _SOURCE / "private"))
DISC_IDS = ("zettai", "mugen-1", "mugen-2", "eigou-1", "eigou-2")
MAX_VOLUME_DESCRIPTORS = 100
MAX_DIRECTORY_BYTES = 64 * 1024 * 1024
MAX_DIRECTORY_DEPTH = 64
MAX_MANIFEST_FILES = 100_000
MAX_EXECUTABLE_BYTES = 128 * 1024 * 1024
MAX_EXTRACTED_TOTAL_BYTES = 512 * 1024 * 1024
MAX_ELF_TABLE_ENTRIES = 8192
MAX_ELF_SYMBOLS = 2_000_000


class InventoryError(RuntimeError):
    """The disc image is absent or does not meet the narrow reader's contract."""


@dataclass(frozen=True)
class Extent:
    lba: int
    size: int


@dataclass(frozen=True)
class IsoFile:
    path: str
    extents: tuple[Extent, ...]
    is_directory: bool = False
    continues_in_next_record: bool = False

    @property
    def size(self) -> int:
        return sum(extent.size for extent in self.extents)

    @property
    def lba(self) -> int:
        return self.extents[0].lba


class BoundedIso9660:
    """A deliberately small ISO9660 reader with strict offset and size checks."""

    def __init__(self, image_path: Path) -> None:
        self.image_path = image_path
        self._handle = image_path.open("rb")
        self.size = os.fstat(self._handle.fileno()).st_size
        self.volume_space_size: int | None = None
        self.root: IsoFile | None = None

    def __enter__(self) -> "BoundedIso9660":
        return self

    def __exit__(self, *_: object) -> None:
        self._handle.close()

    def read_at(self, offset: int, count: int) -> bytes:
        if offset < 0 or count < 0 or offset > self.size or count > self.size - offset:
            raise InventoryError("ISO read is outside the image bounds")
        self._handle.seek(offset)
        value = self._handle.read(count)
        if len(value) != count:
            raise InventoryError("ISO image ended during a bounded read")
        return value

    def _validate_extent(self, lba: int, size: int) -> Extent:
        if lba < 0 or size < 0:
            raise InventoryError("ISO directory record has a negative extent")
        offset = lba * SECTOR_SIZE
        if offset > self.size or size > self.size - offset:
            raise InventoryError("ISO directory record extent is outside the image")
        if self.volume_space_size is not None and (lba > self.volume_space_size or
                                                   size > (self.volume_space_size - lba) * SECTOR_SIZE):
            raise InventoryError("ISO directory record extent is outside the declared volume")
        return Extent(lba=lba, size=size)

    def load_primary_volume(self) -> IsoFile:
        for descriptor_number in range(MAX_VOLUME_DESCRIPTORS):
            descriptor = self.read_at((16 + descriptor_number) * SECTOR_SIZE, SECTOR_SIZE)
            if descriptor[1:6] != b"CD001" or descriptor[6] != 1:
                raise InventoryError("image does not contain a valid ISO9660 volume descriptor")
            descriptor_type = descriptor[0]
            if descriptor_type == 1:
                block_size = struct.unpack_from("<H", descriptor, 128)[0]
                if block_size != SECTOR_SIZE:
                    raise InventoryError("only 2048-byte ISO9660 logical blocks are supported")
                self.volume_space_size = struct.unpack_from("<I", descriptor, 80)[0]
                root = self._record_from_bytes(descriptor, 156, SECTOR_SIZE - 156)
                if not root.is_directory:
                    raise InventoryError("ISO9660 root record is not a directory")
                self.root = IsoFile(path="/", extents=root.extents, is_directory=True)
                return self.root
            if descriptor_type == 255:
                break
        raise InventoryError("ISO9660 primary volume descriptor was not found")

    def _record_from_bytes(self, data: bytes, offset: int, available: int) -> IsoFile:
        if available < 1:
            raise InventoryError("truncated ISO directory record")
        record_length = data[offset]
        if record_length < 34 or record_length > available:
            raise InventoryError("invalid ISO directory record length")
        record = data[offset : offset + record_length]
        name_length = record[32]
        if 33 + name_length > len(record):
            raise InventoryError("truncated ISO directory identifier")
        lba = struct.unpack_from("<I", record, 2)[0]
        size = struct.unpack_from("<I", record, 10)[0]
        flags = record[25]
        name = record[33 : 33 + name_length]
        if name not in (b"\x00", b"\x01"):
            try:
                decoded_name = name.decode("ascii")
            except UnicodeDecodeError as error:
                raise InventoryError("non-ASCII ISO9660 identifiers are unsupported") from error
            decoded_name = re.sub(r";[0-9]+$", "", decoded_name)
            if not decoded_name or "/" in decoded_name or "\\" in decoded_name:
                raise InventoryError("unsafe ISO9660 directory identifier")
            path = decoded_name
        else:
            path = "." if name == b"\x00" else ".."
        return IsoFile(path=path, extents=(self._validate_extent(lba, size),),
                       is_directory=bool(flags & 2), continues_in_next_record=bool(flags & 0x80))

    def _directory_records(self, directory: IsoFile) -> Iterator[IsoFile]:
        if directory.size > MAX_DIRECTORY_BYTES:
            raise InventoryError("ISO directory exceeds the metadata reader size limit")
        if len(directory.extents) != 1:
            raise InventoryError("multi-extent directories are unsupported")
        extent = directory.extents[0]
        data = self.read_at(extent.lba * SECTOR_SIZE, extent.size)
        position = 0
        while position < len(data):
            record_length = data[position]
            if record_length == 0:
                position = ((position // SECTOR_SIZE) + 1) * SECTOR_SIZE
                continue
            if record_length > SECTOR_SIZE - (position % SECTOR_SIZE):
                raise InventoryError("ISO directory record crosses a logical block boundary")
            record = self._record_from_bytes(data, position, len(data) - position)
            yield record
            position += record_length

    def manifest_files(self) -> list[IsoFile]:
        root = self.root or self.load_primary_volume()
        files: list[IsoFile] = []
        seen_directories: set[tuple[int, int]] = set()

        def walk(directory: IsoFile, parent: PurePosixPath, depth: int) -> None:
            if depth > MAX_DIRECTORY_DEPTH:
                raise InventoryError("ISO directory nesting exceeds the reader depth limit")
            key = (directory.lba, directory.size)
            if key in seen_directories:
                raise InventoryError("ISO directory graph contains a cycle or duplicate extent")
            seen_directories.add(key)
            pending: IsoFile | None = None
            for record in self._directory_records(directory):
                if record.path in (".", ".."):
                    continue
                child_path = str(parent / record.path)
                item = IsoFile(path=child_path, extents=record.extents, is_directory=record.is_directory,
                               continues_in_next_record=record.continues_in_next_record)
                if pending is not None:
                    if item.path != pending.path or item.is_directory:
                        raise InventoryError("ISO multi-extent file records are not consecutive")
                    pending = IsoFile(path=item.path, extents=pending.extents + item.extents,
                                      continues_in_next_record=item.continues_in_next_record)
                    if pending.continues_in_next_record:
                        continue
                    files.append(pending)
                    pending = None
                    if len(files) > MAX_MANIFEST_FILES:
                        raise InventoryError("ISO file count exceeds the manifest limit")
                    continue
                if item.continues_in_next_record:
                    if item.is_directory:
                        raise InventoryError("multi-extent directories are unsupported")
                    pending = item
                elif item.is_directory:
                    walk(item, PurePosixPath(child_path), depth + 1)
                else:
                    files.append(item)
                    if len(files) > MAX_MANIFEST_FILES:
                        raise InventoryError("ISO file count exceeds the manifest limit")
            if pending is not None:
                raise InventoryError("ISO multi-extent file is missing its final record")

        walk(root, PurePosixPath("/"), 0)
        paths = [item.path.casefold() for item in files]
        if len(paths) != len(set(paths)):
            raise InventoryError("ISO contains duplicate file paths")
        return sorted(files, key=lambda item: item.path.casefold())

    def iter_file_chunks(self, item: IsoFile, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        if item.is_directory:
            raise InventoryError("cannot extract an ISO directory")
        for extent in item.extents:
            remaining = extent.size
            offset = extent.lba * SECTOR_SIZE
            while remaining:
                count = min(chunk_size, remaining)
                yield self.read_at(offset, count)
                offset += count
                remaining -= count

    def read_small_file(self, item: IsoFile, limit: int) -> bytes:
        if item.size > limit:
            raise InventoryError("requested metadata file exceeds its safe read limit")
        return b"".join(self.iter_file_chunks(item))


def parse_system_cnf(contents: bytes) -> str:
    """Return the normalized disc path from a SYSTEM.CNF BOOT or BOOT2 entry."""
    try:
        text = contents.decode("ascii")
    except UnicodeDecodeError as error:
        raise InventoryError("SYSTEM.CNF is not ASCII") from error
    # BOOT2 is the PS2 key; BOOT (PS1) only when there is no BOOT2.
    match = (re.search(r"(?im)^[ \t]*BOOT2[ \t]*=[ \t]*([^\r\n]+)", text) or
             re.search(r"(?im)^[ \t]*BOOT[ \t]*=[ \t]*([^\r\n]+)", text))
    if not match:
        raise InventoryError("SYSTEM.CNF does not define BOOT or BOOT2")
    value = match.group(1).strip().strip('"')
    value = value.split()[0] if value else ""
    value = re.sub(r"(?i)^cdrom(?:0)?\s*:\\?", "", value).replace("\\", "/")
    value = value.lstrip("/")
    value = re.sub(r";[0-9]+$", "", value)
    parts = PurePosixPath(value).parts
    if not value or any(part in ("", ".", "..", "/") for part in parts):
        raise InventoryError("SYSTEM.CNF BOOT path is unsafe")
    return "/" + "/".join(parts)


def parse_elf32_little(contents: bytes) -> dict[str, object]:
    """Inspect a little-endian ELF32 executable without retaining string tables."""
    if len(contents) < 52 or contents[:4] != b"\x7fELF":
        raise InventoryError("extracted file is not an ELF executable")
    if contents[4] != 1 or contents[5] != 1:
        raise InventoryError("executable is not little-endian ELF32")
    header = struct.unpack_from("<16sHHIIIIIHHHHHH", contents)
    (_, elf_type, machine, version, entry, phoff, shoff, flags, ehsize, phentsize,
     phnum, shentsize, shnum, shstrndx) = header
    if version != 1 or ehsize < 52:
        raise InventoryError("ELF header has an unsupported version or size")

    def table_bounds(offset: int, entry_size: int, count: int, minimum_size: int, label: str) -> None:
        if count > MAX_ELF_TABLE_ENTRIES or (count and entry_size < minimum_size):
            raise InventoryError(f"ELF {label} table is too large or malformed")
        if offset < 0 or offset > len(contents) or count * entry_size > len(contents) - offset:
            raise InventoryError(f"ELF {label} table is outside the executable")

    table_bounds(phoff, phentsize, phnum, 32, "program header")
    table_bounds(shoff, shentsize, shnum, 40, "section header")
    if shnum and shstrndx not in (0, 0xFFFF) and shstrndx >= shnum:
        raise InventoryError("ELF section-name index is outside the section table")

    loads: list[dict[str, int]] = []
    for index in range(phnum):
        offset = phoff + index * phentsize
        p_type, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz, p_flags, p_align = struct.unpack_from(
            "<IIIIIIII", contents, offset
        )
        if p_filesz > len(contents) - p_offset:
            raise InventoryError("ELF program segment is outside the executable")
        if p_type == 1:
            loads.append({"offset": p_offset, "vaddr": p_vaddr, "paddr": p_paddr,
                          "file_size": p_filesz, "memory_size": p_memsz,
                          "flags": p_flags, "alignment": p_align})

    sections: list[dict[str, int]] = []
    symbol_tables = 0
    symbol_count = 0
    defined_symbols = 0
    for index in range(shnum):
        offset = shoff + index * shentsize
        name, section_type, section_flags, address, section_offset, section_size, link, info, alignment, entry_size = struct.unpack_from(
            "<IIIIIIIIII", contents, offset
        )
        if section_type != 8 and section_size > len(contents) - section_offset:
            raise InventoryError("ELF section is outside the executable")
        sections.append({"index": index, "type": section_type, "flags": section_flags,
                         "address": address, "offset": section_offset, "size": section_size,
                         "link": link, "info": info, "alignment": alignment,
                         "entry_size": entry_size})
        if section_type in (2, 11):
            if entry_size < 16 or section_size % entry_size:
                raise InventoryError("ELF symbol table has an invalid entry size")
            count = section_size // entry_size
            if count > MAX_ELF_SYMBOLS - symbol_count:
                raise InventoryError("ELF symbol tables exceed the inspection limit")
            if link >= shnum:
                raise InventoryError("ELF symbol table has an invalid string-table link")
            symbol_tables += 1
            symbol_count += count
            for symbol_index in range(count):
                symbol_offset = section_offset + symbol_index * entry_size
                shndx = struct.unpack_from("<H", contents, symbol_offset + 14)[0]
                if shndx != 0:
                    defined_symbols += 1

    return {
        "format": "ELF32",
        "endianness": "little",
        "type": elf_type,
        "machine": machine,
        "machine_is_mips": machine == 8,
        "entry": entry,
        "flags": flags,
        "load_segments": loads,
        "sections": sections,
        "symbol_presence": {
            "has_symbol_table": bool(symbol_tables),
            "table_count": symbol_tables,
            "entry_count": symbol_count,
            "defined_entry_count": defined_symbols,
        },
    }


def find_iso(disc_directory: Path) -> Path:
    """The one ISO image in a disc folder (any case of ".iso")."""
    if not disc_directory.is_dir():
        raise InventoryError(f"disc directory is missing: {disc_directory}")
    images = sorted(path for path in disc_directory.iterdir() if path.is_file() and path.suffix.casefold() == ".iso")
    if not images:
        raise InventoryError(f"no ISO image is available in {disc_directory}; extraction may still be running")
    if len(images) != 1:
        raise InventoryError(f"expected exactly one ISO image in {disc_directory}, found {len(images)}")
    return images[0]


def has_iso(disc_directory: Path) -> bool:
    return disc_directory.is_dir() and any(
        path.is_file() and path.suffix.casefold() == ".iso" for path in disc_directory.iterdir())


_find_iso = find_iso


def _manifest_item(item: IsoFile) -> dict[str, object]:
    return {
        "path": item.path,
        "lba": item.lba,
        "size": item.size,
        "extents": [{"lba": extent.lba, "size": extent.size} for extent in item.extents],
    }


def _safe_extract_path(root: Path, disc_path: str) -> Path:
    relative = PurePosixPath(disc_path.lstrip("/"))
    if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
        raise InventoryError("refusing unsafe extraction path")
    return root.joinpath(*relative.parts)


def _extract_executable(reader: BoundedIso9660, item: IsoFile, output_root: Path) -> tuple[Path, str, bytes]:
    if item.size > MAX_EXECUTABLE_BYTES:
        raise InventoryError(f"refusing to extract oversized executable at {item.path}")
    target = _safe_extract_path(output_root, item.path)
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=target.parent, prefix=f".{target.name}.", delete=False) as temporary:
            temporary_name = temporary.name
            for chunk in reader.iter_file_chunks(item):
                digest.update(chunk)
                temporary.write(chunk)
        os.replace(temporary_name, target)
    except (OSError, InventoryError) as error:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)
        if isinstance(error, InventoryError):
            raise
        raise InventoryError(f"could not extract {item.path}: {error}") from error
    return target, digest.hexdigest(), target.read_bytes()


def _check_verified_build(disc_id: str, elf_sha256: str, boot_path: str) -> None:
    """A misfiled disc (Mugen 2 in discs/mugen-1) must not be extracted under the wrong ID."""
    builds = json.loads((Path(__file__).resolve().parents[1] / "configs/verified-builds.json").read_text())["builds"]
    expected = next((item for item in builds if item["disc_id"] == disc_id), None)
    if expected is None:
        raise InventoryError(f"{disc_id}: no entry in configs/verified-builds.json (use --unverified to import anyway)")
    if elf_sha256 != expected["elf_sha256"]:
        raise InventoryError(f"the disc's program {boot_path} is not the verified build "
                             f"{expected['serial']} (wrong disc in this folder? --unverified imports it anyway)")


def import_disc(disc_id: str, private_root: Path, verify: bool = True) -> Path:
    if disc_id not in DISC_IDS:
        raise InventoryError(f"unknown disc ID {disc_id!r}; expected one of: {', '.join(DISC_IDS)}")
    image_path = _find_iso(private_root / "discs" / disc_id)
    output_root = private_root / "extracted" / disc_id
    manifest_path = private_root / "manifests" / f"{disc_id}.json"
    with BoundedIso9660(image_path) as reader:
        root = reader.load_primary_volume()
        files = reader.manifest_files()
        by_path = {item.path.casefold(): item for item in files}
        system_cnf = by_path.get("/system.cnf")
        if system_cnf is None:
            raise InventoryError("ISO is missing root SYSTEM.CNF")
        boot_path = parse_system_cnf(reader.read_small_file(system_cnf, 1024 * 1024))
        boot = by_path.get(boot_path.casefold())
        if boot is None:
            raise InventoryError("SYSTEM.CNF BOOT path does not resolve to an ISO file")
        irx_modules = [item for item in files if item.path.casefold().endswith(".irx") and item != boot]
        selected = [boot, *irx_modules]
        total = sum(item.size for item in selected)
        if total > MAX_EXTRACTED_TOTAL_BYTES:
            raise InventoryError("selected executable files exceed the extraction size limit")

        boot_target, boot_hash, boot_bytes = _extract_executable(reader, boot, output_root)
        if verify:
            _check_verified_build(disc_id, boot_hash, boot.path)
        boot_data: dict[str, object] = {"path": boot.path, "sha256": boot_hash,
                                        "extracted_path": boot_target.relative_to(private_root).as_posix(),
                                        "elf": parse_elf32_little(boot_bytes)}
        irx_data: list[dict[str, object]] = []
        for module in irx_modules:
            target, digest, contents = _extract_executable(reader, module, output_root)
            irx_data.append({"path": module.path, "sha256": digest,
                             "extracted_path": target.relative_to(private_root).as_posix(),
                             "elf": parse_elf32_little(contents)})

    document = {
        "schema_version": 1,
        "disc_id": disc_id,
        "iso9660": {"logical_block_size": SECTOR_SIZE, "volume_space_size": reader.volume_space_size,
                      "root_lba": root.lba, "root_size": root.size},
        "files": [_manifest_item(item) for item in files],
        "boot": boot_data,
        "irx_modules": irx_data,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=manifest_path.parent,
                                         prefix=f".{manifest_path.name}.", delete=False) as temporary:
            temporary_name = temporary.name
            json.dump(document, temporary, indent=2, sort_keys=True)
            temporary.write("\n")
        os.replace(temporary_name, manifest_path)
    except OSError as error:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)
        raise InventoryError(f"could not write {manifest_path}: {error}") from error
    return manifest_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inventory PS2 ISO9660 discs and extract only executable files.")
    parser.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE_ROOT)
    parser.add_argument("--disc", choices=DISC_IDS, help="import one disc ID (default: every known disc)")
    parser.add_argument("--unverified", action="store_true",
                        help="import a program that is not the verified build for the disc ID")
    arguments = parser.parse_args(argv)
    disc_ids = (arguments.disc,) if arguments.disc else DISC_IDS
    failures: list[str] = []
    for disc_id in disc_ids:
        try:
            manifest = import_disc(disc_id, arguments.private_root, verify=not arguments.unverified)
            print(f"{disc_id}: wrote {manifest}")
        except (InventoryError, OSError) as error:  # one bad disc must not stop the others
            failures.append(f"{disc_id}: {error}")
    for failure in failures:
        print(failure, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
