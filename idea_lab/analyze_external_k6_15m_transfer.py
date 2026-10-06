"""Exploratory transfer of the public hourly K6/K14 rule to KXBTC15M."""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

try:
    from idea_lab.common import day_cluster_se, fee
except ModuleNotFoundError:
    from common import day_cluster_se, fee


MARKETS = Path(
    "/Users/kapil/proj/kalshi-scalp/data/oos_20261006/candles_BTC.json"
)
SPOT = Path("/Users/kapil/proj/kalshi-scalp/data/oos_20261006/cb_BTC-USD.json")
OUTPUT = Path("idea_lab/external_k6_15m_transfer_report.json")
SOURCE_COMMIT = "92564c1141bef9e451cd5ccefb2c5fd8cc7b0d38"
START = int(datetime(2026, 9, 24, tzinfo=timezone.utc).timestamp())


def main() -> None:
    markets = json.loads(MARKETS.read_text())["markets"]
    spot = {int(row[0]): row for row in json.loads(SPOT.read_text())["bars"]}
    trades = []
    for market in sorted(markets, key=lambda row: int(row["open_ts"])):
        t0 = int(market["open_ts"])
        if t0 < START or market.get("result") not in ("yes", "no"):
            continue
        strike = float(market["floor_strike"])
        bars = {
            int(row["ts"]): row
            for row in market.get("bars") or []
            if not row.get("post_close")
        }
        for minute in range(1, 15):
            decision_ts = t0 + minute * 60
            quote = bars.get(decision_ts)
            latest = spot.get(decision_ts - 60)
            if quote is None or latest is None:
                continue
            spot_price = float(latest[4])
            distance = spot_price - strike
            if not 50 <= abs(distance) <= 500:
                continue
            side = "yes" if distance > 0 else "no"
            closes = [
                float(spot[decision_ts - offset * 60][4])
                for offset in range(16, 0, -1)
                if decision_ts - offset * 60 in spot
            ]
            if len(closes) < 10:
                continue
            rv_annual = float(
                np.std(np.diff(np.log(np.asarray(closes))), ddof=1)
                * math.sqrt(525_600)
            )
            if side == "yes" and rv_annual < 0.33:
                continue
            if side == "no" and rv_annual >= 0.65:
                continue
            entry = (
                float(quote["a"])
                if side == "yes"
                else 1.0 - float(quote["b"])
            )
            # The public code gates on a fixed historical p(win)=0.88.
            expected_edge = 0.88 - entry - fee(entry)
            if expected_edge < 0.005:
                continue
            payout = 1.0 if market["result"] == side else 0.0
            trades.append(
                {
                    "ticker": market["ticker"],
                    "t0": t0,
                    "day": t0 // 86400,
                    "minute": minute,
                    "side": side,
                    "entry": entry,
                    "distance_dollars": distance,
                    "rv_annual": rv_annual,
                    "expected_edge": expected_edge,
                    "result": market["result"],
                    "pnl": payout - entry - fee(entry),
                }
            )
            break

    values = np.asarray([row["pnl"] for row in trades], dtype=float)
    days = np.asarray([row["day"] for row in trades])
    mean, se = day_cluster_se(values, days)
    half = len(values) // 2
    gross_by_day: dict[int, float] = defaultdict(float)
    for row in trades:
        gross_by_day[row["day"]] += max(0.0, row["pnl"])
    gross = sum(gross_by_day.values())
    report = {
        "classification": "exploratory transfer; observed before documentation; never promotion eligible",
        "source_commit": SOURCE_COMMIT,
        "source_instrument": "KXBTCD hourly strike ladder",
        "transfer_instrument": "KXBTC15M direction contract",
        "evidence": "causal completed-minute spot plus Kalshi candle-close ask; not fills",
        "trades": len(trades),
        "wins": int(np.sum(values > 0)),
        "mean_c": float(mean * 100),
        "clustered_se_c": float(se * 100),
        "clustered_low_c": float((mean - 1.96 * se) * 100),
        "halves_c": [
            float(np.mean(values[:half]) * 100),
            float(np.mean(values[half:]) * 100),
        ],
        "additional_1c_stress_mean_c": float(mean * 100 - 1),
        "max_day_share_gross_positive": (
            max(gross_by_day.values()) / gross if gross else None
        ),
        "by_side": {
            side: {
                "trades": len(selected),
                "mean_c": float(np.mean([row["pnl"] for row in selected]) * 100),
            }
            for side in ("yes", "no")
            if (selected := [row for row in trades if row["side"] == side])
        },
        "rows": trades,
    }
    OUTPUT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

