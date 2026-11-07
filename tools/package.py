#!/usr/bin/env python3
"""Assemble a player-facing folder from a build: the launcher, the five
engines and what they load at run time. No game data is included.

usage: package.py <build-dir> <out-dir>
Linux builds copy libps2_runtime.so (the launcher puts its own folder on
LD_LIBRARY_PATH); Windows builds copy the .exe files and the DLLs next to them.
"""
import shutil, sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
ENGINES = ["ga2_zettai", "ga2_mugen_1", "ga2_mugen_2", "ga2_eigou_1", "ga2_eigou_2"]


def main():
    build, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    bin_dir = build / "ga2" / "bin"
    windows = (bin_dir / "ga2.exe").exists()
    suffix = ".exe" if windows else ""
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    missing = [name for name in ["ga2"] + ENGINES if not (bin_dir / (name + suffix)).exists()]
    if missing:
        raise SystemExit(f"missing from the build: {', '.join(missing)}")
    for name in ["ga2"] + ENGINES:
        shutil.copy2(bin_dir / (name + suffix), out / (name + suffix))
    if windows:
        for dll in sorted(bin_dir.glob("*.dll")):
            shutil.copy2(dll, out / dll.name)
    else:
        shutil.copy2(build / "ps2xRuntime" / "libps2_runtime.so", out / "libps2_runtime.so")
    shutil.copytree(SOURCE / "native" / "shaders", out / "shaders")
    shutil.copytree(SOURCE / "native" / "buttons", out / "buttons")
    shutil.copytree(SOURCE / "native" / "fonts", out / "fonts")  # the UI font and its licence
    (out / "tools").mkdir()
    shutil.copy2(SOURCE / "tools" / "assets" / "extract_skybox.py", out / "tools" / "extract_skybox.py")
    shutil.copy2(SOURCE / "docs" / "PLAYING.md", out / "README.md")
    shutil.copy2(SOURCE / "docs" / "LANGUAGE_PACKS.md", out / "LANGUAGE_PACKS.md")
    shutil.copy2(SOURCE / "LICENSE", out / "LICENSE.md")
    print(f"packaged {'Windows' if windows else 'Linux'} build in {out}")


if __name__ == "__main__":
    main()
