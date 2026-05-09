#!/usr/bin/env python3
"""Fail if any tracked Python/text file contains non-ASCII bytes.

iOS auto-correct can substitute curly quotes, em-dashes, and other
non-ASCII characters when files are edited or pasted on iPhone. This
script is the last line of defense before such characters reach
production. Run before every push.

Exit code 0 = clean. Exit code 1 = non-ASCII found.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ["app", "alembic", "tests", "scripts"]
SUFFIXES = {".py", ".md", ".txt", ".yaml", ".yml", ".ini", ".toml"}


def scan_file(path: Path) -> list[tuple[int, int, int, str]]:
    out: list[tuple[int, int, int, str]] = []
    try:
        data = path.read_bytes()
    except OSError:
        return out
    line_no = 1
    col = 0
    line_start = 0
    for i, b in enumerate(data):
        if b == 0x0A:
            line_no += 1
            col = 0
            line_start = i + 1
            continue
        col += 1
        if b > 127:
            line_end = data.find(b"\n", i)
            if line_end < 0:
                line_end = len(data)
            line_text = data[line_start:line_end].decode("utf-8", errors="replace")
            out.append((line_no, col, b, line_text))
    return out


def main() -> int:
    bad = 0
    for d in SCAN_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in SUFFIXES:
                continue
            for line_no, col, byte_val, line_text in scan_file(path):
                rel = path.relative_to(ROOT)
                print(f"{rel}:{line_no}:{col}: non-ASCII byte 0x{byte_val:02x} in: {line_text.strip()[:120]}")
                bad += 1
    if bad:
        print(f"\nFAIL: {bad} non-ASCII byte(s) found. Likely iOS smart-quote corruption.", file=sys.stderr)
        return 1
    print("OK: all scanned files are pure ASCII.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
