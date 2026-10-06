#!/usr/bin/env python3
"""Audit two public Kalshi 15-minute research artifacts without trading.

This is a provenance/methodology audit, not a strategy backtest.  It records
whether the shipped paper ledger remains profitable after the documented
one-contract taker fees and whether a shipped ML sample's row split is really
an independent time/market holdout.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any


AOI_COMMIT = "0b22c616aa46ce4af09c8d9cd5aa055f5377a695"
FRAMEWORK_COMMIT = "eb129b4fe2fe9a05d8e9f5439e7a97a8e77ae550"
ASSETS = ("btc", "eth", "sol", "xrp")


def taker_fee_cents(price_cents: int) -> int:
    """One-contract fee used by the inspected source, rounded to cents."""
    if price_cents <= 0 or price_cents >= 100:
        return 0
    p = price_cents / 100.0
    return math.ceil(0.07 * p * (1.0 - p) * 100.0)


def quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * (len(ordered) - 1)))]


def audit_aoi(repo: Path) -> dict[str, Any]:
    trades_path = repo / "paper_trades.csv"
    log_path = repo / "aoi_log.csv"

    with trades_path.open(newline="") as handle:
        source_rows = list(csv.DictReader(handle))

    trades = []
    for row in source_rows:
        entry = int(row["entry_price_cents"])
        exit_price = int(row["exit_price_cents"])
        gross = int(row["net_profit_cents"])
        fees = taker_fee_cents(entry) + taker_fee_cents(exit_price)
        trades.append(
            {
                "entry_time": row["entry_time"],
                "direction": row["direction"],
                "entry_c": entry,
                "exit_c": exit_price,
                "gross_c": gross,
                "round_trip_fee_c": fees,
                "after_fee_c": gross - fees,
            }
        )

    aoi_values: list[float] = []
    book_states: list[tuple[int, int]] = []
    first_time = None
    last_time = None
    with log_path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            first_time = first_time or row["system_time"]
            last_time = row["system_time"]
            aoi_values.append(float(row["kalshi_aoi_s"]))
            book_states.append(
                (int(row["kalshi_yes_bid"]), int(row["kalshi_yes_ask"]))
            )

    largest = max(trades, key=lambda row: row["gross_c"])
    valid_entry_rows = [
        row for row in trades if 10 <= row["entry_c"] <= 90
    ]
    return {
        "source_commit": AOI_COMMIT,
        "paper_trades": len(trades),
        "gross_pnl_c": sum(row["gross_c"] for row in trades),
        "modeled_round_trip_taker_fees_c": sum(
            row["round_trip_fee_c"] for row in trades
        ),
        "after_fee_pnl_c": sum(row["after_fee_c"] for row in trades),
        "after_fee_mean_c": (
            sum(row["after_fee_c"] for row in trades) / len(trades)
            if trades
            else None
        ),
        "gross_positive_zero_negative": [
            sum(row["gross_c"] > 0 for row in trades),
            sum(row["gross_c"] == 0 for row in trades),
            sum(row["gross_c"] < 0 for row in trades),
        ],
        "entries_inside_current_10_90_band": len(valid_entry_rows),
        "current_band_after_fee_pnl_c": sum(
            row["after_fee_c"] for row in valid_entry_rows
        ),
        "largest_gross_trade_c": largest["gross_c"],
        "largest_trade_entry_c": largest["entry_c"],
        "after_fee_pnl_ex_largest_trade_c": (
            sum(row["after_fee_c"] for row in trades) - largest["after_fee_c"]
        ),
        "aoi_rows": len(aoi_values),
        "aoi_first_time": first_time,
        "aoi_last_time": last_time,
        "kalshi_aoi_median_s": quantile(aoi_values, 0.5),
        "kalshi_aoi_p90_s": quantile(aoi_values, 0.9),
        "kalshi_aoi_max_s": max(aoi_values) if aoi_values else None,
        "kalshi_aoi_over_10s_share": (
            sum(value > 10 for value in aoi_values) / len(aoi_values)
            if aoi_values
            else None
        ),
        "kalshi_aoi_over_60s_share": (
            sum(value > 60 for value in aoi_values) / len(aoi_values)
            if aoi_values
            else None
        ),
        "unique_book_states": len(set(book_states)),
        "book_state_changes": sum(
            left != right for left, right in zip(book_states, book_states[1:])
        ),
        "methodology_flags": [
            "paper entries assume immediate execution at displayed ask",
            "paper exits assume immediate execution at displayed bid",
            "paper ledger omits fees and ticker/market identifiers",
            "Kalshi AoI uses market.updated_time rather than a book-message timestamp",
            "missing asks fall back to bids, permitting non-executable synthetic prices",
        ],
    }


def audit_framework(repo: Path) -> dict[str, Any]:
    sample_path = repo / "data" / "sample_features.csv"
    with sample_path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))

    ticker_counts = {}
    engineered_sequence: list[tuple[str, str]] = []
    for asset in ASSETS:
        tickers = [
            row[f"{asset}_ticker"]
            for row in rows
            if row.get(f"{asset}_ticker")
        ]
        ticker_counts[asset] = {
            "unique": len(set(tickers)),
            "snapshots": len(tickers),
            "largest_snapshots_per_ticker": (
                Counter(tickers).most_common(1)[0][1] if tickers else 0
            ),
        }
        # train.py appends every BTC row, then every ETH row, etc.
        engineered_sequence.extend((asset, ticker) for ticker in tickers)

    split = int(0.8 * len(engineered_sequence))
    train = engineered_sequence[:split]
    test = engineered_sequence[split:]
    train_tickers = {ticker for _, ticker in train}
    test_tickers = {ticker for _, ticker in test}
    train_asset_tickers = set(train)
    test_asset_tickers = set(test)

    return {
        "source_commit": FRAMEWORK_COMMIT,
        "sample_rows": len(rows),
        "sample_columns": len(rows[0]) if rows else 0,
        "sample_first_time": rows[0]["ts"] if rows else None,
        "sample_last_time": rows[-1]["ts"] if rows else None,
        "ticker_counts": ticker_counts,
        "engineered_snapshot_rows": len(engineered_sequence),
        "row_split_index": split,
        "test_asset_counts": dict(Counter(asset for asset, _ in test)),
        "train_unique_tickers": len(train_tickers),
        "test_unique_tickers": len(test_tickers),
        "ticker_overlap": len(train_tickers & test_tickers),
        "asset_ticker_overlap": len(train_asset_tickers & test_asset_tickers),
        "methodology_flags": [
            "train.py splits snapshot rows after concatenating assets, not markets by time",
            "the shipped test partition is XRP-only",
            "hundreds of snapshots share each single settlement target",
            "the sample spans only a handful of quarter-hour outcomes",
            "the row-level sample count is not an independent-outcome sample size",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--aoi-repo",
        type=Path,
        default=Path("/tmp/kalshi_ext_20261006_6/cross-market-arbitrage-aoi"),
    )
    parser.add_argument(
        "--framework-repo",
        type=Path,
        default=Path("/tmp/kalshi_ext_20261006_6/kalshi-crypto-bot"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("idea_lab/external_aoi_framework_audit.json"),
    )
    args = parser.parse_args()
    report = {
        "aoi_paper_project": audit_aoi(args.aoi_repo),
        "ml_framework": audit_framework(args.framework_repo),
        "evidence_class": "external_retrospective_methodology_audit",
        "promotion_eligible": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
