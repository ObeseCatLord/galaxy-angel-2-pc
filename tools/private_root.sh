# Sourced by the shell tools: sets PRIVATE, the folder for disc images,
# extracted files, generated code and builds (never committed).
# $GA2_PRIVATE_ROOT if set; else galaxy-angel-ii-private beside the folder
# holding this checkout, if that exists (older layout); else private/ inside
# the checkout (git-ignored).
if [ -n "${GA2_PRIVATE_ROOT:-}" ]; then
    PRIVATE="$GA2_PRIVATE_ROOT"
elif [ -d "$SOURCE/../../galaxy-angel-ii-private" ]; then
    PRIVATE="$(cd "$SOURCE/../.." && pwd)/galaxy-angel-ii-private"
else
    PRIVATE="$SOURCE/private"
fi
