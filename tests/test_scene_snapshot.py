"""Diagnostic read bounds and failure reporting, without game data."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
from scene_snapshot import physical_range, scene_snapshot


class SceneSnapshotTests(unittest.TestCase):
    def test_ram_aliases_and_boundaries(self):
        for alias in (0, 0x80000000, 0xA0000000):
            self.assertEqual(physical_range(alias + 0x2000000, 0), 0x2000000)
            self.assertEqual(physical_range(alias + 0x1FFFFFC, 1), 0x1FFFFFC)
            with self.assertRaises(ValueError):
                physical_range(alias + 0x1FFFFFC, 2)
        for address, count in ((0x10000000, 1), (3, 1), (4, 129), (4, -1)):
            with self.assertRaises(ValueError):
                physical_range(address, count)

    def test_bad_collection_shape_never_reads_entries(self):
        memory = {0x2B6640: 0x100, 0x100: 0x200,
                  0x218: 0x300, 0x21C: 0x301}
        reads = []
        def reader(address, count):
            reads.append((address, count))
            return [memory.get(address + index * 4, 0) for index in range(count)]
        result = scene_snapshot(reader)
        self.assertIn("entries_unsampled", result["collection"])
        self.assertFalse(any(address == 0x300 for address, _ in reads))

    def test_short_and_failed_reads_are_explicit(self):
        short = scene_snapshot(lambda address, count: [])
        self.assertEqual({row["field"] for row in short["errors"]},
                         {"world", "collection", "pad_fsm", "mc_reply"})
        def failed(address, count):
            raise OSError("read failed")
        result = scene_snapshot(failed)
        self.assertEqual(len(result["errors"]), 4)
        self.assertEqual(result["world"], {})
        self.assertFalse(result["complete"])
        self.assertEqual(result["completed_words"], 0)

    def test_equal_invalid_collection_pointers_are_not_valid_empty(self):
        memory = {0x2B6640: 0x100, 0x100: 0x200,
                  0x218: 0x10000000, 0x21C: 0x10000000}
        result = scene_snapshot(lambda address, count: [
            memory.get(address + index * 4, 0) for index in range(count)])
        self.assertFalse(result["complete"])
        self.assertEqual(result["collection"]["object"], 0x200)
        self.assertEqual(result["collection"]["distance32"], 0)
        self.assertNotIn("entries", result["collection"])

    def test_partial_collection_header_survives_failed_descriptor_read(self):
        memory = {0x2B6640: 0x100, 0x100: 0x200, 0x234: 0x10000000}
        result = scene_snapshot(lambda address, count: [
            memory.get(address + index * 4, 0) for index in range(count)])
        self.assertFalse(result["complete"])
        self.assertEqual(result["collection"]["object"], 0x200)
        self.assertEqual(result["collection"]["words"][13], 0x10000000)

    def test_worst_case_children_cannot_exceed_total_budget(self):
        memory = {0x2B6640: 0x100, 0x100: 0x200,
                  0x218: 0x300, 0x21C: 0x340, 0x404: 0x2D4398}
        for index in range(8):
            memory[0x300 + index * 8] = 0x400
            memory[0x304 + index * 8] = index
        for offset in range(0x10, 0x28, 4):
            memory[0x400 + offset] = 0x500
        total = 0
        def reader(address, count):
            nonlocal total
            total += count
            self.assertLessEqual(count, 128)
            self.assertLessEqual(total, 1024)
            return [memory.get(address + index * 4, 0) for index in range(count)]
        result = scene_snapshot(reader)
        children = result["collection"]["children_first_eight"]
        failures = [row.get("error", "") for row in children]
        failures += [row.get("error", "") for child in children
                     for row in child.get("handles_10_24", [])]
        self.assertTrue(any("budget exhausted" in error for error in failures))
        self.assertLessEqual(result["requested_words"], 1024)
        self.assertFalse(result["complete"])


if __name__ == "__main__":
    unittest.main()
