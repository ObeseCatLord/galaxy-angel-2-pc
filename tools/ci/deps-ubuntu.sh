#!/usr/bin/env bash
# Build dependencies on Ubuntu (20.04 for release builds, 24.04 for CI), run as root.
#   deps-ubuntu.sh release   gcc-13 from the toolchain PPA, CMake/Ninja from pip (Ubuntu 20.04)
#   deps-ubuntu.sh ci        clang, CMake, Ninja and FFmpeg from the distribution
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
COMMON="git curl ca-certificates xz-utils zstd pkg-config python3 python3-pip make openssh-client
  libx11-dev libxrandr-dev libxinerama-dev libxcursor-dev libxi-dev libxext-dev libgl-dev libglu1-mesa-dev"
if [ "${1:-ci}" = release ]; then
    apt-get install -y -q software-properties-common
    add-apt-repository -y ppa:ubuntu-toolchain-r/test
    apt-get update -q
    apt-get install -y -q $COMMON gcc-13 g++-13 nasm
    python3 -m pip install -q --upgrade pip
    python3 -m pip install -q "cmake>=3.28,<4" ninja
else
    apt-get install -y -q $COMMON clang lld cmake ninja-build \
        libavcodec-dev libavformat-dev libavutil-dev libswscale-dev libswresample-dev
fi
