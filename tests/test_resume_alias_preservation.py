"""Guard/idempotence tests for the actual native alias-preservation helper.

The host-table fixture never executes a guest function. Native pointer and
guest-return evidence remain separate required checks.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class ResumeAliasPreservationTests(unittest.TestCase):
    def test_actual_helper_preflights_both_ranges_and_is_idempotent(self):
        root = Path(__file__).parents[1]
        compiler = shutil.which("clang++")
        if compiler is None:
            self.skipTest("clang++ is required by the native build setup")
        source = (root / "native/compat/zettai_sif.cpp").read_text()
        start = source.index("    void preserveZettaiResumeOwners")
        end = source.index("    void bindZettaiSif", start)
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / "production_resume_helper.inc").write_text(source[start:end])
            fixture = root / "tests/fixtures/resume_alias_table.cpp"
            binary = work / "resume_alias_fixture"
            subprocess.run([compiler, "-std=c++20", "-O0", "-I", str(work),
                            str(fixture), "-o", str(binary)], check=True,
                           capture_output=True, text=True)
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
            self.assertIn("6 host-table fixture scenarios pass", result.stdout)


if __name__ == "__main__":
    unittest.main()
