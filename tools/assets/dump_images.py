#!/usr/bin/env python3
"""Dump the 2D pictures of the Galaxy Angel II trilogy to PNG.

Reads the player's disc images under ``<private>/discs/<disc>/*.iso`` and
writes, per game (``zettai``, ``mugen`` = Mugen discs 1+2, ``eigou`` = Eigou
discs 1+2), to ``<private>/research/assets-dump/<game>/``:

* ``cg/``                  event pictures (``adv/bg/iv*.agi``)
* ``sprites/<character>/`` standing portraits (``adv/face/cha*.fac``): the
  body layer as ``<file>.png`` (it already shows the expression with open
  eyes and closed mouth), the blink and lip-sync frames in ``parts/``, the
  small message-window faces (``fac*.agi``) in ``faces/`` and a
  ``sprites.json`` with the layer offsets.  Characters come from the disc's ``adv/charNNN.tbl`` (``NAME00``),
  ``names.json`` maps folder names to the Japanese names.
* ``backgrounds/``         ADV backgrounds (``adv/bg/ad_bk_*``)
* ``eyecatch/``, ``other/<group>/`` eyecatches, thumbnails, place names, UI
  (``common/``), and with ``--all`` every other AGI texture on the disc
* ``index.csv``, ``contact-cg.png``, ``contact-sprites.png``

Identical images (same pixels) are written once per folder; ``index.csv``
lists every archive file with the output it maps to.  Only the standard
library and Pillow are needed.  ``docs/assets/images.md`` describes the
formats.

    python3 tools/assets/dump_images.py <private> [--game zettai|mugen|eigou]
            [--all] [--jobs N] [--no-contact]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import multiprocessing as mp
import os
import re
import shutil
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract_skybox as sky  # noqa: E402  (Disc, PIDX and FSTS readers)

from PIL import Image, ImageDraw  # noqa: E402

GAMES = {
    'zettai': ['zettai'],
    'mugen': ['mugen-1', 'mugen-2'],
    'eigou': ['eigou-1', 'eigou-2'],
}
PACK_ARCHIVES = ('ADV.DAT', 'ADVMAT.DAT', 'SLG.DAT')

# Romanised folder names for NAME00 of adv/charNNN.tbl (official spellings
# where known, otherwise a Hepburn reading or an English description).
ROMAJI = {
    'カズヤ': 'Kazuya', 'アプリコット': 'Apricot', 'カルーア': 'Kahlua',
    'テキーラ': 'Tequila', 'ナノナノ': 'NanoNano', 'リリィ': 'Lily',
    'アニス': 'Anise', 'ナツメ': 'Natsume', 'ロゼル': 'Roselle',
    'ベリータ': 'Berita', 'ミルフィーユ': 'Milfeulle', 'ランファ': 'Ranpha',
    'ミント': 'Mint', 'フォルテ': 'Forte', 'ヴァニラ': 'Vanilla',
    'ちとせ': 'Chitose', 'タクト': 'Tact', 'レスター': 'Lester', 'ノア': 'Noa',
    'ソルダム': 'Soldum', 'キャラウェイ': 'Caraway', 'ココ': 'Coco',
    'アルモ': 'Almo', 'モルデン': 'Morden', 'ステリーネ': 'Sterine',
    'コンビニ店員': 'ConvenienceStoreClerk', 'ルコ': 'Ruko',
    'ランティ': 'Ranti', 'ミモレット': 'Mimolette', 'メルバ': 'Melba',
    'クロワ': 'Kurowa', 'コロネ': 'Corone',
    'カレーショップ店員': 'CurryShopClerk', 'ブレンダ': 'Brenda',
    'ケーラ': 'Keira', 'デパート店員Ａ': 'DepartmentStoreClerkA',
    'デパート店員Ｂ': 'DepartmentStoreClerkB', '店員': 'Clerk',
    'クロミエ': 'Kuromie', 'クレータ': 'Kureta',
    '国王お付き魔女Ａ': 'RoyalWitchA', '国王お付き魔女Ｂ': 'RoyalWitchB',
    'エコ': 'Eco', 'タピオ': 'Tapio', 'ビアンカ': 'Bianca',
    'サーファーＡ': 'SurferA', 'サーファーＢ': 'SurferB',
    'サーファーＣ': 'SurferC', '梅': 'Ume', 'ハーコット': 'Harcott',
    'テロリスト': 'Terrorist', 'ケルシー': 'Kelsey',
    'サンタローザ': 'SantaRosa', 'ナギサ': 'Nagisa', '子供': 'Child',
    '女性Ａ': 'WomanA', '女性Ｂ': 'WomanB', '婦人': 'Lady',
    '客Ａ': 'CustomerA', '客Ｂ': 'CustomerB', 'クッキー': 'Cookie',
    'オペレーターＡ': 'OperatorA', 'オペレーターＢ': 'OperatorB',
    'ヒビキ': 'Hibiki', 'シュリ': 'Shuri', '直': 'Nao',
    'ルクシオールクルー': 'LuxiolCrew', 'セキュリティ': 'Security',
    'ペル': 'Pel', '海賊': 'Pirate', 'セルダール使者': 'CeldarEnvoy',
    'ハモン': 'Hamon', 'リベラ': 'Libera', '騎士団長': 'KnightCommander',
    'ロンド': 'Rondo', '近衛隊長': 'GuardCaptain', 'クラム': 'Clam',
    'ガラム': 'Garam', 'アジート行政官': 'AjitoAdministrator',
    'マサラ': 'Masala', 'ケルビン': 'Kelvin', 'ディータ': 'Dita',
    'メノルカ': 'Menorca', 'ヴェレル': 'Verel', 'カルバドゥス': 'Calvadus',
    'デリアナ': 'Deliana', 'アイラ': 'Aira',
    'ベネディクタイン': 'Benedictine', 'ジュニエヴル': 'Genievre',
    '看護師': 'Nurse', 'パルフェ': 'Parfait', 'メイド': 'Maid',
    'ピコ行政官': 'PicoAdministrator', 'マジーク行政官': 'MagiqueAdministrator',
    '鎧Ｂ': 'ArmorB', '鎧Ｃ': 'ArmorC', '鎧Ｄ': 'ArmorD',
    '艦隊司令': 'FleetCommander', 'アライアンス兵士': 'AllianceSoldier',
    'シヴァ': 'Shiva', 'セルダール番兵': 'CeldarSentry',
    'ディータ軍兵士': 'DitaArmySoldier', 'ハチェット住民': 'HatchetResident',
    'ルフト': 'Luft', 'グロッグ': 'Grog', 'シャトヤーン': 'Shatoyan',
    'ソルベ': 'Sorbet', 'ヘレア': 'Helea', 'セレナ': 'Serena',
    '司会者': 'Host', '交': 'Kou', '兵士': 'Soldier', '警備兵': 'Guard',
    'チンピラ': 'Thug', 'ロディアック': 'Rodiak', '幼ナツメ': 'YoungNatsume',
    'ユズ': 'Yuzu', '幽体カズヤ': 'GhostKazuya',
    'おでんカウンター': 'OdenCounter', '銅像': 'Statue', '矢': 'Arrow',
    '緑の月': 'GreenMoon', '４つの月': 'FourMoons',
    '黒カーテン・下手': 'BlackCurtainLeft',
    '黒カーテン・上手': 'BlackCurtainRight', 'リリィとアイラ': 'LilyAndAira',
    '温泉下部': 'HotSpringBottom', '表情確認用': 'ExpressionTest',
    'スタンレイ': 'Stanley', 'エレナ': 'Elena', 'ジュレ': 'Gelee',
    '全員': 'Everyone', '一同': 'Everyone', '野郎共': 'TheGuys',
    '顔なし': 'Faceless', '三人': 'ThreePeople',
    'ナノナノ＆アニス＆ナツメ': 'NanoNanoAniseNatsume', '役者Ａ': 'ActorA',
}

# Sprites whose ID has no adv/charNNN.tbl on that disc, named from the label
# drawn on the image (cha077: a "カトリーヌ / 通常" name-card placeholder) or
# from Zettai's table (cha070 on Mugen disc 2 is Zettai's Dita army soldier).
MANUAL_NAMES = {
    'mugen-1': {77: ['カトリーヌ']},
    'eigou-2': {77: ['カトリーヌ']},
    'mugen-2': {70: ['ディータ軍兵士']},
}
ROMAJI['カトリーヌ'] = 'Catherine'

_XOR = bytes(i ^ 0x72 for i in range(256))
_NIBBLE_SWAP = bytes(((i & 15) << 4) | (i >> 4) for i in range(256))


# ---------------------------------------------------------------------------
# Decompression and image decoding
# ---------------------------------------------------------------------------

def raic(data: bytes) -> bytes:
    """RAIC (LZSS) / RAIB (stored) stream, see extract_skybox.raic_decompress.
    Same algorithm on one buffer whose first 0xFEE + 4096 bytes are the zero
    window, so a back reference is a slice of the output (about 3x faster)."""
    magic = data[:4].translate(_XOR)
    usize = struct.unpack_from('<I', data, 4)[0]
    if magic == b'RAIB':
        return data[8:8 + usize].translate(_XOR)
    if magic != b'RAIC':
        raise ValueError('not a RAIC/RAIB stream')
    src = data[8:].translate(_XOR)
    pre = 0xFEE + 4096
    out = bytearray(pre)
    end = pre + usize
    i, n = 0, len(src)
    while len(out) < end and i < n:
        flags = src[i]
        i += 1
        for _ in range(8):
            if i >= n:
                break
            if flags & 1:
                out.append(src[i])
                i += 1
            else:
                if i + 1 >= n:
                    i = n
                    break
                a, b = src[i], src[i + 1]
                i += 2
                length = (b & 15) + 3
                cur = len(out)
                s = cur - (((cur - (a | ((b & 0xF0) << 4)) - 1) & 0xFFF) + 1)
                if cur - s >= length:
                    out += out[s:s + length]
                else:
                    for k in range(length):
                        out.append(out[s + k])
            flags >>= 1
    if len(out) < end:
        raise ValueError('truncated RAIC stream')
    return bytes(out[pre:end])


def unpack(blob: bytes, usize: int, csize: int) -> bytes:
    if csize and csize != usize:
        return raic(blob[:csize])
    data = blob[:usize]
    if data[:4].translate(_XOR) in (b'RAIC', b'RAIB'):  # stored RAIC stream
        return raic(data)
    return data


def _clut_index(i: int) -> int:
    return (i & 0xE7) | ((i & 0x08) << 1) | ((i & 0x10) >> 1)


def _alpha2(a: int) -> int:
    return min(255, a * 2)


def decode_agi(d: bytes, base: int = 0):
    """AGI texture at d[base:] -> (Pillow RGBA image, format name)."""
    pix = base + struct.unpack_from('<I', d, base + 0x08)[0]
    psm = struct.unpack_from('<H', d, base + 0x0E)[0]
    w, h = struct.unpack_from('<HH', d, base + 0x18)
    if psm == 0x13 or psm == 0x14:
        clut = base + struct.unpack_from('<I', d, base + 0x1C)[0]
        ncol = 256 if psm == 0x13 else 16
        raw = d[clut:clut + 4 * ncol]
        pal = bytearray()
        for i in range(ncol):
            j = 4 * (_clut_index(i) if ncol == 256 else i)
            pal += bytes((raw[j], raw[j + 1], raw[j + 2], _alpha2(raw[j + 3])))
        if psm == 0x13:
            im = Image.frombytes('P', (w, h), d[pix:pix + w * h])
            fmt = 'PSMT8'
        else:
            n = (w * h + 1) // 2
            im = Image.frombytes('P', (w, h), d[pix:pix + n].translate(_NIBBLE_SWAP), 'raw', 'P;4')
            fmt = 'PSMT4'
        im.putpalette(bytes(pal), 'RGBA')
        return im.convert('RGBA'), fmt
    if psm == 0x00:  # PSMCT32
        im = Image.frombytes('RGBA', (w, h), d[pix:pix + 4 * w * h])
        r, g, b, a = im.split()
        return Image.merge('RGBA', (r, g, b, a.point(_alpha2))), 'PSMCT32'
    if psm == 0x02:  # PSMCT16: R5 G5 B5 A1, A=1 -> 0x80 (opaque)
        px = d[pix:pix + 2 * w * h]
        out = bytearray(4 * w * h)
        lut5 = [(v << 3) | (v >> 2) for v in range(32)]
        for k, (v,) in enumerate(struct.iter_unpack('<H', px)):
            o = 4 * k
            out[o] = lut5[v & 31]
            out[o + 1] = lut5[(v >> 5) & 31]
            out[o + 2] = lut5[(v >> 10) & 31]
            out[o + 3] = 255 if v & 0x8000 else 0
        im = Image.frombytes('RGBA', (w, h), bytes(out))
        if not any(im.getchannel('A').getextrema()):  # no alpha bit set: opaque
            im.putalpha(255)
        return im, 'PSMCT16'
    raise ValueError(f'unsupported PSM {psm:#x}')


def parse_fac(d: bytes):
    """FACE standing portrait -> (eye frames, mouth frames, layers).  Each
    layer is (kind 0 body / 1 eye / 2 mouth, x, y, agi offset)."""
    if d[:4] != b'FACE':
        raise ValueError('not a FACE file')
    if struct.unpack_from('<H', d, 4)[0] == 0x20:
        # Chunked: header 0x20 bytes (0x0C eyes, mouths), then chunks of
        # tag + u32 total size: WIND, POIN, IMAG {kind, x, y, 0x20, size, 0}.
        eyes, mouths = struct.unpack_from('<HH', d, 0x0C)
        layers, o = [], 0x20
        while o + 8 <= len(d):
            tag, size = d[o:o + 4], struct.unpack_from('<I', d, o + 4)[0]
            if size < 8:
                break
            if tag == b'IMAG':
                kind, x, y, hdr = struct.unpack_from('<Iiii', d, o + 8)
                layers.append((kind, x, y, o + hdr))
            o += size
        return eyes, mouths, layers
    # Older table form (3-digit names): u16 eyes, u16 mouths, u32 anchor x/y,
    # then 1 + eyes + mouths entries {offset, size, x, y}.
    eyes, mouths = struct.unpack_from('<HH', d, 4)
    layers = []
    for k in range(1 + eyes + mouths):
        off, _, x, y = struct.unpack_from('<IIii', d, 0x10 + 16 * k)
        layers.append((0 if k == 0 else 1 if k <= eyes else 2, x, y, off))
    return eyes, mouths, layers


def pixel_hash(im: Image.Image) -> str:
    h = hashlib.sha256(b'%dx%d:' % im.size)
    h.update(im.tobytes())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Worker: decode one archive file, write content-addressed PNGs to staging
# ---------------------------------------------------------------------------

_DISCS: dict[str, sky.Disc] = {}
_STAGE: Path | None = None


def _init(stage: str):
    global _STAGE
    _STAGE = Path(stage)


def _disc(iso: str) -> sky.Disc:
    if iso not in _DISCS:
        _DISCS[iso] = sky.Disc(Path(iso))
    return _DISCS[iso]


def _stage(im: Image.Image) -> str:
    sha = pixel_hash(im)
    dst = _STAGE / f'{sha}.png'
    if not dst.exists():
        tmp = _STAGE / f'{sha}.{os.getpid()}.tmp'
        im.save(tmp, 'PNG', optimize=False, compress_level=6)
        os.replace(tmp, dst)
    return sha


def decode_task(task):
    iso, dat, off, usize, csize, path = task
    try:
        disc = _disc(iso)
        blob = disc.read(dat, off, csize if csize and csize != usize else usize)
        data = unpack(blob, usize, csize)
        if path.endswith('.fac'):
            eyes, mouths, layers = parse_fac(data)
            recs = []
            for kind, x, y, agi in layers:
                im, fmt = decode_agi(data, agi)
                recs.append({'kind': ('body', 'eye', 'mouth')[min(kind, 2)], 'x': x, 'y': y,
                             'w': im.width, 'h': im.height, 'fmt': fmt, 'sha': _stage(im)})
            return path, {'eyes': eyes, 'mouths': mouths, 'layers': recs}
        im, fmt = decode_agi(data)
        return path, {'layers': [{'kind': 'image', 'x': 0, 'y': 0, 'w': im.width, 'h': im.height,
                                  'fmt': fmt, 'sha': _stage(im)}]}
    except Exception as e:  # reported in index.csv
        return path, {'error': f'{type(e).__name__}: {e}'}


# ---------------------------------------------------------------------------
# Disc scanning, tables and classification
# ---------------------------------------------------------------------------

def disc_sources(disc: sky.Disc):
    """(archive, absolute offset, size, packed size, path) for every file of
    ALL.DAT and of the FSTS packs."""
    for path, (off, us, cs) in sky.read_all_dat(disc).items():
        yield 'ALL.DAT', off, us, cs, path, 'ALL.DAT'
    for dat in PACK_ARCHIVES:
        for pk in sky.list_packs(disc, dat):
            for path, (off, us, cs) in pk.entries.items():
                yield dat, pk.offset + off, us, cs, path, f'{dat}:{pk.name}'


def _tbl_text(disc, tree, path):
    if path not in tree:
        return ''
    return sky.all_dat_get(disc, tree, path).decode('cp932', 'replace')


ENTRY_RE = re.compile(r'^#\s*\d+\s*=\s*([\w.\-]+)\s*(?:;|//)\s*(.*?)\s*$', re.M)


def disc_tables(disc: sky.Disc):
    """Character names and labels from the disc's ADV tables."""
    tree = sky.read_all_dat(disc)
    chars, labels = {}, {}
    for path in sorted(tree):
        m = re.fullmatch(r'adv/char(\d+)\.tbl', path)
        if not m:
            continue
        text = _tbl_text(disc, tree, path)
        names = [re.split(r'\s*//', n)[0].strip() for n in re.findall(r'^NAME\d+\s*=\s*([^;\r\n]*)', text, re.M)]
        chars[int(m.group(1))] = [n for n in names if n]
        for fn, label in ENTRY_RE.findall(text):
            labels.setdefault(fn.lower(), label)
    for path in ('adv/bg.tbl', 'adv/evcg.tbl'):
        for fn, label in ENTRY_RE.findall(_tbl_text(disc, tree, path)):
            labels.setdefault(fn.lower(), label)
    return chars, labels


def classify(path: str, include_all: bool):
    """-> (group, character id or None) or None to skip."""
    base = path.rsplit('/', 1)[-1].lower()
    stem, ext = os.path.splitext(base)
    if ext not in ('.agi', '.fac'):
        return None
    m = re.match(r'(cha|fac)(\d{3})', stem)
    if m and (path.startswith('adv/face/') or path.startswith('common/')):
        if ext == '.fac':
            return 'sprite', int(m.group(2))
        return ('face' if m.group(1) == 'fac' else 'face-other'), int(m.group(2))
    if path.startswith('adv/bg/'):
        if stem.startswith('ivtn'):
            return 'other/cg-thumbnails', None
        if stem.startswith('iv'):
            return 'cg', None
        if stem.startswith('ad_bktn') or stem.startswith('ad_bksm'):
            return 'other/bg-thumbnails', None
        if stem.startswith('ad_bkna'):
            return 'other/bg-names', None
        if stem.startswith('ad_bk'):
            return 'backgrounds', None
        if stem.startswith('mvtn'):
            return 'other/movie-thumbnails', None
        return 'other/bg-misc', None
    if path.startswith('adv/eyecatch/'):
        return 'eyecatch', None
    if path.startswith('common/') and '/' not in path[7:]:
        return 'other/ui', None
    if include_all and ext == '.agi':
        return 'other/' + os.path.dirname(path).replace('/', '_'), None
    return None


def char_folder(names: list[str] | None) -> str | None:
    if not names:
        return None
    jp = names[0]
    return ROMAJI.get(jp) or 'char_' + hashlib.md5(jp.encode()).hexdigest()[:6]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def dump_game(priv: Path, game: str, include_all: bool, jobs: int, contact: bool, log):
    out = priv / 'research' / 'assets-dump' / game
    stage = priv / 'research' / 'assets-dump' / 'tmp' / f'stage-{game}'
    if out.exists():
        shutil.rmtree(out)
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    out.mkdir(parents=True)

    per_disc = []  # (disc id, chars, labels, {path: (sources, result)})
    for disc_id in GAMES[game]:
        isos = sorted((priv / 'discs' / disc_id).glob('*.iso'))
        if not isos:
            log(f'{disc_id}: no ISO, skipped')
            continue
        iso = str(isos[0])
        disc = sky.Disc(Path(iso))
        chars, labels = disc_tables(disc)
        files: dict[str, dict] = {}
        for dat, off, us, cs, path, where in disc_sources(disc):
            cls = classify(path, include_all)
            if cls is None or us == 0:
                continue
            # A path stored in several archives/packs: copies with the same
            # sizes are taken as identical and decoded once.
            f = files.setdefault(path, {'cls': cls, 'variants': {}})
            f['variants'].setdefault((us, cs), [dat, off, us, cs, []])[4].append(where)
        tasks, owners = [], []
        for path in sorted(files):
            for v in files[path]['variants'].values():
                tasks.append((iso, v[0], v[1], v[2], v[3], path))
                owners.append((path, v))
        log(f'{disc_id}: decoding {len(tasks)} files')
        for path in files:
            files[path]['results'] = []
        with mp.Pool(jobs, initializer=_init, initargs=(str(stage),)) as pool:
            for k, (_, res) in enumerate(pool.imap(decode_task, tasks, chunksize=8)):
                path, v = owners[k]
                files[path]['results'].append((v, res))
                if (k + 1) % 2000 == 0:
                    log(f'  {k + 1}/{len(tasks)}')
        per_disc.append((disc_id, chars, labels, files))

    # IDs without a table on their disc.  No fallback to the other disc of
    # the game: Eigou discs 1 and 2 number their characters differently.
    for disc_id, chars, _, _ in per_disc:
        for cid, names in MANUAL_NAMES.get(disc_id, {}).items():
            chars.setdefault(cid, names)

    # Character folders: one per romanised NAME00; disambiguate clashes.
    folder_of: dict[tuple[str, int], str] = {}
    names_json: dict[str, dict] = {}
    for disc_id, chars, _, files in per_disc:
        used = {}
        for cid in sorted({f['cls'][1] for f in files.values() if f['cls'][1] is not None}):
            names = chars.get(cid)
            folder = char_folder(names) or 'unknown'
            if folder != 'unknown':
                jp = names[0]
                if folder in used and used[folder] != cid:
                    folder = f'{folder}_{cid:03d}'
                used[folder] = cid
                ent = names_json.setdefault(folder, {'japanese': jp, 'all_names': [], 'ids': {}})
                for n in names:
                    if n not in ent['all_names']:
                        ent['all_names'].append(n)
                ent['ids'][disc_id] = cid
            folder_of[(disc_id, cid)] = folder

    placed: dict[tuple[str, str], str] = {}   # (dir, sha) -> relative output
    names_taken: dict[str, str] = {}          # relative output -> sha
    rows, sprite_meta, errors = [], {}, []
    sha_discs: dict[tuple[str, str], set] = {}

    def place(rel_dir: str, name: str, sha: str, disc_id: str) -> tuple[str, str]:
        sha_discs.setdefault((rel_dir, sha), set()).add(disc_id)
        if (rel_dir, sha) in placed:
            return placed[(rel_dir, sha)], 'duplicate'
        rel = f'{rel_dir}/{name}.png'
        if rel in names_taken and names_taken[rel] != sha:
            rel = f'{rel_dir}/{name}.{disc_id}.png'
            n = 2
            while rel in names_taken and names_taken[rel] != sha:
                rel = f'{rel_dir}/{name}.{disc_id}.{n}.png'
                n += 1
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        src = stage / f'{sha}.png'
        try:
            os.link(src, dst)
        except OSError:
            shutil.copyfile(src, dst)
        placed[(rel_dir, sha)] = rel
        names_taken[rel] = sha
        return rel, ''

    for disc_id, chars, labels, files in per_disc:
        for path in sorted(files):
            f = files[path]
            group, cid = f['cls']
            stem = os.path.splitext(path.rsplit('/', 1)[-1])[0]
            label = labels.get(path.rsplit('/', 1)[-1].lower(), '')
            folder = folder_of.get((disc_id, cid)) if cid is not None else None
            for v, res in f['results']:
                archives = ' '.join(sorted(set(v[4])))
                if 'error' in res:
                    errors.append((disc_id, path, res['error']))
                    rows.append([disc_id, path, archives, '', '', '', 'error', '', folder or '', label, res['error']])
                    continue
                char_dir = f'sprites/{folder}'
                # 3-digit names (cha001_000.fac, fac001_000.agi) are early
                # line-art / name-card placeholders the tables never use.
                legacy = re.fullmatch(r'(cha|fac)\d{3}_\d{3}', stem.lower()) is not None
                if group == 'sprite':
                    base_dir = char_dir + ('/placeholders' if legacy else '')
                    meta = {'file': path, 'disc': disc_id, 'label': label,
                            'eye_frames': res['eyes'], 'mouth_frames': res['mouths'], 'layers': []}
                    counters = {'eye': 0, 'mouth': 0}
                    for lay in res['layers']:
                        kind = lay['kind']
                        if kind == 'body':
                            rel, note = place(base_dir, stem, lay['sha'], disc_id)
                        else:
                            k = counters[kind]
                            counters[kind] += 1
                            rel, note = place(base_dir + '/parts', f'{stem}_{kind}{k}', lay['sha'], disc_id)
                        meta['layers'].append({'kind': kind, 'x': lay['x'], 'y': lay['y'],
                                               'w': lay['w'], 'h': lay['h'], 'png': rel[len(char_dir) + 1:]})
                        rows.append([disc_id, path + ('' if kind == 'body' else f'#{kind}'), archives, rel,
                                     lay['w'], lay['h'], lay['fmt'], lay['sha'], folder, label,
                                     f'duplicate of {rel}' if note else ''])
                    sprite_meta.setdefault(folder, {}).setdefault(stem, [])
                    if not any(m['layers'] == meta['layers'] for m in sprite_meta[folder][stem]):
                        sprite_meta[folder][stem].append(meta)
                    continue
                lay = res['layers'][0]
                if group in ('face', 'face-other'):
                    rel_dir = char_dir + ('/placeholders' if legacy else '/faces' if group == 'face' else '/other')
                else:
                    rel_dir = group
                rel, note = place(rel_dir, stem, lay['sha'], disc_id)
                rows.append([disc_id, path, archives, rel, lay['w'], lay['h'], lay['fmt'], lay['sha'],
                             folder or '', label, f'duplicate of {rel}' if note else ''])

    # Which discs carry each image (for disc-only notes).
    all_discs = [d for d, *_ in per_disc]
    disc_of_rel = {}
    for (rel_dir, sha), ds in sha_discs.items():
        rel = placed.get((rel_dir, sha))
        if rel:
            disc_of_rel[rel] = ds
    with open(out / 'index.csv', 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(['disc', 'archive_path', 'archives', 'output', 'width', 'height', 'format', 'sha256',
                    'character', 'label', 'note', 'on_discs'])
        for r in rows:
            ds = disc_of_rel.get(r[3], set())
            only = '' if len(all_discs) < 2 else ('all' if len(ds) == len(all_discs) else ' '.join(sorted(ds)))
            w.writerow(r + [only])
    for folder, metas in sprite_meta.items():
        with open(out / 'sprites' / folder / 'sprites.json', 'w', encoding='utf-8') as fh:
            json.dump(metas, fh, ensure_ascii=False, indent=1, sort_keys=True)
    with open(out / 'sprites' / 'names.json', 'w', encoding='utf-8') as fh:
        json.dump(names_json, fh, ensure_ascii=False, indent=1, sort_keys=True)
    shutil.rmtree(stage)

    # Summary
    cg = {r[3] for r in rows if r[3].startswith('cg/')}
    spr_dirs = sorted({r[3].split('/')[1] for r in rows if r[3].startswith('sprites/')})
    bodies = {r[3] for r in rows if r[3].startswith('sprites/') and r[3].count('/') == 2}
    faces = {r[3] for r in rows if '/faces/' in r[3]}
    parts = {r[3] for r in rows if '/parts/' in r[3]}
    disc_only = {}
    if len(all_discs) > 1:
        for rel in cg:
            ds = disc_of_rel.get(rel, set())
            if len(ds) == 1:
                disc_only[next(iter(ds))] = disc_only.get(next(iter(ds)), 0) + 1
    summary = {
        'game': game, 'discs': all_discs, 'cg': len(cg), 'characters': len(spr_dirs),
        'sprite_bodies': len(bodies), 'sprite_parts': len(parts), 'faces': len(faces),
        'cg_disc_only': disc_only, 'files_written': len(names_taken), 'errors': len(errors),
        'unknown_character_files': sum(1 for r in rows if r[8] == 'unknown'),
    }
    with open(out / 'summary.json', 'w', encoding='utf-8') as fh:
        json.dump({**summary, 'error_list': errors}, fh, ensure_ascii=False, indent=1)
    log(json.dumps(summary, ensure_ascii=False))
    for e in errors[:20]:
        log(f'  error: {e}')
    if contact:
        contact_sheets(out, log)
    return summary


def _sheet(items, cell, cols, path, title_h=12):
    """items: (png path, caption).  Thumbnails fit into cell (w, h)."""
    if not items:
        return
    cw, ch = cell
    rows = (len(items) + cols - 1) // cols
    sheet = Image.new('RGB', (cols * cw, rows * (ch + title_h)), (40, 40, 48))
    draw = ImageDraw.Draw(sheet)
    checker = Image.new('RGB', (cw, ch), (90, 90, 100))
    cd = ImageDraw.Draw(checker)
    for yy in range(0, ch, 8):
        for xx in range(0, cw, 8):
            if (xx // 8 + yy // 8) % 2:
                cd.rectangle([xx, yy, xx + 7, yy + 7], fill=(120, 120, 130))
    for k, (png, cap) in enumerate(items):
        im = Image.open(png).convert('RGBA')
        im.thumbnail((cw, ch))
        x, y = (k % cols) * cw, (k // cols) * (ch + title_h)
        bg = checker.copy().convert('RGBA')
        bg.alpha_composite(im, ((cw - im.width) // 2, (ch - im.height) // 2))
        sheet.paste(bg.convert('RGB'), (x, y))
        draw.text((x + 2, y + ch), cap[:cw // 6], fill=(230, 230, 230))
    sheet.save(path)


def contact_sheets(out: Path, log):
    cgs = sorted((out / 'cg').glob('*.png'))
    _sheet([(p, p.stem) for p in cgs], (128, 96), 24, out / 'contact-cg.png')
    items = []
    sp = out / 'sprites'
    for folder in sorted(p for p in sp.iterdir() if p.is_dir()) if sp.exists() else []:
        comp = sorted(folder.glob('*.png'))
        faces = sorted((folder / 'faces').glob('*.png'))
        for p in comp[:3] + faces[:1]:
            items.append((p, f'{folder.name}:{p.stem[-4:]}'))
    _sheet(items, (96, 192), 24, out / 'contact-sprites.png')
    log(f'contact sheets: {len(cgs)} CGs, {len(items)} sprite cells')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('private', type=Path, help='private root holding discs/<disc>/*.iso')
    ap.add_argument('--game', choices=sorted(GAMES), action='append',
                    help='game to dump (repeatable; default all)')
    ap.add_argument('--all', action='store_true', help='also dump every other AGI texture (3D, battle)')
    ap.add_argument('--jobs', type=int, default=max(1, min(8, (os.cpu_count() or 2) - 2)))
    ap.add_argument('--no-contact', action='store_true', help='skip contact sheets')
    args = ap.parse_args(argv)

    def log(msg):
        print(msg, flush=True)

    for game in args.game or ['zettai', 'mugen', 'eigou']:
        log(f'== {game}')
        dump_game(args.private, game, args.all, args.jobs, not args.no_contact, log)


if __name__ == '__main__':
    main()
