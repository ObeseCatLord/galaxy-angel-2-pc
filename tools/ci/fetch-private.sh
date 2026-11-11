#!/usr/bin/env bash
# Release workflow: unpack the private build inputs into the private root.
# Every *.tar.zst of the repository $GA2_PRIVATE_REPO (owner/name, read with
# the SSH deploy key in $GA2_PRIVATE_DEPLOY_KEY) is extracted into $1.
#   fetch-private.sh <private-root>
set -euo pipefail
: "${GA2_PRIVATE_REPO:?}" "${GA2_PRIVATE_DEPLOY_KEY:?}"
DEST="$1"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$DEST"
# Explicit key and known-hosts paths: in a container, ssh's home is not $HOME.
umask 077
printf '%s\n' "$GA2_PRIVATE_DEPLOY_KEY" > "$WORK/key"
ssh-keyscan github.com > "$WORK/known_hosts" 2>/dev/null
GIT_SSH_COMMAND="ssh -i $WORK/key -o UserKnownHostsFile=$WORK/known_hosts -o IdentitiesOnly=yes" \
    git clone -q --depth 1 "git@github.com:$GA2_PRIVATE_REPO.git" "$WORK/repo"
for archive in "$WORK"/repo/*.tar.zst; do
    tar -I zstd -xf "$archive" -C "$DEST"
done
