#!/usr/bin/env bash
# Fetch paraLLEl-GS (the GPU GS renderer) at the pinned commit and apply the
# port's commits from patches/parallel-gs. Default destination:
# <private>/third_party/parallel-gs, where tools/configure_runtime.sh looks.
set -euo pipefail
SOURCE="$(cd "$(dirname "$0")/.." && pwd)"
. "$SOURCE/tools/private_root.sh"
DEST="${1:-$PRIVATE/third_party/parallel-gs}"
URL="${GA2_PGS_URL:-https://github.com/z3xox/parallel-gs}"
PIN=42c6701e929686f29273cbaa9e4fd4feb4c1fc22   # branch bt3-texreplace
if [ -e "$DEST" ]; then echo "refusing to overwrite existing $DEST" >&2; exit 1; fi
git clone -q "$URL" "$DEST"
git -C "$DEST" checkout -q -b ga2 "$PIN"
git -C "$DEST" submodule update -q --init --recursive
git -C "$DEST" -c user.name=ga2 -c user.email=ga2@localhost -c commit.gpgsign=false -c core.hooksPath=/dev/null \
    am -q "$SOURCE"/patches/parallel-gs/*.patch
echo "paraLLEl-GS ready: $DEST"
