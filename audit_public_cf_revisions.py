#!/usr/bin/env python3
"""Compare values captured live with the endpoint's later historical rendering."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict

import requests

from analyze_public_cf_live import REST, rows_from_open_gzip, time_ms


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("capture", type=Path)
    p.add_argument("--tolerance", type=float, default=1e-9)
    p.add_argument("--output", type=Path, default=Path("data/cf_exact/live_revision_audit.json"))
    args = p.parse_args()

    captured: Dict[str, Dict[int, float]] = defaultdict(dict)
    metadata: Dict[str, Dict[str, Any]] = {}
    for row in rows_from_open_gzip(args.capture):
        if row.get("kind") != "poll":
            continue
        event = row["event_ticker"]
        metadata[event] = row.get("book") or {}
        for point in row.get("points") or []:
            # Preserve the first value actually observed for each event timestamp.
            captured[event].setdefault(int(point["t"]), float(point["v"]))

    session = requests.Session()
    events = {}
    for event in sorted(captured):
        response = session.get(f"{REST}/live_data/events/{event}", timeout=20)
        if response.status_code != 200:
            events[event] = {"http_status": response.status_code}
            continue
        current = {
            int(point["t"]): float(point["v"])
            for point in response.json()["live_data"]["details"]["timeseries"]
        }
        changes = []
        close_ms = None
        try:
            close_ms = time_ms(metadata[event]["close_time"])
        except (KeyError, TypeError, ValueError):
            pass
        matched = 0
        for timestamp, old in captured[event].items():
            if timestamp not in current:
                continue
            matched += 1
            delta = current[timestamp] - old
            if abs(delta) <= args.tolerance:
                continue
            changes.append(
                {
                    "timestamp_ms": timestamp,
                    "live_value": old,
                    "historical_value": current[timestamp],
                    "delta": delta,
                    "is_close_boundary": timestamp == close_ms,
                }
            )
        events[event] = {
            "captured_points": len(captured[event]),
            "matched_points": matched,
            "changed_points": len(changes),
            "non_close_changes": sum(not row["is_close_boundary"] for row in changes),
            "max_abs_change": max((abs(row["delta"]) for row in changes), default=0.0),
            "changes": changes[:100],
        }

    report = {
        "capture": str(args.capture),
        "tolerance": args.tolerance,
        "events": events,
        "totals": {
            "events": len(events),
            "matched_points": sum(row.get("matched_points", 0) for row in events.values()),
            "changed_points": sum(row.get("changed_points", 0) for row in events.values()),
            "non_close_changes": sum(row.get("non_close_changes", 0) for row in events.values()),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
