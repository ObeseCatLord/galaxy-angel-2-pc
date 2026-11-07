"""Apply a known ordered patch stack while preserving other working-tree edits."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def _numstat_paths(patch: Path) -> list[str]:
    """Paths a patch touches.

    `git apply --numstat -z` names only the destination of a rename or copy, so
    the source is read from the patch's own "rename from" / "copy from" lines.
    """
    stats = subprocess.check_output(["git", "apply", "--numstat", "-z", str(patch)])
    names = []
    for row in stats.split(b"\0"):
        if not row:
            continue
        parts = row.split(b"\t", 2)
        if len(parts) != 3 or not parts[2]:
            raise ValueError(f"unexpected numstat record in {patch.name}")
        names.append(parts[2].decode())
    for line in patch.read_text(errors="replace").splitlines():
        for prefix in ("rename from ", "copy from "):
            if line.startswith(prefix):
                names.append(line[len(prefix):])
    return names


def patch_paths(upstream: Path, patches: list[Path]) -> set[Path]:
    paths = set()
    for patch in patches:
        for name in _numstat_paths(patch):
            path = Path(name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"patch path outside upstream: {name}")
            source = upstream / path
            if not source.resolve().is_relative_to(upstream):
                raise ValueError(f"patch path resolves outside upstream: {name}")
            paths.add(path)
    return paths


def unrelated_changes(upstream: Path, patches: list[Path]) -> list[str]:
    """Modified or untracked files in the upstream checkout that no patch of the stack touches."""
    upstream = upstream.resolve()
    allowed = {path.as_posix() for path in patch_paths(upstream, [p.resolve() for p in patches])}
    status = subprocess.check_output(["git", "-C", str(upstream), "status", "--porcelain", "-z",
                                      "--untracked-files=all"]).split(b"\0")
    stray = []
    i = 0
    while i < len(status):
        entry = status[i].decode(errors="replace")
        i += 1
        if not entry:
            continue
        code, name = entry[:2], entry[3:]
        if code[0] in "RC":  # renamed in the index: the next record is the old name
            i += 1
        if name not in allowed:
            stray.append(name)
    return sorted(stray)


def ensure_patch_stack(upstream: Path, patches: list[Path]) -> int:
    upstream = upstream.resolve()
    patches = [p.resolve() for p in patches]
    if not patches:
        return 0
    paths = patch_paths(upstream, patches)

    def apply(directory, patch, reverse=False, check=False):
        command = ["git", "-C", str(directory), "apply"]
        if reverse:
            command.append("--reverse")
        if check:
            command.append("--check")
        command.append(str(patch))
        env = None
        if directory != upstream:
            # A private snapshot lives outside any repository even when TMPDIR is
            # inside a work tree: git must not discover that tree and resolve the
            # patch paths against it.
            env = dict(os.environ, GIT_CEILING_DIRECTORIES=str(Path(directory).resolve().parent))
        return subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env).returncode == 0

    # Later patches can change earlier context, so reverse the entire candidate
    # prefix in a private snapshot. Never probe by undoing the user's real tree.
    applied = None
    for prefix in range(len(patches), -1, -1):
        with tempfile.TemporaryDirectory(prefix="ga2-patch-state-") as directory:
            snapshot = Path(directory)
            for path in paths:
                source = upstream / path
                if source.exists():
                    if not source.is_file():
                        raise ValueError(f"patch target is not a regular file: {path}")
                    target = snapshot / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
            valid = True
            for patch in reversed(patches[:prefix]):
                if not apply(snapshot, patch, reverse=True, check=True) or not apply(snapshot, patch, reverse=True):
                    valid = False
                    break
            if valid:
                for patch in patches:
                    if not apply(snapshot, patch, check=True) or not apply(snapshot, patch):
                        valid = False
                        break
            if valid:
                applied = prefix
                break
    if applied is None:
        raise ValueError("local upstream changes do not match an applicable patch-stack prefix; preserved all files")
    for patch in patches[applied:]:
        # Recheck the actual tree in case it changed during snapshot validation.
        subprocess.run(["git", "-C", str(upstream), "apply", "--check", str(patch)], check=True)
        subprocess.run(["git", "-C", str(upstream), "apply", str(patch)], check=True)
    return applied
