# Voice clips

`tools/assets/dump_voices.py` decodes every voice clip on the five discs to
WAV and sorts the clips into one folder per speaking character. It reads the
player's ISO images. No game data or dialogue is stored in this repository.
It needs Python 3.10+, numpy, and the readers in `tools/modding/` (`ga2arc`,
`ga2asb`, `script_xlsx`).

```bash
python3 tools/assets/dump_voices.py <private> [--out DIR] [--discs zettai,mugen-1,...] [--jobs N] [--dry-run]
```

The default output folder is `<private>/research/voices/`. A full run over all
five discs takes about 5 minutes with 8 jobs. It writes 118,278 WAV files
(about 16.7 GB). `--dry-run` maps the clips and prints the counts per
character without writing anything. A rerun deletes the WAVs listed in the
previous `index.csv` that the new run no longer writes, so you can run it again
over an existing output folder.

## Output

| Path | Content |
| --- | --- |
| `<English (Japanese)>/<game>_d<N>_<bank><id>_<name>.wav` | 16-bit mono PCM at the clip's own rate. `bank` is `adv` (story), `slg` (battle) or `misc` (title/system calls); `id` is the clip number in that bank; `name` is the recording name stored in the clip |
| `_unknown/<bank>/<code>/...` | clips whose speaker the disc data does not identify (see Limits) |
| `index.csv` | one row per clip occurrence (disc, bank, id) |
| `summary.csv` | per character: clip occurrences and unique clips per game, and files written |

Folder names come from the translation workbook's Names sheet
(`<private>/research/translation/ga2_script.xlsx`, English and Japanese
`NAMEnn`). Without the workbook they fall back to the disc's
`adv/charNNN.tbl`. Group speakers such as "Kazuya & Apricot" get their own
folder, as the scripts name them.

**Deduplication.** Clips are hashed by their ADPCM data and sample rate.
Identical clips are written once: the first occurrence in disc order (zettai,
mugen-1, mugen-2, eigou-1, eigou-2), then bank order (adv, misc, slg), names
the file. Every other occurrence still gets its own `index.csv` row, which
points at that file and gives the first occurrence in `duplicate_of`. Mugen
disc 2 carries a copy of Zettai's battle and misc banks, so 32,064 of the
150,342 occurrences are duplicates.

`index.csv` columns:

- `file`, `character`, `character_id`: the char-table id on that disc, with
  `:k` for `NAMEk` (for example `2:1` for Tequila on Zettai and Mugen).
- `game`, `disc`, `voice_id`: the clip number in its bank, which is also the
  script voice argument for `adv`.
- `source`: `.msb` path, clip offset and size, plus the disc file and absolute
  offset.
- `duration_s`.
- `script_refs`: `disc:script:sN`, the translation-workbook row ID of every
  line that plays the clip.
- `japanese_line`: text of those lines.
- `bank`, `clip_name`, `code`, `rate`.
- `speaker_evidence`: which rule assigned the folder, with its counts.
- `script_speakers`: set when a script line shows a different name tag than
  the folder.
- `battle_table_chars`: battle voice-table rows, as character id and portrait
  file number.
- `peak_dbfs`, `rms_dbfs`, `sha1`, `duplicate_of`.

## Format

Each disc has three banks in a stream archive (`STRM_R.DAT` on Zettai,
`STRM_R3.DAT` on the others), listed in `IDX.DAT` like any other file:

| Bank | Clips (Z / M1 / M2 / E1 / E2) | Use |
| --- | --- | --- |
| `voice/advvoice.msh/.msb/.vac` | 23500 / 27679 / 25966 / 35173 / 14021 | story (ADV) lines |
| `voice/slgvoice.msh/.msb` | 2966 / 5184 / 2966 / 7120 / 5226 | battle (SLG) lines |
| `voice/misc.msh/.msb` | 68 / 99 / 68 / 200 / 100 | title calls and other system voices (`LOGO_VOICE`) |

Zettai also has `adv/voice/ranfa22.msh/.msb` in ALL.DAT (6 clips, RAIC
compressed). The executable names the loader `common/voicemgr.cpp`
("VoiceBank", `%s%s.msh` / `%s%s.msb`). Copies of the `.msh` files sit in FSTS
load groups (`advinit`, `title`, `slginitNNNNNN`).

- **`.msh`** (little endian): `u32 file_size, u32 padding_bytes, u32 count`,
  then `count` records `{u32 size, u32 id, u32 offset, u32 rate}`. The ids run
  0..count-1. The offset is the clip's start in the `.msb`, aligned to 2048
  bytes, and the size includes the clip header. The rate is 22050, 24000,
  32000 or 44100.
- **`.msb` clip ("MSVp")**: a 48-byte big-endian header in the PS2 VAGp
  layout: `"MSVp"`, version 4, 0, data size (= size - 48), sample rate, 12
  zero bytes, then a 16-byte recording name (`kaz067340`, `TAC500010.aif`,
  `ED8_01.wav`). The header is followed by mono PS2 SPU ADPCM: 16-byte frames
  holding a shift/filter byte, a flag byte (always 0 here) and 28 4-bit
  samples. `ranfa22.msb` has no clip headers or names, only raw ADPCM.
- **ADPCM**: the standard SPU decoder (filters 0-4: (0,0), (60,0),
  (115,-52), (98,-55), (122,-60), /64 with rounding, clamped to 16 bits). The
  tool's vectorised decoder is bit-exact with pcsx2's `XA_decode_block`, as
  vendored in the runtime's `spu2core`.
- **`.vac`** (`"VAC "`, one variable-length record per advvoice clip) is
  probably lip-sync data (`common/voiceanim.cpp`, "VoiceAnim"). The tool does
  not use it.

### Script voice argument

The voice argument of `say` (0x13, argument 3), `think` (0x14) and
`say_voice` (0x56, argument 4 after the flags argument) is the `advvoice`
clip id on the same disc. `text_slot` (0x4b) argument 5 is collected the same
way. Proof:

- On every disc, the speakers of the lines that play a clip agree with the
  three-letter code of that clip's recording name. On Zettai, char 0 lines
  play `kaz` clips 8235 times, char 1 plays `apr` clips 1940 times, char 2
  NAME01 plays `tql` clips 644 times, and so on.
- Clip duration follows line length. Over 108,199 voiced lines the
  correlation is 0.86, at a median of 4.7 characters per second.

The second `msg` argument on Mugen and Eigou is **not** a voice. It is a
per-line serial number (unique per line, and in 94% of cases one more than
the previous line), probably for read tracking.

### Speaker mapping

The first letters of the recording name are a per-role code (`kaz`, `apr`,
`tql`, `ros`, ...). Upper and lower case are the same code. `assign_speakers()`
applies these rules in order:

1. **Code owned in this game.** The tool uses the story-script lines that
   play clips of the code (both discs of a game together). If at least 90% of
   those lines are spoken by one character (char id plus `NAMEnn`), every clip
   with that code goes to that character, including battle, misc and
   unreferenced clips. The voice actor recorded under that code, so a clip
   goes to the code owner even when a few lines show a different name tag
   (these are listed in `script_speakers`).
2. **Battle voice table.** `slg/voicetbl/slgvNNNNNN.tbl` files in SLG.DAT
   packs have rows `tension, flag, char id, face id, filter, probability,
   voice id` (the voice id is the `slgvoice` clip). They also have
   `[FACEFILES]` that map face ids to `adv/face/facNNN_xxxx.agi`. A battle
   clip goes to character N when all its rows name N and show only N's own
   portraits. On clips that also have an owned code, this rule agrees with
   the code 14,097 times out of 14,105. The exceptions are another
   character's lines filed under a heroine's unit. The rule sorts the
   Moon Angel `S_FO_`/`S_MF_`/... and `GA`/`GB`/`GC` clips, most Eigou
   battle `ROS`, `WIL`, `KAL`, `ALI` and `MAG` clips, and a few others.
3. **Cross-game match.** If no script in the game plays the code (for example
   Eigou's battle-only `dit`, `ver`), but every other game that plays it ties
   it to the same character with at least 20 lines, the clip goes to that
   character, and the evidence says "cross-game match".
4. **Per-clip speaker.** If a code is shared by several roles (`all`, `crw`,
   `pcv`, Eigou `ros`), a clip that the scripts play for only one speaker goes
   to that speaker.
5. **Identical audio.** If an occurrence is still unassigned, but other
   occurrences of the same recording (same hash) agree on a speaker, it takes
   that speaker. Example: Mugen battle clips copied onto Eigou disc 2, where
   their code is shared.
6. Anything else goes to `_unknown/`. The tool never assigns a folder from
   the code's letters alone.

Speech check: in a random sample of 400 decoded clips, about 94% have mostly
voiced frames. The spectral centroid is about 3.1 kHz, against 8 kHz for white
noise. The envelopes are syllabic. Median pitch differs by character as
expected (Morden about 134 Hz, Tact 195 Hz, Apricot 385 Hz).

## Results (all five discs)

The per-character counts per game are in `<private>/research/voices/summary.csv`.
The main cast per game (unique clips):

| Character | Zettai | Mugen | Eigou |
| --- | --- | --- | --- |
| Kazuya | 7795 | 17306 | 13710 |
| Anise | 2311 | 4835 | 4682 |
| Apricot | 2361 | 4687 | 4054 |
| Lily | 1606 | 3735 | 4135 |
| Nano-Nano | 1833 | 3590 | 3370 |
| Natsume | - | 1516 | 4780 |
| Coco | 861 | 3155 | 3071 |
| Kahlua | 1249 | 2704 | 2849 |
| Tequila | 1064 | 2317 | 2050 |
| Tact | 2750 | 3659 | 1261 |

## Limits

- **`_unknown/`** holds 1,131 unique clips (1,174 occurrences), mostly:
  - Eigou story `ros` clips that no script plays. The Eigou scripts use code
    `ros` for Roselle, Helea and Roselle (God), so the code does not identify
    the speaker. The Mugen scripts use it only for Roselle.
  - Group `all` clips that no script plays, or that several speakers play.
  - Mugen story `gay` (knights) and `ED8_*` (16 clips, no code, no script
    use).
  - Eigou battle `sxc`, `wil`, `ros` and `all` clips that no battle table
    ties to one character.
  - Zettai `ranfa22` (the bank name suggests Ranpha, but its clips have no
    names, no script or table uses them, and they match no Ranpha clip).
  - A handful of one-off codes.
- **Eigou disc 2** has only 45 scripts, so most of its 14,021 story clips
  have no `script_refs`. They are still sorted by the code owners from Eigou
  disc 1's scripts.
- **Battle and misc clips** have no script lines. Their folder comes from
  the code (rule 1) or the battle tables (rule 2).
- `misc` clips are only sorted by code (no table was examined for them).
- With `--discs` limited to some discs, ownership and cross-game matches use
  only the loaded discs, so a partial run can sort fewer clips.
