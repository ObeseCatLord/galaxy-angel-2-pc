#!/usr/bin/env bash
# A static, position-independent FFmpeg with only what the port uses (the
# MPEG-2 decoder, scaler and resampler), so release builds need no system
# FFmpeg.   ffmpeg-static.sh <prefix> [C compiler]
set -euo pipefail
PREFIX="$1"; CC="${2:-gcc}"
VERSION=7.1.1
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
curl -sSfL "https://ffmpeg.org/releases/ffmpeg-$VERSION.tar.xz" | tar -xJ -C "$WORK"
cd "$WORK/ffmpeg-$VERSION"
# x86 assembly (inline and external) is not position-independent, and this
# goes into the shared runtime library; the 640x448 MPEG-2 movies decode fast
# enough in C.
ASM=""
case "$(uname -m)" in x86_64|i?86) ASM="--disable-asm" ;; esac
./configure $ASM --prefix="$PREFIX" --cc="$CC" --enable-static --disable-shared --enable-pic \
    --disable-programs --disable-doc --disable-network --disable-autodetect --disable-everything \
    --enable-decoder=mpeg2video --enable-parser=mpegvideo --enable-avformat --enable-avcodec \
    --enable-swscale --enable-swresample > configure.out
make -j"$(nproc)" > /dev/null
make install > /dev/null
