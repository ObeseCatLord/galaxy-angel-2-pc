#!/usr/bin/env bash
# Release build for Linux (x86-64 or ARM64): clang (GA2_CC/GA2_CXX, default
# clang-18) with GCC 13's libstdc++ and FFmpeg linked statically, so the
# result needs only glibc and libGL of the system it was built on (Ubuntu
# 20.04: glibc 2.31).
# Needs a prepared private root (tools/ga2.py import and generate, as in the
# README's build steps).
#   build-linux-release.sh <out-dir>
set -euo pipefail
SOURCE="$(cd "$(dirname "$0")/../.." && pwd)"
. "$SOURCE/tools/private_root.sh"
OUT="$1"
FFMPEG="${GA2_FFMPEG_PREFIX:-$PRIVATE/ffmpeg-static}"
[ -f "$FFMPEG/lib/libavcodec.a" ] || "$SOURCE/tools/ci/ffmpeg-static.sh" "$FFMPEG" gcc-13
[ -d "$PRIVATE/worktrees/ga2-runtime" ] || "$SOURCE/tools/make_runtime_tree.sh"
[ -d "$PRIVATE/third_party/parallel-gs" ] || "$SOURCE/tools/fetch_parallel_gs.sh"
export GA2_COMPILERS="-DCMAKE_C_COMPILER=${GA2_CC:-clang-18} -DCMAKE_CXX_COMPILER=${GA2_CXX:-clang++-18}"
export GA2_DISC_IDS="zettai;mugen-1;mugen-2;eigou-1;eigou-2"
export GA2_RUNTIME_BUILD="${GA2_RUNTIME_BUILD:-$PRIVATE/build/release}"
export PKG_CONFIG_PATH="$FFMPEG/lib/pkgconfig"
# --as-needed drops libraries nothing calls (raylib links libGLU, the runtime
# libatomic), which a minimal system or SteamOS outside the Steam runtime lacks.
STATIC="-static-libstdc++ -static-libgcc -Wl,--as-needed"
# -O2 as in the tested builds (-O3 needs much more memory).
"$SOURCE/tools/configure_runtime.sh" -DCMAKE_BUILD_TYPE=Release "-DCMAKE_CXX_FLAGS_RELEASE=-O2 -DNDEBUG" \
    "-DCMAKE_C_FLAGS_RELEASE=-O2 -DNDEBUG" -DPS2X_BUILD_TEST=OFF -DPS2X_IOP_BUILD_TESTS=OFF \
    -DPKG_CONFIG_ARGN=--static -DPS2X_LINK_DEPENDS_SHARED=ON \
    "-DCMAKE_EXE_LINKER_FLAGS=$STATIC" "-DCMAKE_SHARED_LINKER_FLAGS=$STATIC"
# The generated VU1 programs are a few huge files: fewer at once, or GCC runs out of memory.
cmake --build "$GA2_RUNTIME_BUILD" --target ga2_vu1_programs --parallel "${GA2_BIG_JOBS:-2}"
cmake --build "$GA2_RUNTIME_BUILD" --target ga2 ga2_zettai ga2_mugen_1 ga2_mugen_2 ga2_eigou_1 ga2_eigou_2 \
    --parallel "${GA2_JOBS:-$(nproc)}"
python3 "$SOURCE/tools/package.py" "$GA2_RUNTIME_BUILD" "$OUT"
strip --strip-debug "$OUT"/ga2 "$OUT"/ga2_* "$OUT"/libps2_runtime.so
# Report what the binaries need: the newest glibc symbol version and the libraries.
objdump -T "$OUT"/ga2 "$OUT"/ga2_* "$OUT"/libps2_runtime.so | grep -oE 'GLIBC_[0-9.]+' | sort -uV | tail -1
readelf -d "$OUT"/ga2 "$OUT"/ga2_* "$OUT"/libps2_runtime.so | grep NEEDED | sort -u
