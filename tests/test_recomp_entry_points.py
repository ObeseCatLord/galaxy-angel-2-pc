import hashlib
import json
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
import ga2


class EntryPointGenerationTests(unittest.TestCase):
    def fixture(self, root, disc="zettai", existing=""):
        (root / "configs").mkdir()
        private = root / "private"
        boot = private / "boot.elf"
        boot.parent.mkdir()
        boot.write_bytes(b"synthetic executable identity")
        digest = hashlib.sha256(boot.read_bytes()).hexdigest()
        (root / "configs/recomp-entry-points.json").write_text(json.dumps({
            "schema_version": 1, "builds": [{"disc_id": "zettai",
                "elf_sha256": digest, "entry_points": ["0x00222608"]}]}))
        (root / "configs/sdk-bindings.json").write_text(json.dumps({
            "schema_version": 1, "disc_id": "zettai", "elf_sha256": digest,
            "bindings": []}))
        (private / "manifests").mkdir()
        (private / "manifests" / f"{disc}.json").write_text(json.dumps({"boot": {
            "extracted_path": "boot.elf", "sha256": digest}}))
        analysis = private / "analysis" / disc
        analysis.mkdir(parents=True)
        (analysis / "identity.json").write_text(json.dumps({
            "elf_sha256": digest, "ps2recomp_commit": ga2.PIN}))
        (analysis / "ghidra.toml").write_text(
            '[general]\ninput = "old"\noutput = "old"\nghidra_output = "old"\n'
            'patch_syscalls = true\npatch_cop0 = true\npatch_cache = true\n' + existing)
        (analysis / "functions.csv").write_text("synthetic map\n")
        return private, analysis, boot

    def generator(self, private, disc, observed):
        def run(command, log):
            observed.append(tomllib.loads(Path(command[1]).read_text())["general"])
            output = private / "generated" / disc
            output.mkdir(parents=True)
            (output / "register_functions.cpp").write_text("synthetic output\n")
        return run

    def test_existing_multiline_hints_survive_exact_build_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private, analysis, _ = self.fixture(root, existing=(
                'entry_points = [\n "0x00100008",\n "0x00222608",\n]\n'))
            observed = []
            with patch.object(ga2, "ROOT", root), patch.object(ga2, "run",
                    self.generator(private, "zettai", observed)):
                ga2.generate(private, "zettai")
            self.assertEqual(observed[0]["entry_points"], ["0x00100008", "0x00222608"])
            self.assertFalse(observed[0]["patch_syscalls"])
            identity = json.loads((private / "generated/zettai/identity.json").read_text())
            self.assertEqual(identity["config_sha256"], hashlib.sha256(
                (analysis / "recomp.toml").read_bytes()).hexdigest())

    def test_another_disc_does_not_inherit_zettai_address(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private, _, _ = self.fixture(root, disc="mugen-1")
            observed = []
            with patch.object(ga2, "ROOT", root), patch.object(ga2, "run",
                    self.generator(private, "mugen-1", observed)):
                ga2.generate(private, "mugen-1")
            self.assertNotIn("entry_points", observed[0])

    def test_different_executable_is_rejected_before_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private, analysis, boot = self.fixture(root)
            boot.write_bytes(b"a different executable with its own valid analysis")
            digest = hashlib.sha256(boot.read_bytes()).hexdigest()
            manifest = private / "manifests/zettai.json"
            data = json.loads(manifest.read_text())
            data["boot"]["sha256"] = digest
            manifest.write_text(json.dumps(data))
            (analysis / "identity.json").write_text(json.dumps({
                "elf_sha256": digest, "ps2recomp_commit": ga2.PIN}))
            with patch.object(ga2, "ROOT", root), patch.object(ga2, "run") as run:
                with self.assertRaisesRegex(ValueError, "exact executable"):
                    ga2.generate(private, "zettai")
            run.assert_not_called()
            self.assertFalse((analysis / "recomp.toml").exists())

    def test_sdk_stubs_merge_without_replacing_existing_handlers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private, _, _ = self.fixture(root, existing='stubs = [\n "SetGsCrt@0x0026F320",\n]\n')
            profile_path = root / "configs/sdk-bindings.json"
            profile = json.loads(profile_path.read_text())
            profile["bindings"] = [
                {"name": "scePadSetActAlign", "address": "0024b050", "generation_strategy": "runtime-stub"},
                {"name": "scePadRead", "address": "0024a8f8", "generation_strategy": "runtime-stub"},
                {"name": "sceSifInitRpc", "address": "00272aa8", "generation_strategy": "runtime-alias"}]
            profile_path.write_text(json.dumps(profile))
            observed = []
            with patch.object(ga2, "ROOT", root), patch.object(ga2, "run",
                    self.generator(private, "zettai", observed)):
                ga2.generate(private, "zettai")
            self.assertEqual(set(observed[0]["stubs"]), {
                "SetGsCrt@0x0026F320", "scePadSetActAlign@0x0024B050", "scePadRead@0x0024A8F8"})
            self.assertEqual(observed[0]["entry_points"], ["0x00222608"])

    def test_sdk_profile_mismatch_is_rejected_before_invoking_generator(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private, analysis, _ = self.fixture(root)
            profile_path = root / "configs/sdk-bindings.json"
            profile = json.loads(profile_path.read_text())
            profile["elf_sha256"] = "0" * 64
            profile_path.write_text(json.dumps(profile))
            with patch.object(ga2, "ROOT", root), patch.object(ga2, "run") as run:
                with self.assertRaisesRegex(ValueError, "SDK binding profile requires its exact executable"):
                    ga2.generate(private, "zettai")
            run.assert_not_called()
            self.assertFalse((analysis / "recomp.toml").exists())

    def test_other_disc_does_not_inherit_sdk_stub_addresses(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private, _, _ = self.fixture(root, disc="eigou-1")
            profile_path = root / "configs/sdk-bindings.json"
            profile = json.loads(profile_path.read_text())
            profile["bindings"] = [{"name": "scePadRead", "address": "0024a8f8",
                                    "generation_strategy": "runtime-stub"}]
            profile_path.write_text(json.dumps(profile))
            observed = []
            with patch.object(ga2, "ROOT", root), patch.object(ga2, "run",
                    self.generator(private, "eigou-1", observed)):
                ga2.generate(private, "eigou-1")
            self.assertNotIn("stubs", observed[0])
