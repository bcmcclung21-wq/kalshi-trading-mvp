#!/usr/bin/env python3
"""Container log analyzer for trading service health.

Usage:
  cat container.log | python3 scripts/container_log_analysis.py
  python3 scripts/container_log_analysis.py --current /tmp/container.log --previous /tmp/container_prev.log
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze container logs for cycle health and skips.")
    parser.add_argument("--current", type=Path, help="Path to current session log file.")
    parser.add_argument("--previous", type=Path, help="Path to previous session log for comparison.")
    parser.add_argument(
        "--alert-active-drop-pct",
        type=float,
        default=5.0,
        help="Emit alert when active drops more than this percent between latest two snapshots.",
    )
    parser.add_argument(
        "--alert-skip-count",
        type=int,
        default=10,
        help="Emit alert when skipped position fetch count exceeds this threshold.",
    )
    return parser.parse_args()


def read_entries_from_source(current: Path | None) -> list[Entry]:
    if current:
        return parse_entries(current.read_text().splitlines())
    return parse_entries(sys.stdin)


def print_snapshot(entries: list[Entry]) -> None:
    fetch = [entry for entry in entries if "fetch_raw_complete" in entry.msg]
    universe = [entry for entry in entries if "universe_refresh_complete" in entry.msg]
    skipped = [entry for entry in entries if "positions_fetch_skipped" in entry.msg]
    active = extract_active(universe)

    print("=" * 50)
    print("SNAPSHOT ANALYSIS")
    print("=" * 50)
    print(f"Duration:  {entries[-1].ts - entries[0].ts}")
    print(f"Cycles:    {len(fetch)}")

    total_position_cycles = len(skipped) + len(fetch)
    skipped_pct = (len(skipped) / total_position_cycles * 100) if total_position_cycles else 0
    print(f"Skipped:   {len(skipped)} / {total_position_cycles} ({skipped_pct:.0f}%)")

    if active:
        delta = active[-1] - active[0]
        pct = (delta / active[0] * 100) if active[0] else 0
        print(f"Active:    {active[0]} → {active[-1]} (Δ{delta}, {pct:+.1f}%)")
    else:
        print("Active:    N/A (no universe_refresh_complete with active=...)")

    print("\n--- CYCLE BREAKDOWN ---")
    for idx, (fetch_entry, universe_entry) in enumerate(zip(fetch, universe), start=1):
        duration_seconds = (universe_entry.ts - fetch_entry.ts).total_seconds()
        active_match = re.search(r"active=(\d+)", universe_entry.msg)
        active_count = active_match.group(1) if active_match else "N/A"
        print(f"  #{idx}: {fetch_entry.ts.strftime('%H:%M:%S')} | active={active_count} | proc={duration_seconds:.0f}s")

    print("\n--- SLOW CYCLES (>5s) ---")
    slow = False
    for fetch_entry, universe_entry in zip(fetch, universe):
        duration_seconds = (universe_entry.ts - fetch_entry.ts).total_seconds()
        if duration_seconds > 5:
            slow = True
            print(f"  {fetch_entry.ts.strftime('%H:%M:%S')}: {duration_seconds:.0f}s")
    if not slow:
        print("  none")

    print("\n--- SKIP TIMESTAMPS ---")
    if skipped:
        for entry in skipped:
            print(f"  {entry.ts.strftime('%H:%M:%S')}")
    else:
        print("  none")


def print_comparison(current: Path, previous: Path) -> None:
    previous_entries = parse_entries(previous.read_text().splitlines())
    current_entries = parse_entries(current.read_text().splitlines())
    previous_active = extract_active([entry for entry in previous_entries if "universe_refresh_complete" in entry.msg])
    current_active = extract_active([entry for entry in current_entries if "universe_refresh_complete" in entry.msg])

    if not previous_active or not current_active:
        print("\n--- CROSS-SESSION COMPARISON ---")
        print("  Insufficient active=... points in one or both files.")
        return

    print("\n--- CROSS-SESSION COMPARISON ---")
    print(f"  Previous: {previous_active[0]} → {previous_active[-1]} ({len(previous_active)} cycles)")
    print(f"  Current:  {current_active[0]} → {current_active[-1]} ({len(current_active)} cycles)")
    print(
        "  Trend:    active baseline dropped by "
        f"{previous_active[0] - current_active[0]} assets session-over-session"
    )


def print_alerts(entries: list[Entry], active_drop_threshold: float, skip_count_threshold: int) -> None:
    universe = [entry for entry in entries if "universe_refresh_complete" in entry.msg]
    skipped = [entry for entry in entries if "positions_fetch_skipped" in entry.msg]
    active = extract_active(universe)

    print("\n--- ALERT CHECK ---")
    if len(active) >= 2:
        drop = (active[-2] - active[-1]) / active[-2] * 100 if active[-2] else 0
        if drop > active_drop_threshold:
            print(f"  ALERT: universe dropped {drop:.1f}%")
        else:
            print(f"  OK: latest active delta {drop:.1f}%")
    else:
        print("  OK: insufficient active points for drop alert")

    if len(skipped) > skip_count_threshold:
        print(f"  ALERT: {len(skipped)} position skips detected")
    else:
        print(f"  OK: {len(skipped)} position skips")


def main() -> int:
    args = parse_args()
    entries = read_entries_from_source(args.current)
    if not entries:
        print("No parseable log entries found.")
        return 1

    print_snapshot(entries)
    print_alerts(entries, args.alert_active_drop_pct, args.alert_skip_count)

    if args.current and args.previous and args.previous.exists():
        print_comparison(args.current, args.previous)
    elif args.previous:
        print("\n--- CROSS-SESSION COMPARISON ---")
        print(f"  [{args.previous} not found]")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
