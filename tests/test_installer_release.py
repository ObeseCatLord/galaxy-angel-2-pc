"""The installer's release side: tools/release_manifest.py (ga2-manifest.json) and tools/ci/add_installer.py (the
installer inside the archives), with small synthetic archives."""
import hashlib
import importlib.util
import io
import json
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


release_manifest = load("release_manifest", "tools/release_manifest.py")
add_installer = load("add_installer", "tools/ci/add_installer.py")


def make_tar_xz(path, folder, files):
    with tarfile.open(path, "w:xz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(f"{folder}/{name}")
            info.size = len(data)
            info.mode = 0o755 if name == "ga2" else 0o644
            tar.addfile(info, io.BytesIO(data))


def make_zip(path, folder, files):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(f"{folder}/{name}", data)


class ReleaseManifestTest(unittest.TestCase):
    def test_notes_prefer_the_release_section(self):
        with tempfile.TemporaryDirectory() as temp:
            notes = Path(temp) / "notes.md"
            notes.write_text("Intro\n\n## Downloads\n\n| a |\n\n## New in 9.9.9\n\n- **One** thing\n- Two\n\n## Tested\n\nyes\n")
            self.assertEqual(release_manifest.release_notes(notes), "- **One** thing\n- Two")

    def test_changelog_bullets_are_unwrapped(self):
        notes = release_manifest.changelog_notes("0.10.0")
        self.assertTrue(notes.startswith("- "))
        for line in notes.splitlines():
            self.assertFalse(line.startswith("  ") and not line.lstrip().startswith("- "), line)

    def test_manifest_entries(self):
        with tempfile.TemporaryDirectory() as temp:
            assets = Path(temp)
            make_tar_xz(assets / "galaxy-angel-2-pc-v1.2.3-linux-x86_64.tar.xz", "galaxy-angel-2-pc-v1.2.3-linux-x86_64", {"ga2": b"x"})
            make_zip(assets / "galaxy-angel-2-pc-v1.2.3-windows-x86_64.zip", "galaxy-angel-2-pc-v1.2.3-windows-x86_64", {"ga2.exe": b"y"})
            (assets / "galaxy-angel-2-pc-installer-linux-x86_64").write_bytes(b"installer")
            sys.argv = ["release_manifest.py", str(assets), "--tag", "v1.2.3", "--repo", "owner/name", "--date", "2026-01-02"]
            release_manifest.main()
            manifest = json.loads((assets / "ga2-manifest.json").read_text())
        self.assertEqual(manifest["schemaVersion"], 1)
        self.assertEqual(manifest["version"], "1.2.3")
        self.assertEqual(sorted(manifest["packages"]), ["linux-x86_64", "windows-x86_64"])
        linux = manifest["packages"]["linux-x86_64"]
        self.assertEqual(linux["url"], "https://github.com/owner/name/releases/download/v1.2.3/galaxy-angel-2-pc-v1.2.3-linux-x86_64.tar.xz")
        self.assertEqual(len(linux["sha256"]), 64)
        installer = manifest["installer"]["files"]["linux-x86_64"]
        self.assertEqual(installer["sha256"], hashlib.sha256(b"installer").hexdigest())
        self.assertEqual(installer["size"], 9)
        self.assertEqual(manifest["installer"]["version"], (ROOT / "native/installer/VERSION").read_text().strip())


class AddInstallerTest(unittest.TestCase):
    def test_adds_and_replaces(self):
        with tempfile.TemporaryDirectory() as temp:
            assets, builds = Path(temp) / "assets", Path(temp) / "builds"
            assets.mkdir()
            builds.mkdir()
            tar_folder, zip_folder = "galaxy-angel-2-pc-v1.0.0-linux-x86_64", "galaxy-angel-2-pc-v1.0.0-windows-x86_64"
            make_tar_xz(assets / f"{tar_folder}.tar.xz", tar_folder, {"ga2": b"launcher", "README.md": b"r"})
            make_zip(assets / f"{zip_folder}.zip", zip_folder, {"ga2.exe": b"launcher", "README.md": b"r"})
            for content in (b"first", b"second"):  # a second run replaces the installer
                (builds / "galaxy-angel-2-pc-installer-linux-x86_64").write_bytes(content + b"-linux")
                (builds / "galaxy-angel-2-pc-installer-windows-x86_64.exe").write_bytes(content + b"-windows")
                sys.argv = ["add_installer.py", str(assets), str(builds)]
                add_installer.main()
            with tarfile.open(assets / f"{tar_folder}.tar.xz") as tar:
                names = tar.getnames()
                self.assertEqual(sorted(names), sorted([f"{tar_folder}/ga2", f"{tar_folder}/README.md", f"{tar_folder}/ga2-installer"]))
                member = tar.getmember(f"{tar_folder}/ga2-installer")
                self.assertEqual(member.mode, 0o755)
                self.assertEqual(tar.extractfile(member).read(), b"second-linux")
                self.assertEqual(tar.extractfile(f"{tar_folder}/ga2").read(), b"launcher")
            with zipfile.ZipFile(assets / f"{zip_folder}.zip") as z:
                self.assertEqual(sorted(z.namelist()), sorted([f"{zip_folder}/ga2.exe", f"{zip_folder}/README.md", f"{zip_folder}/ga2-installer.exe"]))
                self.assertEqual(z.read(f"{zip_folder}/ga2-installer.exe"), b"second-windows")
            self.assertEqual((assets / "galaxy-angel-2-pc-installer-linux-x86_64").read_bytes(), b"second-linux")


if __name__ == "__main__":
    unittest.main()
