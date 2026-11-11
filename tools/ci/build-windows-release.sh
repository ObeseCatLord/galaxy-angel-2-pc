#!/usr/bin/env bash
# Release build for Windows x86-64, cross-compiled on Linux with llvm-mingw
# (GA2_LLVM_MINGW=<dir>; clang, libc++ and current mingw-w64 headers), linked
# statically apart from the FFmpeg DLLs shipped next to the executables.
# Needs a prepared private root (tools/ga2.py import and generate, as in the
# README's build steps).
#   build-windows-release.sh <out-dir>
set -euo pipefail
SOURCE="$(cd "$(dirname "$0")/../.." && pwd)"
. "$SOURCE/tools/private_root.sh"
OUT="$1"
: "${GA2_LLVM_MINGW:?set GA2_LLVM_MINGW to an llvm-mingw release folder}"
[ -d "$PRIVATE/worktrees/ga2-runtime" ] || "$SOURCE/tools/make_runtime_tree.sh"
[ -d "$PRIVATE/third_party/parallel-gs" ] || "$SOURCE/tools/fetch_parallel_gs.sh"
export GA2_COMPILERS=" " GA2_DISC_IDS="zettai;mugen-1;mugen-2;eigou-1;eigou-2"
export GA2_RUNTIME_BUILD="${GA2_RUNTIME_BUILD:-$PRIVATE/build/windows}"
"$SOURCE/tools/configure_runtime.sh" -DCMAKE_TOOLCHAIN_FILE="$SOURCE/tools/mingw-w64-toolchain.cmake" \
    -DPS2X_RUNTIME_SHARED=OFF -DPS2X_BUILD_TEST=OFF -DPS2X_IOP_BUILD_TESTS=OFF \
    -DCMAKE_BUILD_TYPE=Release "-DCMAKE_CXX_FLAGS_RELEASE=-O2 -DNDEBUG" "-DCMAKE_C_FLAGS_RELEASE=-O2 -DNDEBUG"
cmake --build "$GA2_RUNTIME_BUILD" --target ga2 ga2_zettai ga2_mugen_1 ga2_mugen_2 ga2_eigou_1 ga2_eigou_2 \
    --parallel "${GA2_JOBS:-$(nproc)}"
python3 "$SOURCE/tools/package.py" "$GA2_RUNTIME_BUILD" "$OUT"
"$GA2_LLVM_MINGW/bin/llvm-strip" --strip-debug "$OUT"/ga2.exe "$OUT"/ga2_*.exe
# What the executables import (system DLLs and the shipped FFmpeg ones).
"$GA2_LLVM_MINGW/bin/llvm-objdump" -p "$OUT"/ga2_zettai.exe | grep "DLL Name" | sort -u
