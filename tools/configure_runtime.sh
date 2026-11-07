#!/usr/bin/env bash
# Configure the consolidated GA2 runtime tree (private PS2Recomp clone on branch
# "ga2") with the native game targets attached. Reuses dependency checkouts from
# an earlier build when present so configuration works offline.
set -euo pipefail
SOURCE="$(cd "$(dirname "$0")/.." && pwd)"
. "$SOURCE/tools/private_root.sh"
TREE="${GA2_RUNTIME_TREE:-$PRIVATE/worktrees/ga2-runtime}"
BUILD="${GA2_RUNTIME_BUILD:-$PRIVATE/build/ga2-runtime}"
GENERATED="${GA2_GENERATED_ROOT:-$PRIVATE/generated52}"
DISCS="${GA2_DISC_IDS:-zettai}"
DEPS="${GA2_DEPS:-$SOURCE/build/ps2recomp/_deps}"

[ -d "$TREE" ] || { echo "configure_runtime: runtime tree $TREE is missing (create it with tools/make_runtime_tree.sh)" >&2; exit 1; }
PGS="${GA2_PGS_DIR:-$PRIVATE/third_party/parallel-gs}"
[ -d "$PGS" ] || { echo "configure_runtime: paraLLEl-GS $PGS is missing (fetch it with tools/fetch_parallel_gs.sh)" >&2; exit 1; }
# Dependencies: offline checkouts in $DEPS (<name>-src, from an earlier build)
# when all are there, else CMake downloads them.
DEP_ARGS=(-DFETCHCONTENT_FULLY_DISCONNECTED=ON)
for dep in elfio fmt imgui libdwarf nlohmann_json rabbitizer raylib rlimgui toml11; do
    if [ ! -d "$DEPS/$dep-src" ]; then DEP_ARGS=(); break; fi
    DEP_ARGS+=("-DFETCHCONTENT_SOURCE_DIR_$(echo "$dep" | tr a-z A-Z)=$DEPS/$dep-src")
done

mkdir -p "$BUILD"
"${GA2_CMAKE:-cmake}" -S "$TREE" -B "$BUILD" -G "${GA2_CMAKE_GENERATOR:-Ninja}" \
    -DCMAKE_BUILD_TYPE=RelWithDebInfo -DPS2X_RUNTIME_SHARED=ON \
    ${GA2_COMPILERS:--DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++} \
    -DPS2X_BUILD_STUDIO=OFF -DPS2X_BUILD_TEST=ON -DPS2X_IOP_BUILD_TESTS=ON \
    -DPS2X_ENABLE_AGRESSIVE_LOGS=OFF \
    "-DCMAKE_PROJECT_PS2RetroX_INCLUDE=$SOURCE/cmake/AttachNativeTargets.cmake" \
    "-DGA2_PRIVATE_ROOT=$PRIVATE" "-DGA2_GENERATED_ROOT=$GENERATED" "-DGA2_DISC_IDS=$DISCS" \
    "${DEP_ARGS[@]}" \
    "-DPS2X_PGS_DIR=$PGS" \
    "$@"
