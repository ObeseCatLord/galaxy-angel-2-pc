#!/usr/bin/env python3
"""Puts the installer into a release's archives (as <archive folder>/ga2-installer[.exe], so every install has its
updater) and next to them as the standalone downloads (galaxy-angel-2-pc-installer-<platform>[.exe]).

usage: add_installer.py <assets-dir> <installers-dir>
<installers-dir> holds galaxy-angel-2-pc-installer-<platform>[.exe] (tools/ci/build-installer.sh). An archive that
already has an installer gets the new one instead. tar.xz archives are recompressed with xz (multithreaded)."""
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

PLATFORMS = {'windows-x86_64': '.zip', 'linux-x86_64': '.tar.xz', 'linux-arm64': '.tar.xz'}


def add_to_tar(archive, installer, folder):
    member = f'{folder}/ga2-installer'
    with tempfile.TemporaryDirectory(dir=archive.parent) as temp:
        plain = Path(temp) / 'archive.tar'
        with tarfile.open(archive, 'r:xz') as source, tarfile.open(plain, 'w', format=tarfile.GNU_FORMAT) as target:
            for info in source:
                if info.name == member:
                    continue
                target.addfile(info, source.extractfile(info) if info.isfile() else None)
            info = target.gettarinfo(str(installer), member)
            info.mode, info.uid, info.gid, info.uname, info.gname = 0o755, 0, 0, 'root', 'root'
            with open(installer, 'rb') as data:
                target.addfile(info, data)
        subprocess.run(['xz', '-T0', '-6', '-f', str(plain)], check=True)
        shutil.move(str(plain) + '.xz', archive)


def add_to_zip(archive, installer, folder):
    member = f'{folder}/ga2-installer.exe'
    with tempfile.TemporaryDirectory(dir=archive.parent) as temp:
        rebuilt = Path(temp) / 'archive.zip'
        with zipfile.ZipFile(archive) as source, zipfile.ZipFile(rebuilt, 'w', zipfile.ZIP_DEFLATED) as target:
            for info in source.infolist():
                if info.filename != member:
                    target.writestr(info, source.read(info), compress_type=info.compress_type)
            target.write(installer, member)
        shutil.move(str(rebuilt), archive)


def main():
    assets, installers = Path(sys.argv[1]), Path(sys.argv[2])
    done = 0
    for platform, suffix in PLATFORMS.items():
        installer = installers / f'galaxy-angel-2-pc-installer-{platform}{".exe" if platform.startswith("windows") else ""}'
        if not installer.exists():
            print(f'{platform}: no installer build')
            continue
        shutil.copy2(installer, assets / installer.name)
        for archive in sorted(assets.glob(f'galaxy-angel-2-pc-v*-{platform}{suffix}')):
            folder = archive.name[:-len(suffix)]
            (add_to_zip if suffix == '.zip' else add_to_tar)(archive, installer, folder)
            print(f'{archive.name}: added {folder}/ga2-installer{".exe" if suffix == ".zip" else ""}')
            done += 1
    if done == 0:
        raise SystemExit('no archives got an installer')


if __name__ == '__main__':
    main()
