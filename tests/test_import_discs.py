import importlib.util
import struct
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "tools" / "import_discs.py"
SPEC = importlib.util.spec_from_file_location("import_discs", MODULE_PATH)
assert SPEC and SPEC.loader
import_discs = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = import_discs
SPEC.loader.exec_module(import_discs)


def directory_record(lba: int, size: int, flags: int, name: bytes) -> bytes:
    length = 33 + len(name) + (len(name) % 2 == 0)
    record = bytearray(length)
    record[0] = length
    struct.pack_into("<I", record, 2, lba)
    struct.pack_into(">I", record, 6, lba)
    struct.pack_into("<I", record, 10, size)
    struct.pack_into(">I", record, 14, size)
    record[25] = flags
    struct.pack_into("<H", record, 28, 1)
    struct.pack_into(">H", record, 30, 1)
    record[32] = len(name)
    record[33 : 33 + len(name)] = name
    return bytes(record)


def minimal_elf(entry: int = 0x00100000, with_symbol: bool = True) -> bytes:
    phoff, shoff, phnum = 52, 84, 2 if with_symbol else 1
    size = shoff + shnum * 40 if (shnum := (3 if with_symbol else 2)) else 0
    value = bytearray(max(size, 256))
    struct.pack_into("<16sHHIIIIIHHHHHH", value, 0,
                     b"\x7fELF\x01\x01\x01" + b"\0" * 9, 2, 8, 1, entry, phoff, shoff,
                     0, 52, 32, phnum, 40, shnum, 0)
    struct.pack_into("<IIIIIIII", value, phoff, 1, 0, entry, entry, 52, 52, 5, 16)
    struct.pack_into("<IIIIIIII", value, phoff + 32, 0, 0, 0, 0, 0, 0, 0, 0)
    struct.pack_into("<IIIIIIIIII", value, shoff + 40, 0, 1, 6, entry, 0, 52, 0, 0, 4, 0)
    if with_symbol:
        struct.pack_into("<IIIIIIIIII", value, shoff + 80, 0, 2, 0, 0, 240, 16, 0, 0, 4, 16)
        struct.pack_into("<IIIBBH", value, 240, 0, entry, 0, 0, 0, 1)
    return bytes(value)


def synthetic_iso(path: Path) -> None:
    sectors = 40
    image = bytearray(sectors * 2048)
    root_lba, subdir_lba = 20, 21
    system_cnf = b"BOOT2 = cdrom0:\\GAME\\BOOT.ELF;1\r\n"
    boot = minimal_elf()
    irx = minimal_elf(0x00200000, with_symbol=False)
    root_entries = [
        directory_record(root_lba, 2048, 2, b"\0"),
        directory_record(root_lba, 2048, 2, b"\1"),
        directory_record(22, len(system_cnf), 0, b"SYSTEM.CNF;1"),
        directory_record(subdir_lba, 2048, 2, b"GAME"),
    ]
    sub_entries = [
        directory_record(subdir_lba, 2048, 2, b"\0"),
        directory_record(root_lba, 2048, 2, b"\1"),
        directory_record(23, len(boot), 0, b"BOOT.ELF;1"),
        directory_record(24, len(irx), 0, b"IOP.IRX;1"),
    ]
    image[root_lba * 2048 : root_lba * 2048 + sum(map(len, root_entries))] = b"".join(root_entries)
    image[subdir_lba * 2048 : subdir_lba * 2048 + sum(map(len, sub_entries))] = b"".join(sub_entries)
    image[22 * 2048 : 22 * 2048 + len(system_cnf)] = system_cnf
    image[23 * 2048 : 23 * 2048 + len(boot)] = boot
    image[24 * 2048 : 24 * 2048 + len(irx)] = irx
    pvd = memoryview(image)[16 * 2048 : 17 * 2048]
    pvd[0], pvd[1:6], pvd[6] = 1, b"CD001", 1
    struct.pack_into("<I", pvd, 80, sectors)
    struct.pack_into("<H", pvd, 128, 2048)
    pvd[156 : 156 + 34] = directory_record(root_lba, 2048, 2, b"\0")[:34]
    image[17 * 2048], image[17 * 2048 + 1 : 17 * 2048 + 6], image[17 * 2048 + 6] = 255, b"CD001", 1
    path.write_bytes(image)


class IsoAndElfTests(unittest.TestCase):
    def test_recursive_manifest_system_boot_and_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            private = Path(temporary)
            disc_dir = private / "discs" / "zettai"
            disc_dir.mkdir(parents=True)
            synthetic_iso(disc_dir / "disc.iso")
            manifest_path = import_discs.import_disc("zettai", private)
            manifest = __import__("json").loads(manifest_path.read_text())
            self.assertEqual([file["path"] for file in manifest["files"]],
                             ["/GAME/BOOT.ELF", "/GAME/IOP.IRX", "/SYSTEM.CNF"])
            self.assertEqual(manifest["boot"]["path"], "/GAME/BOOT.ELF")
            self.assertTrue(manifest["boot"]["elf"]["machine_is_mips"])
            self.assertEqual(manifest["boot"]["elf"]["symbol_presence"]["defined_entry_count"], 1)
            self.assertEqual(manifest["irx_modules"][0]["path"], "/GAME/IOP.IRX")
            self.assertTrue((private / "extracted/zettai/GAME/BOOT.ELF").is_file())

    def test_elf_parser_rejects_out_of_bounds_program_header(self) -> None:
        malformed = bytearray(minimal_elf(with_symbol=False))
        struct.pack_into("<I", malformed, 52 + 4, len(malformed) - 4)
        struct.pack_into("<I", malformed, 52 + 16, 16)
        with self.assertRaisesRegex(import_discs.InventoryError, "program segment"):
            import_discs.parse_elf32_little(bytes(malformed))

    def test_system_cnf_rejects_parent_directory(self) -> None:
        with self.assertRaisesRegex(import_discs.InventoryError, "unsafe"):
            import_discs.parse_system_cnf(b"BOOT = cdrom0:\\\\..\\BOOT.ELF;1\n")


if __name__ == "__main__":
    unittest.main()
