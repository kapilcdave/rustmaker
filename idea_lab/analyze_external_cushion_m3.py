"""Reproduce the externally frozen minute-3 1.04-sigma BTC cushion gate locally."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

try:
    from idea_lab.common import day_cluster_se, fee
except ModuleNotFoundError:
    from common import day_cluster_se, fee

ROOT = Path("/Users/kapil/proj/kalshi-scalp/data/oos_20261006")
START = 1_789_357_500
END = 1_791_244_800
SOURCE_PUBLICATION = 1_791_118_920
MIN_Z = 1.04
PROXY_RMSE = 8.32
REMAINING_EFFECTIVE = 12.0 - 2.0 / 3.0


def number(value) -> float:
    return float(str(value).replace(",", ""))


def summarize(rows: list[dict]) -> dict:
    values = [row["pnl"] for row in rows]
    if not values:
        return {"trades": 0}
    days = [row["day"] for row in rows]
    mean, se = day_cluster_se(values, days)
    split = len(values) // 2
    day_values = {}
    for row in rows:
        day_values[row["day"]] = day_values.get(row["day"], 0.0) + row["pnl"]
    delete_n = max(1, math.ceil(0.10 * len(day_values)))
    best = set(sorted(day_values, key=day_values.get, reverse=True)[:delete_n])
    remaining = [row["pnl"] for row in rows if row["day"] not in best]
    delayed = [row["delayed_pnl"] for row in rows if row["delayed_pnl"] is not None]
    by_side = {}
    for side in ("yes", "no"):
        side_values = [row["pnl"] for row in rows if row["side"] == side]
        by_side[side] = {
            "trades": len(side_values),
            "mean_c": float(np.mean(side_values) * 100) if side_values else None,
        }
    return {
        "trades": len(rows),
        "days": len(set(days)),
        "mean_c": mean * 100,
        "clustered_se_c": se * 100,
        "clustered_lo95_c": (mean - 1.96 * se) * 100,
        "extra_1c_stress_lo95_c": (mean - 0.01 - 1.96 * se) * 100,
        "halves_c": [
            float(np.mean(values[:split]) * 100) if split else None,
            float(np.mean(values[split:]) * 100) if values[split:] else None,
        ],
        "best_10pct_days_deleted_mean_c": (
            float(np.mean(remaining) * 100) if remaining else None
        ),
        "one_minute_delayed_mean_c": (
            float(np.mean(delayed) * 100) if delayed else None
        ),
        "by_side": by_side,
    }


def main() -> None:
    markets = json.loads((ROOT / "candles_BTC.json").read_text())["markets"]
    coinbase = {
        int(row[0]): float(row[4])
        for row in json.loads((ROOT / "cb_BTC-USD.json").read_text())["bars"]
    }
    rows = []
    for market in markets:
        if market.get("result") not in ("yes", "no"):
            continue
        if market.get("floor_strike") in (None, "None"):
            continue
        t0 = int(market["open_ts"])
        if not START <= t0 < END:
            continue
        bars = {
            int(row["ts"]): row
            for row in market["bars"]
            if not row.get("post_close")
        }
        entry_bar = bars.get(t0 + 180)
        delayed_bar = bars.get(t0 + 240)
        spot = coinbase.get(t0 + 120)
        history = [coinbase.get(ts) for ts in range(t0 - 3600, t0, 60)]
        if (
            entry_bar is None
            or spot is None
            or any(value is None for value in history)
            or entry_bar.get("a") is None
            or entry_bar.get("b") is None
        ):
            continue
        sigma = float(np.std(np.diff(history), ddof=1))
        strike = number(market["floor_strike"])
        denominator = math.sqrt(
            sigma * sigma * REMAINING_EFFECTIVE + PROXY_RMSE * PROXY_RMSE
        )
        z = abs(spot - strike) / denominator
        if z < MIN_Z:
            continue
        side = "yes" if spot >= strike else "no"
        price = (
            float(entry_bar["a"])
            if side == "yes"
            else 1.0 - float(entry_bar["b"])
        )
        won = market["result"] == side
        delayed_pnl = None
        if (
            delayed_bar is not None
            and delayed_bar.get("a") is not None
            and delayed_bar.get("b") is not None
        ):
            delayed_price = (
                float(delayed_bar["a"])
                if side == "yes"
                else 1.0 - float(delayed_bar["b"])
            )
            delayed_pnl = float(won) - delayed_price - fee(delayed_price)
        rows.append(
            {
                "ticker": market["ticker"],
                "t0": t0,
                "day": t0 // 86400,
                "side": side,
                "z": z,
                "price": price,
                "pnl": float(won) - price - fee(price),
                "delayed_pnl": delayed_pnl,
            }
        )
    report = {
        "source_commit": "a3f1ebaad385dec9a0595ad558649fa199a20256",
        "classification": "external-source transfer; overlapping history; non-promotional",
        "alignment": "minute-3 Kalshi close, Coinbase completed bar ending one minute earlier",
        "all_local_period": summarize(rows),
        "strictly_after_source_report": summarize(
            [row for row in rows if row["t0"] >= SOURCE_PUBLICATION]
        ),
    }
    Path("idea_lab/external_cushion_m3_report.json").write_text(
        json.dumps(report, indent=2)
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

