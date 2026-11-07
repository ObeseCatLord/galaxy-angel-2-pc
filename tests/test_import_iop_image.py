import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from import_iop_image import parse_romdir
from import_discs import InventoryError


def fixture():
    records = [(b"RESET", 0, 0), (b"ROMDIR", 0, 64), (b"EXTINFO", 0, 0)]
    return b"".join(struct.pack("<10sHI", *record) for record in records) + bytes(16)


class RomdirTests(unittest.TestCase):
    def test_self_extent_and_unaligned_last_file(self):
        records = [(b"RESET", 0, 0), (b"ROMDIR", 0, 80), (b"EXTINFO", 0, 0), (b"TEST", 0, 3)]
        data = b"".join(struct.pack("<10sHI", *record) for record in records) + bytes(16) + b"abc"
        entries = parse_romdir(data)
        self.assertEqual(entries[-1]["offset"], 80)
        self.assertEqual(data[entries[-1]["offset"]:][:3], b"abc")

    def test_rejects_truncated_extent(self):
        data = bytearray(fixture())
        struct.pack_into("<I", data, 44, 1024)
        with self.assertRaises(InventoryError):
            parse_romdir(bytes(data))

    def test_rejects_duplicate_and_unsafe_names(self):
        for name in (b"RESET", b"../FILE"):
            data = bytearray(fixture())
            data[32:42] = name.ljust(10, b"\0")
            with self.assertRaises(InventoryError):
                parse_romdir(bytes(data))

    def test_rejects_self_extent_and_extension_mismatch(self):
        for offset, format_, value in ((28, "<I", 16), (10, "<H", 4)):
            data = bytearray(fixture())
            struct.pack_into(format_, data, offset, value)
            with self.assertRaises(InventoryError):
                parse_romdir(bytes(data))


if __name__ == "__main__":
    unittest.main()
