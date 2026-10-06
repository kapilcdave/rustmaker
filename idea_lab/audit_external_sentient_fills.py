"""Audit the public fill ledger formerly shipped by sentient-market-reader."""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

try:
    from idea_lab.common import day_cluster_se
except ModuleNotFoundError:
    from common import day_cluster_se


FILLS = Path("/tmp/sentient_live_fills_20261006.json")
CACHE = Path("/tmp/sentient_live_analysis_cache_20261006.json")
SOURCE_RESULTS = Path("/tmp/sentient_live_analysis_results_20261006.json")
OUTPUT = Path("idea_lab/external_sentient_fill_audit_report.json")


def fill_price(row: dict) -> float:
    key = "yes_price_dollars" if row["side"] == "yes" else "no_price_dollars"
    return float(row[key])


def ticker_ledger(rows: list[dict], result: str) -> dict:
    rows = sorted(rows, key=lambda row: (int(row["ts"]), row["fill_id"]))
    inventory = {"yes": 0.0, "no": 0.0}
    cash = 0.0
    self_contained = True
    fees = 0.0
    for row in rows:
        count = float(row["count_fp"])
        price = fill_price(row)
        fee_cost = float(row.get("fee_cost") or 0.0)
        fees += fee_cost
        if row["action"] == "buy":
            inventory[row["side"]] += count
            cash -= count * price + fee_cost
        elif row["action"] == "sell":
            inventory[row["side"]] -= count
            cash += count * price - fee_cost
        else:
            raise ValueError(f"unknown action {row['action']!r}")
        if inventory[row["side"]] < -1e-9:
            self_contained = False
    payout = inventory[result]
    return {
        "ticker": rows[0]["ticker"],
        "first_ts": int(rows[0]["ts"]),
        "day": int(rows[0]["ts"]) // 86400,
        "fills": len(rows),
        "cash": cash,
        "fees": fees,
        "ending_yes": inventory["yes"],
        "ending_no": inventory["no"],
        "self_contained": self_contained,
        "result": result,
        "payout": payout,
        "pnl": cash + payout,
    }


def summarize(rows: list[dict]) -> dict:
    if not rows:
        return {"tickers": 0, "pnl": 0.0}
    pnl = np.asarray([row["pnl"] for row in rows], dtype=float)
    days = np.asarray([row["day"] for row in rows])
    mean, se = day_cluster_se(pnl, days)
    by_day = {
        int(day): float(pnl[days == day].sum()) for day in np.unique(days)
    }
    gross = {day: max(value, 0.0) for day, value in by_day.items()}
    gross_total = sum(gross.values())
    return {
        "tickers": len(rows),
        "fills": sum(row["fills"] for row in rows),
        "pnl": float(pnl.sum()),
        "mean_pnl_per_ticker": mean,
        "day_cluster_lo95_per_ticker": mean - 1.96 * se,
        "positive_tickers": int((pnl > 0).sum()),
        "negative_tickers": int((pnl < 0).sum()),
        "zero_tickers": int((pnl == 0).sum()),
        "first_time": datetime.fromtimestamp(
            min(row["first_ts"] for row in rows), timezone.utc
        ).isoformat(),
        "last_time": datetime.fromtimestamp(
            max(row["first_ts"] for row in rows), timezone.utc
        ).isoformat(),
        "max_day_gross_positive_share": (
            max(gross.values()) / gross_total if gross_total else None
        ),
        "negative_ending_inventory_tickers": sum(
            row["ending_yes"] < -1e-9 or row["ending_no"] < -1e-9
            for row in rows
        ),
        "rows": rows,
    }


def main() -> None:
    fills_doc = json.loads(FILLS.read_text())
    all_fills = [
        row
        for row in fills_doc["all_fills"]
        if "KXBTC15M" in row.get("ticker", "")
    ]
    cache = json.loads(CACHE.read_text())
    outcomes = cache["outcomes"]
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in all_fills:
        outcome = outcomes.get(row["ticker"], {}).get("result")
        if outcome in ("yes", "no"):
            grouped[row["ticker"]].append(row)
    ledgers = [
        ticker_ledger(rows, outcomes[ticker]["result"])
        for ticker, rows in grouped.items()
    ]
    ledgers.sort(key=lambda row: row["first_ts"])
    self_contained = [row for row in ledgers if row["self_contained"]]

    source = json.loads(SOURCE_RESULTS.read_text())
    source_trades = source["trades"]
    source_dates = [row["fill_time"] for row in source_trades]
    report = {
        "preregistration": "PREREG_external_sentient_fill_audit_20261006.md",
        "source_head_commit": "bfbf470f49cc7585f59fde02f4501da0ae480e37",
        "runtime_file_removal_commit": (
            "a34c4b745cf38f032433f43a11d18b36bd3b0029"
        ),
        "raw": {
            "all_fills": len(fills_doc["all_fills"]),
            "buy_fills": len(fills_doc["buy_fills"]),
            "kxbtc15m_outcome_covered_fills": sum(
                len(rows) for rows in grouped.values()
            ),
            "outcome_covered_tickers": len(grouped),
        },
        "all_covered_ticker_ledger": summarize(ledgers),
        "self_contained_ticker_ledger": summarize(self_contained),
        "source_independent_buy_hold": {
            "fill_rows": len(source_trades),
            "unique_tickers": len({row["ticker"] for row in source_trades}),
            "pnl": sum(float(row["pnl"]) for row in source_trades),
            "fees": sum(float(row["fee_paid"]) for row in source_trades),
            "wins": sum(bool(row["won"]) for row in source_trades),
            "losses": sum(not bool(row["won"]) for row in source_trades),
            "first_time": min(source_dates),
            "last_time": max(source_dates),
        },
        "readme_147_apr19_22_publicly_auditable": False,
        "readme_mismatch_reason": (
            "The public analysis rows end on 2026-04-07, before the claimed "
            "2026-04-19 through 2026-04-22 147-fill filter sample."
        ),
    }
    OUTPUT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
