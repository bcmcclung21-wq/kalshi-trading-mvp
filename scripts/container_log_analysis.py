#!/usr/bin/env python3
"""Container log analyzer for trading service health.

Usage:
  cat container.log | python3 scripts/container_log_analysis.py
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

LOG_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z)\s+\[(\w+)\]\s+(.*)")


@dataclass(frozen=True)
class Entry:
    ts: datetime
    lvl: str
    msg: str


def parse_entries(lines: Iterable[str]) -> list[Entry]:
    parsed: list[Entry] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        match = LOG_RE.match(line)
        if not match:
            continue
        ts = datetime.strptime(match.group(1)[:19], "%Y-%m-%dT%H:%M:%S")
        parsed.append(Entry(ts=ts, lvl=match.group(2), msg=match.group(3).strip()))
    return parsed


def extract_active(universe_msgs: list[Entry]) -> list[int]:
    active: list[int] = []
    for entry in universe_msgs:
        match = re.search(r"active=(\d+)", entry.msg)
        if match:
            active.append(int(match.group(1)))
    return active


def main() -> int:
    entries = parse_entries(sys.stdin)
    if not entries:
        print("No parseable log entries found.")
        return 1

    fetch = [entry for entry in entries if "fetch_raw_complete" in entry.msg]
    universe = [entry for entry in entries if "universe_refresh_complete" in entry.msg]
    skipped = [entry for entry in entries if "positions_fetch_skipped" in entry.msg]
    active = extract_active(universe)

    print(f"Duration: {entries[-1].ts - entries[0].ts}")
    print(f"Cycles:   {len(fetch)}")

    total_position_cycles = len(skipped) + len(fetch)
    skipped_pct = (len(skipped) / total_position_cycles * 100) if total_position_cycles else 0
    print(f"Skipped:  {len(skipped)} / {total_position_cycles} ({skipped_pct:.0f}%)")

    if active:
        delta = active[-1] - active[0]
        print(f"Active:   {active[0]} → {active[-1]} (Δ{delta})")
    else:
        print("Active:   N/A (no universe_refresh_complete with active=...)" )

    for fetch_entry, universe_entry in zip(fetch, universe):
        duration_seconds = (universe_entry.ts - fetch_entry.ts).total_seconds()
        if duration_seconds > 5:
            stamp = fetch_entry.ts.strftime("%H:%M:%S")
            print(f"SLOW: {stamp} took {duration_seconds:.0f}s")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
