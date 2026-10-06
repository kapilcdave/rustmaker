"""Frozen causal replication of the external near-strike late-fade rule."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

try:
    from idea_lab.common import day_cluster_se, fee
except ModuleNotFoundError:
    from common import day_cluster_se, fee


LATER_MARKETS = Path("/Users/kapil/proj/kalshi-scalp/data/oos_20261006/candles_BTC.json")
LATER_SPOT = Path("/Users/kapil/proj/kalshi-scalp/data/oos_20261006/cb_BTC-USD.json")
EARLIER_MARKETS = Path("/Users/kapil/proj/kalshi-scalp/data/kxbtc15m_candles.json")
EARLIER_SPOT = Path("/Users/kapil/proj/kalshi-scalp/data/cb_btc_1m.json")
OUTPUT = Path("idea_lab/external_momotsanya_late_fade_report.json")


def load_spot(path: Path) -> dict[int, float]:
    payload = json.loads(path.read_text())
    rows = payload.get("bars") if isinstance(payload, dict) else payload
    return {int(row[0]): float(row[4]) for row in rows}


def summarize(rows: list[dict]) -> dict:
    pnl = np.asarray([row["pnl"] for row in rows])
    days = np.asarray([row["day"] for row in rows])
    mean = se = None
    if len(rows):
        mean, se = day_cluster_se(pnl, days)
    split = len(rows) // 2
    day_total = {
        day: float(pnl[days == day].sum()) for day in sorted(set(days.tolist()))
    }
    remove_n = max(1, math.ceil(0.10 * len(day_total))) if day_total else 0
    removed = set(
        sorted(day_total, key=lambda day: day_total[day], reverse=True)[:remove_n]
    )
    trimmed = [row["pnl"] for row in rows if row["day"] not in removed]
    gross = {
        day: sum(max(row["pnl"], 0.0) for row in rows if row["day"] == day)
        for day in day_total
    }
    gross_total = sum(gross.values())
    return {
        "trades": len(rows),
        "mean_c": None if mean is None else 100 * mean,
        "lo95_c": None if mean is None else 100 * (mean - 1.96 * se),
        "one_cent_stressed_mean_c": None if mean is None else 100 * mean - 1,
        "halves_c": [
            float(100 * np.mean(pnl[:split])) if split else None,
            float(100 * np.mean(pnl[split:])) if len(pnl[split:]) else None,
        ],
        "mean_ex_best_10pct_days_c": (
            float(100 * np.mean(trimmed)) if trimmed else None
        ),
        "max_day_gross_positive_share": (
            max(gross.values()) / gross_total if gross_total else None
        ),
        "rows": rows,
    }


def score(markets_path: Path, spot_path: Path) -> dict:
    markets = json.loads(markets_path.read_text())["markets"]
    spot = load_spot(spot_path)
    trades = []
    for market in sorted(markets, key=lambda row: int(row["open_ts"])):
        close = int(market["close_ts"])
        try:
            strike = float(market["floor_strike"])
        except (KeyError, TypeError, ValueError):
            continue
        bars = {int(row["ts"]): row for row in market.get("bars") or []}
        for seconds_left in (120, 60):
            stamp = close - seconds_left
            spot_stamp = stamp - 60
            if stamp not in bars or spot_stamp not in spot or spot_stamp - 60 not in spot:
                continue
            px = spot[spot_stamp]
            gap_pct = (px / strike - 1.0) * 100
            if not (0 < abs(gap_pct) <= 0.02):
                continue
            side = "no" if gap_pct > 0 else "yes"
            bar = bars[stamp]
            entry = 1.0 - float(bar["b"]) if side == "no" else float(bar["a"])
            if not (0.15 <= entry <= 0.50):
                continue
            momentum = (px / spot[spot_stamp - 60] - 1.0) * 100
            if side == "no" and momentum > 0.03:
                continue
            if side == "yes" and momentum < -0.03:
                continue
            payout = 1.0 if market.get("result") == side else 0.0
            trades.append(
                {
                    "ticker": market["ticker"],
                    "day": int(market["open_ts"]) // 86400,
                    "seconds_left": seconds_left,
                    "side": side,
                    "entry": entry,
                    "gap_pct": gap_pct,
                    "momentum_pct": momentum,
                    "result": market.get("result"),
                    "pnl": payout - entry - fee(entry),
                }
            )
            break
    return summarize(trades)


def passes(row: dict) -> bool:
    return bool(
        row["trades"] >= 100
        and row["one_cent_stressed_mean_c"] is not None
        and row["one_cent_stressed_mean_c"] > 0
        and row["lo95_c"] is not None
        and row["lo95_c"] > 0
        and all(value is not None and value > 0 for value in row["halves_c"])
        and row["mean_ex_best_10pct_days_c"] is not None
        and row["mean_ex_best_10pct_days_c"] > 0
        and row["max_day_gross_positive_share"] is not None
        and row["max_day_gross_positive_share"] <= 0.20
    )


def main() -> None:
    later = score(LATER_MARKETS, LATER_SPOT)
    earlier = score(EARLIER_MARKETS, EARLIER_SPOT)
    later["passes_period_gate"] = passes(later)
    earlier["passes_period_gate"] = passes(earlier)
    report = {
        "preregistration": "PREREG_external_momotsanya_late_fade_20261006.md",
        "later": later,
        "earlier_replication": earlier,
        "passes_both_periods": later["passes_period_gate"] and earlier["passes_period_gate"],
    }
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
