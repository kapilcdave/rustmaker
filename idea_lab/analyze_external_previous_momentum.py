"""Frozen replication of DeweyMarco/simple-kalshi-bot's three core rules."""
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
SPOT = Path(
    "/Users/kapil/proj/kalshi-scalp/data/oos_20261006/cb_BTC-USD.json"
)
OUTPUT = Path("idea_lab/external_previous_momentum_report.json")


def summarize(rows: list[dict]) -> dict:
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
    out = {
        "trades": len(rows),
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
        "rows": rows,
    }
    out["passes"] = bool(
        out["trades"] >= 100
        and out["extra_1c_stress_c"] > 0
        and out["lo95_c"] > 0
        and all(value > 0 for value in out["halves_c"])
        and out["mean_ex_best_10pct_days_c"] > 0
        and out["max_day_gross_positive_share"] is not None
        and out["max_day_gross_positive_share"] <= 0.20
    )
    return out


def main() -> None:
    markets = sorted(
        json.loads(MARKETS.read_text())["markets"],
        key=lambda row: int(row["open_ts"]),
    )
    spot = {
        int(row[0]): float(row[4])
        for row in json.loads(SPOT.read_text())["bars"]
    }
    output = {"PREVIOUS": [], "MOMENTUM": [], "CONSENSUS": []}
    previous = None
    for market in markets:
        t0 = int(market["open_ts"])
        adjacent = (
            previous is not None and int(previous["close_ts"]) == t0
        )
        first = next(
            (
                row
                for row in market.get("bars") or []
                if not row.get("post_close") and int(row["ts"]) == t0 + 60
            ),
            None,
        )
        now_close = spot.get(t0 - 60)
        prior_close = spot.get(t0 - 120)
        if (
            adjacent
            and first is not None
            and now_close is not None
            and prior_close is not None
        ):
            previous_side = previous["result"]
            momentum_side = "yes" if now_close > prior_close else "no"
            sides = {
                "PREVIOUS": previous_side,
                "MOMENTUM": momentum_side,
            }
            if previous_side == momentum_side:
                sides["CONSENSUS"] = previous_side
            for strategy, side in sides.items():
                entry = (
                    float(first["a"])
                    if side == "yes"
                    else 1.0 - float(first["b"])
                )
                payout = 1.0 if market["result"] == side else 0.0
                output[strategy].append(
                    {
                        "ticker": market["ticker"],
                        "t0": t0,
                        "day": t0 // 86400,
                        "side": side,
                        "entry": entry,
                        "result": market["result"],
                        "pnl": payout - entry - fee(entry),
                    }
                )
        previous = market
    report = {
        "preregistration": "PREREG_external_previous_momentum_20261006.md",
        "source_commit": "f01baea7cf185186e46ce701904d60357ad779ba",
        "strategies": {
            strategy: summarize(rows) for strategy, rows in output.items()
        },
    }
    OUTPUT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
