"""Frozen post-report OOS replication of the external Mystic done-deal rule."""
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
OUTPUT = Path("idea_lab/mystic_done_deal_oos_report.json")
SECONDS = (300, 240, 180, 120)


def aligned_and_no_wick(
    spot: dict[int, tuple[float, float, float, float]],
    stamp: int,
    direction: str,
    ref_price: float,
) -> bool:
    candles = [spot.get(stamp - 180), spot.get(stamp - 120), spot.get(stamp - 60)]
    if any(candle is None for candle in candles):
        return False
    rows = [candle for candle in candles if candle is not None]
    closes = [row[1] for row in rows]
    net_move = closes[-1] - closes[0]
    if direction == "up" and net_move <= 0:
        return False
    if direction == "down" and net_move >= 0:
        return False
    minimum_wick = ref_price * 0.00015
    for opened, closed, low, high in rows[-2:]:
        body = max(abs(closed - opened), 1e-8)
        if direction == "up":
            opposing = min(opened, closed) - low
        else:
            opposing = high - max(opened, closed)
        if opposing > body * 2.5 and opposing > minimum_wick:
            return False
    return True


def main() -> None:
    markets = json.loads(MARKETS.read_text())["markets"]
    payload = json.loads(SPOT.read_text())
    spot = {
        int(row[0]): (float(row[3]), float(row[4]), float(row[1]), float(row[2]))
        for row in payload["bars"]
    }
    trades = []
    for market in sorted(markets, key=lambda row: int(row["open_ts"])):
        t0 = int(market["open_ts"])
        close = int(market["close_ts"])
        bars = {int(row["ts"]): row for row in market.get("bars") or []}
        if t0 not in spot:
            continue
        window_open = spot[t0][0]
        for seconds_left in SECONDS:
            stamp = close - seconds_left
            if stamp not in bars or stamp not in spot:
                continue
            bar = bars[stamp]
            yes_ask = float(bar["a"])
            no_ask = 1.0 - float(bar["b"])
            if yes_ask >= no_ask:
                side, direction, ask = "yes", "up", yes_ask
                distance = (spot[stamp][1] / window_open - 1.0) * 100
            else:
                side, direction, ask = "no", "down", no_ask
                distance = (window_open / spot[stamp][1] - 1.0) * 100
            if not 0.93 <= ask <= 0.95:
                continue
            required = 0.10 if seconds_left > 210 else 0.06
            if distance < required:
                continue
            if not aligned_and_no_wick(spot, stamp, direction, spot[stamp][1]):
                continue
            entry = min(0.99, ask + 0.01)
            payout = 1.0 if market.get("result") == side else 0.0
            trades.append(
                {
                    "ticker": market["ticker"],
                    "t0": t0,
                    "day": t0 // 86400,
                    "seconds_left": seconds_left,
                    "side": side,
                    "ask": ask,
                    "entry_after_1c_slip": entry,
                    "distance_pct": distance,
                    "required_distance_pct": required,
                    "result": market.get("result"),
                    "payout": payout,
                    "pnl": payout - entry - fee(entry),
                }
            )
            break
    pnl = np.asarray([row["pnl"] for row in trades])
    days = np.asarray([row["day"] for row in trades])
    mean = se = None
    if len(pnl):
        mean, se = day_cluster_se(pnl, days)
    split = len(trades) // 2
    day_totals = {
        day: float(pnl[days == day].sum()) for day in sorted(set(days.tolist()))
    }
    remove_n = max(1, math.ceil(0.10 * len(day_totals))) if day_totals else 0
    removed = set(
        sorted(day_totals, key=lambda day: day_totals[day], reverse=True)[:remove_n]
    )
    trimmed = [row["pnl"] for row in trades if row["day"] not in removed]
    gross_by_day = {
        day: sum(max(row["pnl"], 0.0) for row in trades if row["day"] == day)
        for day in day_totals
    }
    gross_total = sum(gross_by_day.values())
    max_share = max(gross_by_day.values()) / gross_total if gross_total else None
    report = {
        "preregistration": "PREREG_mystic_done_deal_oos_20261006.md",
        "evidence": "post-report historical same-minute candle paper",
        "markets": len(markets),
        "trades": len(trades),
        "losses": sum(row["payout"] == 0 for row in trades),
        "loss_rate": (
            sum(row["payout"] == 0 for row in trades) / len(trades)
            if trades
            else None
        ),
        "mean_c": None if mean is None else mean * 100,
        "se_c": None if se is None else se * 100,
        "lo95_c": None if mean is None else (mean - 1.96 * se) * 100,
        "extra_one_cent_stressed_mean_c": (
            None if mean is None else mean * 100 - 1
        ),
        "halves_c": [
            float(np.mean(pnl[:split]) * 100) if split else None,
            float(np.mean(pnl[split:]) * 100) if len(pnl[split:]) else None,
        ],
        "mean_ex_best_10pct_days_c": (
            float(np.mean(trimmed) * 100) if trimmed else None
        ),
        "max_day_gross_positive_share": max_share,
        "trade_rows": trades,
    }
    report["passes_paper_screen"] = bool(
        len(trades) >= 100
        and report["extra_one_cent_stressed_mean_c"] is not None
        and report["extra_one_cent_stressed_mean_c"] > 0
        and report["lo95_c"] > 0
        and all(value is not None and value > 0 for value in report["halves_c"])
        and report["mean_ex_best_10pct_days_c"] is not None
        and report["mean_ex_best_10pct_days_c"] > 0
        and max_share is not None
        and max_share <= 0.20
    )
    OUTPUT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
