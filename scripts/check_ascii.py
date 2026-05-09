#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

EXCLUDE_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules"}


def iter_files(root: Path):
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        if any(part in EXCLUDE_DIRS for part in path.parts):
            continue
        yield path


def main() -> int:
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    bad: list[str] = []
    for file in iter_files(root):
        try:
            data = file.read_bytes()
        except Exception:
            continue
        if any(b > 127 for b in data):
            bad.append(str(file.relative_to(root)))
    if bad:
        print("Non-ASCII bytes found:")
        for f in bad:
            print(f" - {f}")
        return 1
    print("ASCII check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
