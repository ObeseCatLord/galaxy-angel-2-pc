#!/usr/bin/env bash
# Builds ipu_stream_test against the runtime tree's IPU device and checks BDEC
# and IDEC decodes of FFmpeg-encoded MPEG-2 variants against FFmpeg's output.
set -euo pipefail
SOURCE="$(cd "$(dirname "$0")/../.." && pwd)"
. "$SOURCE/tools/private_root.sh"
LIB="${GA2_RUNTIME_TREE:-$PRIVATE/worktrees/ga2-runtime}/ps2xRuntime/src/lib"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
clang++ -std=c++20 -O1 -g -fsanitize=address,undefined -I"$LIB" \
    "$SOURCE/tests/native/ipu_stream_test.cpp" "$LIB/ps2_ipu.cpp" -o "$WORK/ipu_stream_test"
status=0
variant() {
    local name=$1 alt=$2; shift 2
    ffmpeg -loglevel error -y -f lavfi -i testsrc2=size=320x240:rate=25 -frames:v 3 \
        -c:v mpeg2video -g 1 -bf 0 "$@" -f mpeg2video "$WORK/$name.m2v"
    ffmpeg -loglevel error -y -i "$WORK/$name.m2v" -f rawvideo -pix_fmt yuv420p "$WORK/$name.yuv"
    for mode in bdec idec; do
        if out=$("$WORK/ipu_stream_test" "$WORK/$name.m2v" "$WORK/$name.yuv" 320 240 $mode $alt 2>/dev/null | tail -1); then
            echo "PASS $name $mode: $out"
        else
            echo "FAIL $name $mode: $out"; status=1
        fi
    done
}
variant base 0 -q:v 5
variant lowq 0 -q:v 2
variant intravlc 0 -q:v 3 -intra_vlc 1
variant dc10 0 -q:v 3 -dc 10
variant ildct 0 -q:v 3 -flags +ildct
variant altscan 1 -q:v 3 -alternate_scan 1
exit $status
