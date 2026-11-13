#!/usr/bin/env bash
# Builds the installer (native/installer) for one platform and copies it to <out-file>.
#   build-installer.sh linux|windows <out-file>
# windows cross-compiles with tools/mingw-w64-toolchain.cmake (GA2_LLVM_MINGW=<llvm-mingw> on CI). Extra CMake
# arguments come from $GA2_INSTALLER_CMAKE_ARGS (e.g. local FETCHCONTENT_SOURCE_DIR_* copies);
# the build folder from $GA2_INSTALLER_BUILD.
set -euo pipefail
SOURCE="$(cd "$(dirname "$0")/../.." && pwd)"
TARGET="$1"
OUT="$2"
BUILD="${GA2_INSTALLER_BUILD:-$SOURCE/build/installer-$TARGET}"
ARGS=(-DCMAKE_BUILD_TYPE=Release)
[ "$TARGET" = windows ] && ARGS+=("-DCMAKE_TOOLCHAIN_FILE=$SOURCE/tools/mingw-w64-toolchain.cmake")
[ -n "${GA2_INSTALLER_REPO:-}" ] && ARGS+=("-DGA2_INSTALLER_REPO=$GA2_INSTALLER_REPO")
GENERATOR=()
command -v ninja > /dev/null && GENERATOR=(-G Ninja)
# shellcheck disable=SC2086
cmake -S "$SOURCE/native/installer" -B "$BUILD" "${GENERATOR[@]}" "${ARGS[@]}" ${GA2_INSTALLER_CMAKE_ARGS:-}
cmake --build "$BUILD" --parallel "${GA2_JOBS:-$(nproc)}"
mkdir -p "$(dirname "$OUT")"
if [ "$TARGET" = windows ]; then
    cp "$BUILD/ga2-installer.exe" "$OUT"
    STRIP="${GA2_LLVM_MINGW:+$GA2_LLVM_MINGW/bin/}llvm-strip"
    command -v "$STRIP" > /dev/null || STRIP=x86_64-w64-mingw32-strip
else
    cp "$BUILD/ga2-installer" "$OUT"
    STRIP=strip
fi
"$STRIP" "$OUT"
ls -l "$OUT"
