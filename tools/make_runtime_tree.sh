#!/usr/bin/env bash
# Recreate the consolidated runtime tree: a private clone of the pinned
# PS2Recomp with every patch in patches/ applied as one commit each, on branch
# "ga2". Development happens there; export new work back with
#   git -C <tree> format-patch --start-number <N> -o patches/ <last-recorded>..HEAD
set -euo pipefail
SOURCE="$(cd "$(dirname "$0")/.." && pwd)"
. "$SOURCE/tools/private_root.sh"
TREE="${1:-$PRIVATE/worktrees/ga2-runtime}"
PIN=2c5fbb9389e11dd95693385969490c9e8e6f57b4
if [ -e "$TREE" ]; then echo "refusing to overwrite existing $TREE" >&2; exit 1; fi
git clone -q --no-checkout "$SOURCE/third_party/PS2Recomp" "$TREE"
git -C "$TREE" checkout -q -b ga2 "$PIN"
for patch in "$SOURCE"/patches/0*.patch; do
    git -C "$TREE" apply --index "$patch"
    # A user's commit.gpgsign / hooks must not make recreating the tree prompt or fail.
    git -C "$TREE" -c user.name=ga2 -c user.email=ga2@localhost -c commit.gpgsign=false -c core.hooksPath=/dev/null \
        commit -q --no-verify -m "$(basename "$patch" .patch)"
done
echo "runtime tree ready: $TREE ($(git -C "$TREE" rev-list --count "$PIN"..HEAD) patches)"
