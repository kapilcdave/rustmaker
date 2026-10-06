"""Analyze PREREG_multiasset_tail_duplicate_live_20261006.md."""
from __future__ import annotations

import argparse
import gzip
import json
import math
from pathlib import Path

try:
    from idea_lab.analyze_public_hourly_dominance_orderbook import ask
except ModuleNotFoundError:
    from analyze_public_hourly_dominance_orderbook import ask


def fee(price: float) -> float:
    return math.ceil(0.07 * price * (1.0 - price) * 100.0 - 1e-9) / 100.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "journal",
        nargs="?",
        default="idea_lab/public_tail_duplicate_orderbook_20261006.jsonl.gz",
    )
    ap.add_argument(
        "--output",
        default="idea_lab/public_tail_duplicate_orderbook_report.json",
    )
    args = ap.parse_args()
    rows = []
    with gzip.open(args.journal, "rt", encoding="utf-8") as fh:
        try:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except EOFError:
            pass
    first = {}
    maximum = {}
    valid = 0
    paired_book_snapshots = 0
    identical_top_snapshots = 0
    for row in rows:
        meta, responses = row["meta"], row["responses"]
        if any(responses[leg].get("book") is None for leg in ("left", "right")):
            continue
        starts = [responses[leg]["request_start_ns"] for leg in ("left", "right")]
        finishes = [responses[leg]["request_finish_ns"] for leg in ("left", "right")]
        span_ms = (max(finishes) - min(starts)) / 1e6
        if span_ms > 750 or max(finishes) >= meta["close_ts"] * 1_000_000_000:
            continue
        left, right = (responses[leg]["book"] for leg in ("left", "right"))
        paired_book_snapshots += 1
        if all(
            left.get(key) == right.get(key)
            for key in ("yes_bid", "yes_bid_size", "no_bid", "no_bid_size")
        ):
            identical_top_snapshots += 1
        candidates = []
        for side, first_ask, second_ask in (
            ("left_yes_right_no", ask(left, "yes"), ask(right, "no")),
            ("left_no_right_yes", ask(left, "no"), ask(right, "yes")),
        ):
            if not first_ask or not second_ask:
                continue
            edge = (
                1.0
                - first_ask[0]
                - second_ask[0]
                - fee(first_ask[0])
                - fee(second_ask[0])
            )
            candidates.append((side, edge, min(first_ask[1], second_ask[1])))
        if not candidates:
            continue
        valid += 1
        key = f"{meta['asset']}:{meta['close_ts']}"
        best = max(candidates, key=lambda item: item[1])
        maximum[key] = max(maximum.get(key, -10.0), best[1])
        if best[1] >= 0.02 - 1e-12 and best[2] >= 1 and key not in first:
            first[key] = {
                "asset": meta["asset"],
                "close_ts": meta["close_ts"],
                "side": best[0],
                "post_fee_edge_c": 100 * best[1],
                "after_1c_per_leg_stress_c": 100 * (best[1] - 0.02),
                "paired_size": best[2],
                "request_span_ms": span_ms,
            }
    report = {
        "preregistration": "PREREG_multiasset_tail_duplicate_live_20261006.md",
        "snapshots": len(rows),
        "paired_book_snapshots": paired_book_snapshots,
        "identical_top_snapshots": identical_top_snapshots,
        "valid_snapshots": valid,
        "asset_hours": len(maximum),
        "signals": list(first.values()),
        "max_edge_by_asset_hour_c": {
            key: 100 * value for key, value in sorted(maximum.items())
        },
        "evidence": "prospective direct-depth size-aware non-atomic REST paper",
    }
    Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
