import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("patch_stack", Path(__file__).parents[1] / "tools/apply_patch_stack.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PatchStackTests(unittest.TestCase):
    def fixture(self, directory, content):
        root = Path(directory) / "upstream"
        root.mkdir()
        file = root / "file.txt"
        file.write_text(content)
        first = Path(directory) / "first.patch"
        second = Path(directory) / "second.patch"
        first.write_text("--- a/file.txt\n+++ b/file.txt\n@@ -1,2 +1,2 @@\n-alpha\n+ONE\n beta\n")
        second.write_text("--- a/file.txt\n+++ b/file.txt\n@@ -1,2 +1,2 @@\n ONE\n-beta\n+TWO\n")
        return root, file, [first, second]

    def test_baseline(self):
        with tempfile.TemporaryDirectory() as d:
            root, file, patches = self.fixture(d, "alpha\nbeta\n")
            self.assertEqual(module.ensure_patch_stack(root, patches), 0)
            self.assertEqual(file.read_text(), "ONE\nTWO\n")

    def test_partial_prefix(self):
        with tempfile.TemporaryDirectory() as d:
            root, file, patches = self.fixture(d, "ONE\nbeta\n")
            self.assertEqual(module.ensure_patch_stack(root, patches), 1)
            self.assertEqual(file.read_text(), "ONE\nTWO\n")

    def test_overlapping_applied_stack_does_not_rewrite(self):
        with tempfile.TemporaryDirectory() as d:
            root, file, patches = self.fixture(d, "ONE\nTWO\n")
            modified = file.stat().st_mtime_ns
            self.assertEqual(module.ensure_patch_stack(root, patches), 2)
            self.assertEqual(file.stat().st_mtime_ns, modified)

    def test_unrelated_edit_is_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            root, file, patches = self.fixture(d, "ONE\nbeta\n")
            unrelated = root / "user-file.txt"
            unrelated.write_text("user's existing work\n")
            self.assertEqual(module.ensure_patch_stack(root, patches), 1)
            self.assertEqual(file.read_text(), "ONE\nTWO\n")
            self.assertEqual(unrelated.read_text(), "user's existing work\n")

    def test_conflict_is_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            root, file, patches = self.fixture(d, "user edit\nbeta\n")
            with self.assertRaises(ValueError):
                module.ensure_patch_stack(root, patches)
            self.assertEqual(file.read_text(), "user edit\nbeta\n")
