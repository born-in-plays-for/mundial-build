#!/usr/bin/env python3
"""Diff a minified/gzipped JSON file (e.g. under data/) against git history.

- File has uncommitted changes -> diff working tree vs HEAD.
- File matches HEAD            -> diff the last two commits that actually
  touched the file (not necessarily HEAD and HEAD~1 — those may not have
  changed this file at all).

Works for files inside the data/ submodule or the main repo — the git root
is resolved per-file, so it doesn't matter which repo the path is in.

Usage:
    python3 pipeline/json_diff.py data/v2/status.json
    python3 pipeline/json_diff.py --bc data/v2/status.json   # open in Beyond Compare
"""
import difflib
import gzip
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, check=True
    ).stdout


def pretty(data: bytes) -> str:
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return json.dumps(json.loads(data), indent=2, ensure_ascii=False) + "\n"


def open_in_beyond_compare(left_label: str, left: str, right_label: str, right: str) -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="json_diff_bc_"))
    left_path = tmpdir / left_label.replace("/", "_")
    right_path = tmpdir / right_label.replace("/", "_")
    left_path.write_text(left)
    right_path.write_text(right)
    subprocess.Popen(["bcompare", str(left_path), str(right_path)])


def main() -> None:
    args = sys.argv[1:]
    use_bc = "--bc" in args
    if use_bc:
        args.remove("--bc")
    if len(args) != 1:
        sys.exit(f"usage: {sys.argv[0]} [--bc] <path-to-json-file>")

    target = Path(args[0]).resolve()
    if not target.is_file():
        sys.exit(f"not a file: {target}")

    repo_root = Path(
        subprocess.run(
            ["git", "-C", str(target.parent), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    )
    relpath = target.relative_to(repo_root).as_posix()

    dirty = subprocess.run(
        ["git", "-C", str(repo_root), "diff", "--quiet", "--", relpath]
    ).returncode != 0

    try:
        if dirty:
            left_label, right_label = f"HEAD:{relpath}", f"working tree:{relpath}"
            left = pretty(git(repo_root, "show", f"HEAD:{relpath}"))
            right = pretty(target.read_bytes())
        else:
            commits = git(
                repo_root, "log", "-n2", "--format=%H", "--", relpath
            ).decode().split()
            if len(commits) < 2:
                sys.exit(f"only one commit touches {relpath}; nothing to diff")
            newer, older = commits
            left_label, right_label = f"{older[:8]}:{relpath}", f"{newer[:8]}:{relpath}"
            left = pretty(git(repo_root, "show", f"{older}:{relpath}"))
            right = pretty(git(repo_root, "show", f"{newer}:{relpath}"))
    except subprocess.CalledProcessError as e:
        sys.exit(f"git command failed: {e.stderr.decode().strip()}")

    print(f"# {'working tree differs from HEAD' if dirty else f'{left_label} -> {right_label}'}", file=sys.stderr)

    if use_bc:
        open_in_beyond_compare(left_label, left, right_label, right)
        return

    diff = difflib.unified_diff(
        left.splitlines(keepends=True),
        right.splitlines(keepends=True),
        fromfile=left_label,
        tofile=right_label,
    )
    sys.stdout.writelines(diff)


if __name__ == "__main__":
    main()
