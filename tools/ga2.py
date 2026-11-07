#!/usr/bin/env python3
"""Reproducible local bring-up commands; no game material enters the source tree."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tomllib
from apply_patch_stack import ensure_patch_stack, unrelated_changes
from import_discs import find_iso, has_iso

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / "third_party/PS2Recomp"
BUILD = ROOT / "build/ps2recomp"
PIN = "2c5fbb9389e11dd95693385969490c9e8e6f57b4"
DISCS = ("zettai", "mugen-1", "mugen-2", "eigou-1", "eigou-2")


def default_private_root():
    """$GA2_PRIVATE_ROOT; else galaxy-angel-ii-private beside the folder holding
    this checkout, if it exists (older layout); else private/ inside the
    checkout (git-ignored). The same rule as tools/private_root.sh."""
    if os.environ.get("GA2_PRIVATE_ROOT"):
        return Path(os.environ["GA2_PRIVATE_ROOT"])
    sibling = ROOT.parent.parent / "galaxy-angel-ii-private"
    return sibling if sibling.is_dir() else ROOT / "private"


DEFAULT_PRIVATE = default_private_root()


def run(command, log=None):
    if log:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("w") as out:
            subprocess.run([str(x) for x in command], cwd=ROOT, stdout=out,
                           stderr=subprocess.STDOUT, check=True)
    else:
        subprocess.run([str(x) for x in command], cwd=ROOT, check=True)


def manifest(private, disc):
    path = private / "manifests" / f"{disc}.json"
    data = json.loads(path.read_text())
    boot = private / data["boot"]["extracted_path"]
    actual = hashlib.sha256(boot.read_bytes()).hexdigest()
    if actual != data["boot"]["sha256"]:
        raise ValueError(f"{disc}: extracted executable changed; import again")
    return data, boot


def disc_mount_arguments(private, disc):
    builds = json.loads((ROOT / "configs/verified-builds.json").read_text())["builds"]
    build = next((item for item in builds if item["disc_id"] == disc), None)
    if build is None:
        raise ValueError(f"{disc}: not in configs/verified-builds.json (known: {', '.join(DISCS)})")
    image = find_iso(private / "discs" / disc)  # exactly one image, any case of .iso
    metadata = private / "manifests" / f"{disc}.json"
    if not metadata.is_file():
        raise ValueError(f"{disc}: not imported (no {metadata}); run `ga2.py import {disc}`")
    if hashlib.sha256(metadata.read_bytes()).hexdigest() != build["manifest_sha256"]:
        raise ValueError(f"{disc}: imported metadata changed; import again")
    return ["--disc-image", image, "--disc-manifest", metadata,
            "--disc-image-sha256", build["iso_sha256"],
            "--disc-manifest-sha256", build["manifest_sha256"]]


def disc_set_arguments(private, disc):
    """Boot-disc mount plus every other imported GA2 disc as --extra-disc.

    The runtime inserts an extra disc by itself when the game asks for it
    (multi-disc story, key-disc checks), so players never swap discs."""
    arguments = list(disc_mount_arguments(private, disc))
    for other in DISCS:
        if other == disc or not has_iso(private / "discs" / other):
            continue
        mount = disc_mount_arguments(private, other)
        arguments += ["--extra-disc", mount[1], mount[3], mount[5], mount[7]]
    # LoadExecPS2 of another disc's program hands over to that disc's binary.
    for other in DISCS:
        binary = private / "build" / "ga2-runtime" / "ga2" / "bin" / f"ga2_{other.replace('-', '_')}"
        if binary.exists():
            _, boot = manifest(private, other)
            arguments += ["--exec-target", boot.name, binary, boot]
    # One memory card for the whole set (saves carry over between games).
    # GA2_MC_ROOT points scripted runs at another card (e.g. a blank one).
    cards = Path(os.environ["GA2_MC_ROOT"]) if os.environ.get("GA2_MC_ROOT") else private / "memcards" / "mc0"
    cards.mkdir(parents=True, exist_ok=True)
    arguments += ["--mc-root", cards]
    return arguments


def configure(private, jobs, selected=None):
    actual = subprocess.check_output(["git", "-C", str(UPSTREAM), "rev-parse", "HEAD"], text=True).strip()
    if actual != PIN:
        raise ValueError(f"upstream revision {actual} differs from pinned {PIN}")
    patches = sorted((ROOT / "patches").glob("*.patch"))
    ensure_patch_stack(UPSTREAM, patches)
    stray = unrelated_changes(UPSTREAM, patches)
    if stray:
        raise ValueError("the upstream checkout has edits outside the patch stack, which would be built under the "
                         "'pinned upstream plus reviewed patches' identity: " + ", ".join(stray[:8]) +
                         (" ..." if len(stray) > 8 else ""))
    command = ["cmake", "-S", UPSTREAM, "-B", BUILD, "-G", "Ninja",
               "-DCMAKE_BUILD_TYPE=RelWithDebInfo", "-DPS2X_RUNTIME_SHARED=ON", "-DCMAKE_C_COMPILER=clang",
               "-DCMAKE_CXX_COMPILER=clang++", "-DPS2X_BUILD_STUDIO=OFF",
               "-DPS2X_BUILD_TEST=ON", "-DPS2X_ENABLE_AGRESSIVE_LOGS=OFF",
               f"-DCMAKE_PROJECT_PS2RetroX_INCLUDE={ROOT / 'cmake/AttachNativeTargets.cmake'}",
               f"-DGA2_PRIVATE_ROOT={private}", f"-DGA2_DISC_IDS={';'.join(selected or DISCS)}"]
    run(command, private / "configure.log")


def toolchain_binary(build, relative):
    path = Path(build) / relative
    if not path.is_file():
        raise ValueError(f"{path} is missing; run `ga2.py setup` (add --legacy for the shared submodule build)")
    return path


def configure_consolidated(private, generated_root):
    """Configure private/build/ga2-runtime from the consolidated tree (tools/make_runtime_tree.sh)."""
    tree = private / "worktrees/ga2-runtime"
    if not tree.is_dir():
        raise ValueError(f"{tree} is missing; create it with tools/make_runtime_tree.sh")
    configured = [d for d in DISCS if (generated_root / d / "identity.json").exists()] or ["zettai"]
    env = dict(os.environ, GA2_PRIVATE_ROOT=str(private), GA2_GENERATED_ROOT=str(generated_root),
               GA2_DISC_IDS=";".join(configured))
    subprocess.run([str(ROOT / "tools/configure_runtime.sh")], env=env, check=True, stdout=subprocess.DEVNULL)


def analyze(private, disc, ghidra, build=BUILD, use_ghidra=False):
    data, boot = manifest(private, disc)
    analysis = private / "analysis" / disc
    analysis.mkdir(parents=True, exist_ok=True)
    # The function maps of the known executables ship in configs/maps (Ghidra's
    # export for exactly this ELF); Ghidra runs only when asked or for another ELF.
    shipped = ROOT / "configs/maps" / disc
    if not use_ghidra and (shipped / "identity.json").is_file():
        identity = json.loads((shipped / "identity.json").read_text())
        if identity["elf_sha256"] == data["boot"]["sha256"] and identity["ps2recomp_commit"] == PIN:
            for name in ("functions.csv", "ghidra.toml", "identity.json"):
                shutil.copy2(shipped / name, analysis / name)
            print(f"{disc}: function map from configs/maps/{disc}")
            return
    run([toolchain_binary(build, "ps2xAnalyzer/ps2_analyzer"), boot, analysis / "analyzer.toml"],
        analysis / "analyzer.log")
    projects = private / "ghidra"
    projects.mkdir(parents=True, exist_ok=True)
    project_exists = (projects / f"{disc}.gpr").exists()
    # A stale map must never be stamped with the current executable's identity:
    # remove every earlier output, and re-import (overwriting the project's
    # program) unless the project is known to hold exactly this executable.
    for stale in ("functions.csv", "ghidra.toml", "identity.json"):
        (analysis / stale).unlink(missing_ok=True)
    marker = projects / f"{disc}.elf-sha256"
    same_program = project_exists and marker.exists() and marker.read_text().strip() == data["boot"]["sha256"]
    marker.unlink(missing_ok=True)
    command = [ghidra / "support/analyzeHeadless", projects, disc]
    if same_program:
        command += ["-process", boot.name]
    else:
        command += ["-import", boot, "-overwrite", "-processor", "r5900:LE:32:default"]
    command += ["-analysisTimeoutPerFile", "300", "-max-cpu", "2", "-scriptPath",
                UPSTREAM / "ps2xRecomp/tools/ghidra", "-postScript", "ExportPS2Functions.java",
                analysis / "ghidra.toml", analysis / "functions.csv"]
    run(command, analysis / "ghidra.log")
    if not (analysis / "functions.csv").is_file() or not (analysis / "ghidra.toml").is_file():
        raise ValueError(f"{disc}: Ghidra did not export a map; inspect private analysis log")
    marker.write_text(data["boot"]["sha256"] + "\n")
    (analysis / "identity.json").write_text(json.dumps({"elf_sha256": data["boot"]["sha256"],
        "ps2recomp_commit": PIN, "processor": "r5900:LE:32:default"}, indent=2) + "\n")


def write_source_list(directory):
    """sources.txt: the build reads this instead of globbing ~35k files."""
    names = sorted(path.name for path in directory.glob("*.cpp"))
    (directory / "sources.txt").write_text("".join(name + "\n" for name in names))


def generate(private, disc, build=BUILD, generated_root=None):
    data, boot = manifest(private, disc)
    analysis = private / "analysis" / disc
    identity = json.loads((analysis / "identity.json").read_text())
    if identity["elf_sha256"] != data["boot"]["sha256"] or identity["ps2recomp_commit"] != PIN:
        raise ValueError(f"{disc}: analysis identity mismatch; analyze this exact executable")
    source = (analysis / "ghidra.toml").read_text()
    generated_root = generated_root or private / "generated"
    final = generated_root / disc
    if (final / "identity.json").exists():
        old = json.loads((final / "identity.json").read_text())
        if old["elf_sha256"] != data["boot"]["sha256"]:
            raise ValueError(f"{disc}: different build in output directory; use a new private root")
    # Generate into a fresh directory and swap it in, so files from an older
    # function map can never linger and be globbed into the build.
    output = generated_root / f".{disc}.new"
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    # Preserve guest instructions. No blanket COP0/CACHE/SYSCALL NOP patching.
    values = {"input": json.dumps(str(boot)), "output": json.dumps(str(output)),
              "ghidra_output": json.dumps(str(analysis / "functions.csv")),
              "patch_syscalls": "false", "patch_cop0": "false", "patch_cache": "false"}
    for key, value in values.items():
        source, count = re.subn(rf"(?m)^{key}\s*=.*$", f"{key} = {value}", source)
        if count != 1:
            raise ValueError(f"unexpected Ghidra config: missing or duplicate {key}")
    source = source.replace("[general]\n", "[general]\nlow_memory_mode = true\noutput_worker_threads = 1\n", 1)
    hints = recomp_entry_points(disc, data["boot"]["sha256"])
    called = direct_call_targets(boot, analysis / "functions.csv")
    unmapped = unmapped_code_entries(boot, analysis / "functions.csv")
    pointers = code_pointer_targets(boot, analysis / "functions.csv")
    if called or unmapped or pointers:
        print(f"{disc}: entry points added: {len(called)} direct-call targets, {len(unmapped)} unmapped code starts, "
              f"{len(pointers)} code pointers")
        hints = sorted(set(hints) | set(called) | set(unmapped) | set(pointers))
    if hints:
        general = tomllib.loads(source)["general"]
        line = "entry_points = " + json.dumps(sorted(set(general.get("entry_points", []) + hints)))
        if "entry_points" in general:
            source, count = re.subn(r"(?ms)^entry_points\s*=\s*\[[^\]]*\]", line, source)
            if count != 1:
                raise ValueError("cannot safely merge generated entry-point metadata")
        else:
            source = source.replace("[general]\n", "[general]\n" + line + "\n", 1)
    stubs = recomp_sdk_stubs(disc, data["boot"]["sha256"])
    if stubs:
        general = tomllib.loads(source)["general"]
        line = "stubs = " + json.dumps(sorted(set(general.get("stubs", []) + stubs)))
        if "stubs" in general:
            source, count = re.subn(r"(?ms)^stubs\s*=\s*\[[^\]]*\]", line, source)
            if count != 1:
                raise ValueError("cannot safely merge generated SDK stub metadata")
        else:
            source = source.replace("[general]\n", "[general]\n" + line + "\n", 1)
    tomllib.loads(source)
    config = analysis / "recomp.toml"
    config.write_text(source)
    run([toolchain_binary(build, "ps2xRecomp/ps2_recomp"), config], analysis / "recomp.log")
    if not (output / "register_functions.cpp").exists():
        raise ValueError(f"{disc}: no generated function table")
    generator = subprocess.run(["git", "-C", str(runtime_tree_of(build)), "rev-parse", "HEAD"],
                               capture_output=True, text=True).stdout.strip()
    identity["generator_commit"] = generator or None
    identity.update({"status": "generated-unverified", "config_sha256": hashlib.sha256(source.encode()).hexdigest(),
                     "map_sha256": hashlib.sha256((analysis / "functions.csv").read_bytes()).hexdigest()})
    (output / "identity.json").write_text(json.dumps(identity, indent=2) + "\n")
    old = generated_root / f".{disc}.old"
    if old.exists():
        shutil.rmtree(old)
    if final.exists():
        final.rename(old)
    write_source_list(output)
    output.rename(final)
    if old.exists():
        shutil.rmtree(old)


def direct_call_targets(elf_path, functions_csv):
    """JAL targets that the function map does not start a function at.

    Every JAL target is a function entry; the map can miss one when a callee
    sits inside a range Ghidra merged. Only call sites inside mapped functions
    count, so data that happens to decode as JAL is ignored.
    """
    elf = Path(elf_path).read_bytes()
    phoff, = struct.unpack_from("<I", elf, 0x1C)
    phentsize, phnum = struct.unpack_from("<HH", elf, 0x2A)
    segments = []
    for i in range(phnum):
        p_type, p_offset, p_vaddr, _, p_filesz, _, p_flags, _ = struct.unpack_from("<8I", elf, phoff + i * phentsize)
        if p_type == 1 and p_flags & 1:
            segments.append((p_vaddr, p_offset, p_filesz))
    starts, ranges = set(), []
    for line in Path(functions_csv).read_text().splitlines()[1:]:
        fields = line.rsplit(",", 3)
        if len(fields) == 4:
            start, end = int(fields[1], 16), int(fields[2], 16)
            starts.add(start)
            ranges.append((start, end))
    ranges.sort()
    import bisect
    keys = [r[0] for r in ranges]

    def mapped(address):
        i = bisect.bisect_right(keys, address) - 1
        return i >= 0 and ranges[i][0] <= address < ranges[i][1]

    def executable(address):
        return any(va <= address < va + size for va, _, size in segments)

    targets = set()
    for va, offset, size in segments:
        for i in range(0, size - 3, 4):
            word, = struct.unpack_from("<I", elf, offset + i)
            if word >> 26 != 3:
                continue
            source = va + i
            target = ((source + 4) & 0xF0000000) | ((word & 0x3FFFFFF) << 2)
            if target not in starts and executable(target) and mapped(source):
                targets.add(f"0x{target:08X}")
    return sorted(targets)


def code_pointer_targets(elf_path, functions_csv):
    """Code addresses that the program stores or builds as pointers.

    Function pointers live in data (vtables, callback tables) or are formed
    with lui + addiu/ori; JALR targets the function map may not start a
    function at. Candidates must be 4-aligned inside an executable segment,
    not already a mapped function start, and not a branch delay slot: data
    constants that happen to equal such an address (0x00100FB0, 0x00110000)
    used to split functions between a JAL and its delay slot, dropping the
    delay-slot instruction and the call's return point (Mugen's tutorial
    froze at 0x140FB4).
    """
    elf = Path(elf_path).read_bytes()
    phoff, = struct.unpack_from("<I", elf, 0x1C)
    phentsize, phnum = struct.unpack_from("<HH", elf, 0x2A)
    loads = []
    for i in range(phnum):
        p_type, p_offset, p_vaddr, _, p_filesz, _, p_flags, _ = struct.unpack_from("<8I", elf, phoff + i * phentsize)
        if p_type == 1:
            loads.append((p_vaddr, p_offset, p_filesz, bool(p_flags & 1)))
    code = [(va, size) for va, _, size, x in loads if x]
    starts = set()
    for line in Path(functions_csv).read_text().splitlines()[1:]:
        fields = line.rsplit(",", 3)
        if len(fields) == 4:
            starts.add(int(fields[1], 16))

    def word(address):
        for va, offset, size, _ in loads:
            if va <= address < va + size:
                return struct.unpack_from("<I", elf, offset + address - va)[0]
        return None

    def candidate(address):
        if address % 4 or address in starts or not any(va <= address < va + size for va, size in code):
            return False
        previous = word(address - 4)
        return previous is None or not is_branch(previous)

    targets = set()
    for va, offset, size, executable in loads:
        words = struct.unpack_from(f"<{size // 4}I", elf, offset)
        if not executable:
            targets.update(w for w in words if candidate(w))
            continue
        high = {}
        for w in words:
            op, rs, rt, imm = w >> 26, (w >> 21) & 31, (w >> 16) & 31, w & 0xFFFF
            if op == 0x0F:
                high[rt] = imm << 16
            elif op in (0x09, 0x0D) and rs in high:
                value = (high[rs] + (imm - 0x10000 if op == 0x09 and imm & 0x8000 else imm)) & 0xFFFFFFFF
                if candidate(value):
                    targets.add(value)
    return sorted(f"0x{t:08X}" for t in targets)


def is_branch(word):
    """True for an R5900 jump or branch (an instruction with a delay slot)."""
    op = word >> 26
    if op in (0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x14, 0x15, 0x16, 0x17):  # J JAL BEQ BNE BLEZ BGTZ (+likely)
        return True
    if op == 0x00 and word & 0x3F in (0x08, 0x09):  # JR JALR
        return True
    if op == 0x01 and (word >> 16) & 0x1F in (0x00, 0x01, 0x02, 0x03, 0x10, 0x11, 0x12, 0x13):  # REGIMM branches
        return True
    return op in (0x11, 0x12) and (word >> 21) & 0x1F == 0x08  # BC1x, BC2x


INVALID_R5900_OPCODES = {0x13, 0x1D, 0x30, 0x32, 0x34, 0x35, 0x38, 0x3A, 0x3B, 0x3C, 0x3D}


def unmapped_code_entries(elf_path, functions_csv):
    """Probable function starts in executable bytes the function map skipped.

    Ghidra leaves leaf functions that are only reached through pointers
    undefined. A candidate is the first non-zero word of a gap or the word
    after a `jr ra` and its delay slot; it is kept when the code from there
    reaches a `jr ra` within 4 KB without an invalid R5900 primary opcode.
    """
    elf = Path(elf_path).read_bytes()
    phoff, = struct.unpack_from("<I", elf, 0x1C)
    phentsize, phnum = struct.unpack_from("<HH", elf, 0x2A)
    segments = []
    for i in range(phnum):
        p_type, p_offset, p_vaddr, _, p_filesz, _, p_flags, _ = struct.unpack_from("<8I", elf, phoff + i * phentsize)
        if p_type == 1 and p_flags & 1:
            segments.append((p_vaddr, p_offset, p_filesz))
    ranges = []
    for line in Path(functions_csv).read_text().splitlines()[1:]:
        fields = line.rsplit(",", 3)
        if len(fields) == 4:
            ranges.append((int(fields[1], 16), int(fields[2], 16)))
    ranges.sort()
    entries = set()
    for va, offset, size in segments:
        def word(address):
            return struct.unpack_from("<I", elf, offset + address - va)[0]
        gaps, last = [], va
        for start, end in ranges:
            if end <= va or start >= va + size:
                continue
            if start > last:
                gaps.append((last, start))
            last = max(last, end)
        if last < va + size:
            gaps.append((last, va + size))
        for begin, end in gaps:
            candidates, address = [], begin
            while address < end:
                if word(address) == 0:
                    address += 4
                    continue
                candidates.append(address)
                # next candidate after the next jr ra + delay slot
                while address < end and word(address) != 0x03E00008:
                    address += 4
                address += 8
            for start in candidates:
                address, ok = start, False
                while address < min(end, start + 4096):
                    w = word(address)
                    if w >> 26 in INVALID_R5900_OPCODES:
                        break
                    if w == 0x03E00008:
                        ok = True
                        break
                    address += 4
                if ok:
                    entries.add(f"0x{start:08X}")
    return sorted(entries)


def runtime_tree_of(build):
    cache = Path(build) / "CMakeCache.txt"
    if cache.exists():
        for line in cache.read_text().splitlines():
            if line.startswith("CMAKE_HOME_DIRECTORY:INTERNAL="):
                return Path(line.split("=", 1)[1])
    return UPSTREAM


def recomp_entry_points(disc, elf_sha256):
    profiles = json.loads((ROOT / "configs/recomp-entry-points.json").read_text())
    if profiles["schema_version"] != 1:
        raise ValueError("unsupported entry-point profile schema")
    matches = [item for item in profiles["builds"] if item["disc_id"] == disc]
    if not matches:
        return []
    if len(matches) != 1 or matches[0]["elf_sha256"] != elf_sha256:
        raise ValueError(f"{disc}: entry-point profile requires its exact executable")
    hints = matches[0]["entry_points"]
    if not all(re.fullmatch(r"0x[0-9A-Fa-f]{8}", item) and int(item, 16) % 4 == 0 for item in hints):
        raise ValueError("unaligned or malformed entry-point profile")
    return hints


def recomp_sdk_stubs(disc, elf_sha256):
    per_disc = ROOT / f"configs/sdk-bindings-{disc}.json"
    profile = json.loads((per_disc if per_disc.exists() else ROOT / "configs/sdk-bindings.json").read_text())
    if profile["schema_version"] != 1:
        raise ValueError("unsupported SDK binding profile schema")
    if profile["disc_id"] != disc:
        return []
    if profile["elf_sha256"] != elf_sha256:
        raise ValueError(f"{disc}: SDK binding profile requires its exact executable")
    bindings = [item for item in profile["bindings"]
                if item.get("generation_strategy") == "runtime-stub"]
    selectors = []
    addresses = set()
    for item in bindings:
        name, address = item["name"], item["address"]
        if (not re.fullmatch(r"sce[A-Za-z0-9]+", name) or
                not re.fullmatch(r"[0-9A-Fa-f]{8}", address) or int(address, 16) % 4):
            raise ValueError("unaligned or malformed SDK binding profile")
        value = int(address, 16)
        if value in addresses:
            raise ValueError("duplicate SDK stub address")
        addresses.add(value)
        selectors.append(f"{name}@0x{value:08X}")
    return selectors


def vu1gen(private, build, generated_root):
    """Generate C++ VU1 programs from captured microcode (all discs).

    Inputs: GA2_VU1CAP captures in <private>/vu1/<disc>/captures and raw code
    images (GA2_VU1_DUMP, with the entry points that missed in <hash>.entries)
    in <private>/vu1/<disc>/images. A captured image gets a program for the
    pairs its runs executed, selected by code-image hash; every image also
    gives one program per entry point, selected by the code reachable from it,
    so the same microprogram is found in other images. Output replaces
    <generated_root>/vu1, which every engine links.
    """
    # Programs are keyed by code, not disc, so inputs from every disc
    # contribute (the GA2 discs share their microcode).
    inputs = sorted(private.glob("vu1/*/captures/*.bin")) + sorted(private.glob("vu1/*/images/*.bin"))
    if not inputs:
        raise ValueError(f"no VU1 captures or images under {private / 'vu1'}")
    run(["cmake", "--build", build, "--target", "ga2_vu1_gen"])
    tool = toolchain_binary(build, "ps2xTest/ga2_vu1_gen")
    out = generated_root / "vu1"
    fresh = out.with_name(out.name + ".new")
    shutil.rmtree(fresh, ignore_errors=True)
    fresh.mkdir(parents=True)
    run([tool, fresh, *inputs])
    shutil.rmtree(out, ignore_errors=True)
    fresh.rename(out)


def status(private, build, generated_root):
    for disc in DISCS:
        p = private / "manifests" / f"{disc}.json"
        if not p.exists():
            print(f"{disc}: not imported")
            continue
        data = json.loads(p.read_text())
        generated = (generated_root / disc / "identity.json").exists()
        vu1 = any((generated_root / f"{disc}-vu1").glob("vu1_*.cpp"))
        binary = build / "ga2/bin" / f"ga2_{disc.replace('-', '_')}"
        print(f"{disc}: {data['boot']['path']} imported; generated={generated}; vu1={vu1}; binary={binary.exists()}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--ghidra", type=Path, default=Path("/opt/ghidra"))
    parser.add_argument("--ghidra-analysis", action="store_true",
                        help="analyze: run Ghidra even when configs/maps has this executable's map")
    parser.add_argument("--legacy", action="store_true",
                        help="use the shared submodule build instead of the consolidated ga2 runtime tree")
    parser.add_argument("command", choices=("setup", "import", "analyze", "generate", "vu1gen", "build", "status", "run"))
    parser.add_argument("disc", nargs="?", choices=DISCS)
    args = parser.parse_args()
    private = args.private_root.resolve()
    # Inside the checkout only as the git-ignored private/ folder.
    if private == ROOT or (ROOT in private.parents and private != ROOT / "private" and ROOT / "private" not in private.parents):
        parser.error("private data root must be outside the source repository, or its private/ folder")
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    selected = [args.disc] if args.disc else list(DISCS)
    build = BUILD if args.legacy else private / "build/ga2-runtime"
    generated_root = private / ("generated" if args.legacy else "generated52")
    if args.command == "setup":
        if args.legacy:
            configure(private, args.jobs)
            run(["cmake", "--build", BUILD, "--target", "ps2_recomp", "ps2_analyzer", "ps2EntryRunner", "ps2x_tests",
                 "--parallel", args.jobs], private / "build-toolchain.log")
        else:
            # The recompiler and analyzer that generate/analyze use come from the same tree as the engines.
            configure_consolidated(private, generated_root)
            run(["cmake", "--build", build, "--target", "ps2_recomp", "ps2_analyzer", "--parallel", args.jobs],
                private / "build-toolchain.log")
    elif args.command == "import":
        cmd = [sys.executable, ROOT / "tools/import_discs.py", "--private-root", private]
        if args.disc:
            cmd += ["--disc", args.disc]
        run(cmd)
    elif args.command == "analyze":
        for disc in selected:
            analyze(private, disc, args.ghidra, build, args.ghidra_analysis)
    elif args.command == "generate":
        for disc in selected:
            generate(private, disc, build, generated_root)
    elif args.command == "vu1gen":
        vu1gen(private, build, generated_root)
    elif args.command == "build":
        for disc in selected:
            if not (generated_root / disc / "identity.json").exists():
                raise ValueError(f"{disc}: generate before building")
        if args.legacy:
            configure(private, args.jobs, selected)
        else:
            # Configure every generated disc so building one keeps the others' targets.
            configure_consolidated(private, generated_root)
        # The launcher (ga2) too, except in the legacy build, which has none.
        launcher = [] if args.legacy else ["ga2"]
        run(["cmake", "--build", build, "--target", *launcher, *[f"ga2_{d.replace('-', '_')}" for d in selected],
             "--parallel", args.jobs], private / "build-native.log")
    elif args.command == "status":
        status(private, build, generated_root)
    elif args.command == "run":
        if not args.disc:
            parser.error("run requires a disc ID")
        data, boot = manifest(private, args.disc)
        binary = build / "ga2/bin" / f"ga2_{args.disc.replace('-', '_')}"
        if not binary.exists():
            raise ValueError("build this disc first")
        identity_file = generated_root / args.disc / "identity.json"
        if identity_file.exists():
            # The binary must come from this exact executable (a stale or foreign generation would run silently).
            if json.loads(identity_file.read_text())["elf_sha256"] != data["boot"]["sha256"]:
                raise ValueError(f"{args.disc}: the generated code was made from a different executable; "
                                 "generate and build again")
        else:
            print(f"ga2: warning: {identity_file} is missing; cannot tie the binary to the imported executable",
                  file=sys.stderr)
        # The same arguments as tools/run_owned.py: boot disc, the other imported discs, exec targets, memory card.
        run([binary, boot, *disc_set_arguments(private, args.disc)])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"ga2: {error}", file=sys.stderr)
        raise SystemExit(1)
