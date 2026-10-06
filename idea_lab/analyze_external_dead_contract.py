"""Frozen OOS replication of mickey1995's BTC dead-contract fade."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

try:
    from idea_lab.common import day_cluster_se, fee
except ModuleNotFoundError:
    from common import day_cluster_se, fee


MARKETS = Path(
    "/Users/kapil/proj/kalshi-scalp/data/oos_20261006/candles_BTC.json"
)
OUTPUT = Path("idea_lab/external_dead_contract_report.json")
SOURCE_COMMIT = "7a10efdc8d5d9c67219ce9104dbdbee1e0269201"


def summarize(rows: list[dict]) -> dict:
    if not rows:
        return {
            "trades": 0,
            "passes": False,
            "mean_c": None,
            "lo95_c": None,
            "halves_c": [],
            "extra_1c_stress_c": None,
            "mean_ex_best_10pct_days_c": None,
            "max_day_gross_positive_share": None,
            "rows": [],
        }
    pnl = np.asarray([row["pnl"] for row in rows], dtype=float)
    days = np.asarray([row["day"] for row in rows])
    mean, se = day_cluster_se(pnl, days)
    split = len(rows) // 2
    totals = {int(day): float(pnl[days == day].sum()) for day in np.unique(days)}
    remove_n = max(1, math.ceil(0.10 * len(totals)))
    removed = set(
        sorted(totals, key=lambda day: totals[day], reverse=True)[:remove_n]
    )
    trimmed = [row["pnl"] for row in rows if row["day"] not in removed]
    gross = {
        day: sum(max(row["pnl"], 0.0) for row in rows if row["day"] == day)
        for day in totals
    }
    gross_total = sum(gross.values())
    result = {
        "trades": len(rows),
        "wins": sum(row["pnl"] > 0 for row in rows),
        "losses": sum(row["pnl"] < 0 for row in rows),
        "mean_c": mean * 100,
        "lo95_c": (mean - 1.96 * se) * 100,
        "halves_c": [
            float(np.mean(pnl[:split]) * 100),
            float(np.mean(pnl[split:]) * 100),
        ],
        "extra_1c_stress_c": mean * 100 - 1,
        "mean_ex_best_10pct_days_c": float(np.mean(trimmed) * 100),
        "max_day_gross_positive_share": (
            max(gross.values()) / gross_total if gross_total else None
        ),
        "actual_no_ask_above_92c": sum(row["entry"] > 0.92 for row in rows),
        "rows": rows,
    }
    result["passes"] = bool(
        result["trades"] >= 100
        and result["mean_c"] > 0
        and result["extra_1c_stress_c"] > 0
        and result["lo95_c"] > 0
        and all(value > 0 for value in result["halves_c"])
        and result["mean_ex_best_10pct_days_c"] > 0
        and result["max_day_gross_positive_share"] is not None
        and result["max_day_gross_positive_share"] <= 0.20
    )
    return result


def find_signal(market: dict) -> tuple[dict, int] | None:
    t0 = int(market["open_ts"])
    bars = {
        int(row["ts"]): row
        for row in market.get("bars") or []
        if not row.get("post_close")
    }
    for minute in range(10, 15):
        row = bars.get(t0 + minute * 60)
        if row is None:
            continue
        midpoint_cents = int(
            ((float(row["b"]) + float(row["a"])) / 2.0) * 100
        )
        if 8 <= midpoint_cents < 15:
            return row, midpoint_cents
    return None


def score_market(market: dict) -> tuple[dict, dict | None] | None:
    found = find_signal(market)
    if found is None:
        return None
    row, midpoint_cents = found
    t0 = int(market["open_ts"])
    entry = 1.0 - float(row["b"])
    payout = 1.0 if market["result"] == "no" else 0.0
    base = {
        "ticker": market["ticker"],
        "t0": t0,
        "day": t0 // 86400,
        "signal_ts": int(row["ts"]),
        "minute": (int(row["ts"]) - t0) // 60,
        "midpoint_cents": midpoint_cents,
        "entry": entry,
        "result": market["result"],
        "pnl": payout - entry - fee(entry),
    }
    bars = {
        int(item["ts"]): item
        for item in market.get("bars") or []
        if not item.get("post_close")
    }
    next_row = bars.get(int(row["ts"]) + 60)
    delayed = None
    if next_row is not None:
        next_entry = 1.0 - float(next_row["b"])
        delayed = {
            **base,
            "entry": next_entry,
            "execution_ts": int(next_row["ts"]),
            "pnl": payout - next_entry - fee(next_entry),
        }
    return base, delayed


def main() -> None:
    markets = sorted(
        json.loads(MARKETS.read_text())["markets"],
        key=lambda row: int(row["open_ts"]),
    )
    same_bar = []
    next_minute = []
    for market in markets:
        if market.get("result") not in ("yes", "no"):
            continue
        scored = score_market(market)
        if scored is None:
            continue
        immediate, delayed = scored
        same_bar.append(immediate)
        if delayed is not None:
            next_minute.append(delayed)
    report = {
        "preregistration": "PREREG_external_dead_contract_oos_20261006.md",
        "source_commit": SOURCE_COMMIT,
        "source_published_before_local_sample": True,
        "market_count": len(markets),
        "same_bar_governing": summarize(same_bar),
        "next_minute_diagnostic": summarize(next_minute),
    }
    OUTPUT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
