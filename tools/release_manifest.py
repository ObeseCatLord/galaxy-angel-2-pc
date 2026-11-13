#!/usr/bin/env python3
"""Writes ga2-manifest.json, the file the installer (native/installer) reads from the newest release: the version,
date and notes (the "## New in" section of --notes, else the version's CHANGELOG.md section) and, per platform, the archive's and the installer's download
address, size and SHA-256.

usage: release_manifest.py <assets-dir> --tag v0.10.0 [--repo owner/name] [--date YYYY-MM-DD] [--base-url URL]
                           [--notes release-notes.md] [--out <assets-dir>/ga2-manifest.json]
The assets are the release's files: galaxy-angel-2-pc-<tag>-<platform>.(tar.xz|zip) and
galaxy-angel-2-pc-installer-<platform>[.exe]. --base-url defaults to the tag's GitHub download address; a local
folder (for testing the installer) works too."""
import argparse
import datetime
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLATFORMS = ['windows-x86_64', 'linux-x86_64', 'linux-arm64']


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def changelog_notes(version):
    """The version's section of CHANGELOG.md, with each bullet on one line (the file wraps them)."""
    text = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
    match = re.search(r'^## ' + re.escape(version) + r'\s*$(.*?)(?=^## |\Z)', text, re.M | re.S)
    if not match:
        return ''
    lines = []
    for line in match.group(1).strip('\n').splitlines():
        if line.startswith('  ') and lines and lines[-1].lstrip().startswith('- ') and not line.lstrip().startswith('- '):
            lines[-1] += ' ' + line.strip()
        else:
            lines.append(line.rstrip())
    return '\n'.join(lines).strip()


def release_notes(path):
    """The "## New in ..." section of the release's notes (Markdown, as on the release page)."""
    text = Path(path).read_text(encoding='utf-8')
    match = re.search(r'^## New in [^\n]*$(.*?)(?=^## |\Z)', text, re.M | re.S)
    return match.group(1).strip() if match else ''


def entry(path, base):
    return {'file': path.name, 'url': base.rstrip('/') + '/' + path.name, 'size': path.stat().st_size, 'sha256': sha256(path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('assets', type=Path)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--repo', default='ObeseCatLord/galaxy-angel-2-pc')
    parser.add_argument('--date', default=datetime.date.today().isoformat())
    parser.add_argument('--base-url')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--notes', type=Path, help='release notes (Markdown); their "## New in" section is used')
    args = parser.parse_args()
    version = args.tag[1:] if args.tag.startswith('v') else args.tag
    base = args.base_url or f'https://github.com/{args.repo}/releases/download/{args.tag}'
    manifest = {
        'schemaVersion': 1,
        'name': 'Galaxy Angel II PC',
        'version': version,
        'tag': args.tag,
        'date': args.date,
        'page': f'https://github.com/{args.repo}/releases/tag/{args.tag}',
        'notes': (release_notes(args.notes) if args.notes else '') or changelog_notes(version),
        'packages': {},
        'installer': {'version': (ROOT / 'native/installer/VERSION').read_text().strip(), 'files': {}},
    }
    for platform in PLATFORMS:
        for suffix in ('.tar.xz', '.zip'):
            package = args.assets / f'galaxy-angel-2-pc-{args.tag}-{platform}{suffix}'
            if package.exists():
                manifest['packages'][platform] = entry(package, base)
        installer = args.assets / f'galaxy-angel-2-pc-installer-{platform}{".exe" if platform.startswith("windows") else ""}'
        if installer.exists():
            manifest['installer']['files'][platform] = entry(installer, base)
    if not manifest['packages']:
        raise SystemExit(f'no galaxy-angel-2-pc-{args.tag}-* archives in {args.assets}')
    out = args.out or args.assets / 'ga2-manifest.json'
    out.write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'{out}: {version}, packages {sorted(manifest["packages"])}, installers {sorted(manifest["installer"]["files"])}')


if __name__ == '__main__':
    main()
