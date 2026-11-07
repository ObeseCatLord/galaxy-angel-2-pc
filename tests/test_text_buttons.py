import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "modding"))

import ga2font  # noqa: E402
import script_xlsx as sx  # noqa: E402
import text_buttons as tb  # noqa: E402


class TextButtons(unittest.TestCase):
    def test_codes_are_valid_shift_jis_and_in_order(self):
        used = [ch for name, _ in tb.BUTTONS for ch in tb.CODES[name]]
        self.assertEqual(len(used), len(set(used)))
        for i, ch in enumerate(used):
            self.assertEqual(ch.encode("cp932"), bytes([0xF0, 0x40 + i]))
        self.assertLess(0x40 + len(used), 0x5C)   # never reaches the backslash trail byte

    def test_expand_case_insensitive_and_pills_are_two_cells(self):
        self.assertEqual(tb.expand("A {Circle} b"), "A " + tb.CODES["circle"] + " b")
        self.assertEqual(len(tb.expand("{l1}")), 2)
        self.assertEqual(len(tb.expand("{cross}")), 1)
        self.assertEqual(tb.contract(tb.expand("x {l1}{start}{dpad}")), "x {l1}{start}{dpad}")

    def test_unknown_placeholder_is_an_error(self):
        with self.assertRaises(ValueError):
            tb.expand("press {crcle}")
        with self.assertRaises(sx.ga2asb.AsbError):
            sx.english_to_gasm("press {crcle}")

    def test_icon_counts_as_full_width_in_the_line_fit(self):
        metrics = {"glyphs": {chr(c): [0, 0, 6] for c in range(0x21, 0x7F)}, "space_advance": 10}
        text = sx.expand_buttons("{l1}")
        self.assertEqual(ga2font.text_width(text, 18, metrics), 36.0)     # two cells of 18
        letters = ga2font.text_width("{l1}", 18, metrics)                 # what the letters would cost
        self.assertNotEqual(letters, 36.0)

    def test_gasm_round_trip_keeps_the_reserved_characters(self):
        body = sx.english_to_gasm("Press {circle}!")
        self.assertEqual(sx.ga2asb.text_to_sjis(body), b"Press \xf0\x40!")


if __name__ == "__main__":
    unittest.main()
