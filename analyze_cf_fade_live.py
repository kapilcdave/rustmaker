#!/usr/bin/env python3
"""Score the prospective P2 exact-CF disagreement fade."""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

import numpy as np

import analyze_cf_rolling_endgame as base
import analyze_public_cf_live as live

CUTOFF_MS = 1_791_295_415_000


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "capture",
        type=Path,
        nargs="?",
        default=Path("data/cf_exact/live/orderbook_20261006_0400.jsonl.gz"),
    )
    parser.add_argument("--fetch-outcomes", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/cf_exact/live/cf_fade_live_report.json"),
    )
    args = parser.parse_args()

    signals, _, _ = live.score_capture(args.capture)
    candidates = [
        row
        for row in signals
        if row["arm"] == "P2"
        and row["threshold"] == 0.02
        and row.get("decision_ms", 0) >= CUTOFF_MS
    ]
    # score_capture keeps only the first trigger in each market over the full
    # journal. Therefore a market first triggered before cutoff cannot re-enter.
    outcomes = live.fetch_outcomes(row["ticker"] for row in candidates) if args.fetch_outcomes else {}
    settled = []
    for row in candidates:
        outcome = outcomes.get(row["ticker"])
        if not outcome:
            continue
        if row["side"] == "yes":
            fade_side = "no"
            if row.get("yes_bid") is None:
                continue
            entry = 1.0 - float(row["yes_bid"])
            depth = row.get("yes_bid_size")
        else:
            fade_side = "yes"
            if row.get("yes_ask") is None:
                continue
            entry = float(row["yes_ask"])
            depth = row.get("yes_ask_size")
        fee = base.taker_fee(entry)
        won = outcome["result"] == fade_side
        pnl = (1.0 if won else 0.0) - entry - fee
        settled.append(
            {
                **row,
                "fade_side": fade_side,
                "fade_entry": entry,
                "fade_fee": fee,
                "fade_displayed_depth": depth,
                "result": outcome["result"],
                "pnl": pnl,
            }
        )

    pnl = np.asarray([row["pnl"] for row in settled], dtype=float)
    split = len(settled) // 2
    by_window: dict[int, list[float]] = defaultdict(list)
    by_asset: dict[str, list[float]] = defaultdict(list)
    for row in settled:
        by_window[int(row["close_ms"])].append(float(row["pnl"]))
        by_asset[str(row["asset"])].append(float(row["pnl"]))
    window_means = [statistics.fmean(values) for values in by_window.values()]
    gross = {asset: sum(max(value, 0) for value in values) for asset, values in by_asset.items()}
    gross_total = sum(gross.values())

    def mean(values: np.ndarray | list[float]) -> float | None:
        return float(np.mean(values)) if len(values) else None

    report = {
        "preregistration": "PREREG_cf_fade_live_20261006.md",
        "cutoff_ms": CUTOFF_MS,
        "execution_status": "prospective non-atomic REST paper; no fills claimed",
        "signals": len(candidates),
        "settled": len(settled),
        "mean_c": mean(pnl * 100),
        "median_c": float(np.median(pnl) * 100) if len(pnl) else None,
        "equal_window_mean_c": mean(np.asarray(window_means) * 100),
        "chronological_halves_c": [
            mean(pnl[:split] * 100),
            mean(pnl[split:] * 100),
        ],
        "stress": {
            "0.5c_mean_c": mean(pnl * 100 - 0.5),
            "1.0c_mean_c": mean(pnl * 100 - 1.0),
            "1.0c_equal_window_mean_c": (
                mean(np.asarray(window_means) * 100 - 1.0)
            ),
        },
        "per_asset": {
            asset: {
                "n": len(values),
                "mean_c": statistics.fmean(values) * 100,
                "total_dollars": sum(values),
                "gross_positive_share": (
                    gross[asset] / gross_total if gross_total else None
                ),
            }
            for asset, values in sorted(by_asset.items())
        },
        "settled_rows": settled,
    }
    shares_ok = all(
        row["gross_positive_share"] is None or row["gross_positive_share"] <= 0.60
        for row in report["per_asset"].values()
    )
    halves = report["chronological_halves_c"]
    report["passes_short_prospective_screen"] = bool(
        len(settled) >= 20
        and report["mean_c"] is not None
        and report["mean_c"] > 0
        and report["median_c"] is not None
        and report["median_c"] > 0
        and all(value is not None and value > 0 for value in halves)
        and report["stress"]["1.0c_equal_window_mean_c"] is not None
        and report["stress"]["1.0c_equal_window_mean_c"] > 0
        and shares_ok
    )
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
