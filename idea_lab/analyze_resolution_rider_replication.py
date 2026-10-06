"""Frozen external resolution-rider replication on BTC 15-minute candles."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

try:
    from idea_lab.common import day_cluster_se, fee
except ModuleNotFoundError:
    from common import day_cluster_se, fee

LATER_MARKETS = Path(
    "/Users/kapil/proj/kalshi-scalp/data/oos_20261006/candles_BTC.json"
)
LATER_SPOT = Path(
    "/Users/kapil/proj/kalshi-scalp/data/oos_20261006/cb_BTC-USD.json"
)
EARLIER_MARKETS = Path(
    "/Users/kapil/proj/kalshi-scalp/data/kxbtc15m_candles.json"
)
EARLIER_SPOT = Path("/Users/kapil/proj/kalshi-scalp/data/cb_btc_1m.json")
OUTPUT = Path("idea_lab/resolution_rider_replication_report.json")
SECONDS = (180, 120, 60)


def load_spot(path: Path) -> dict[int, float]:
    payload = json.loads(path.read_text())
    rows = payload.get("bars") if isinstance(payload, dict) else payload
    return {int(row[0]): float(row[4]) for row in rows}


def summarize(trades: list[dict]) -> dict:
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
    return {
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
        "one_cent_stressed_mean_c": None if mean is None else mean * 100 - 1,
        "halves_c": [
            float(np.mean(pnl[:split]) * 100) if split else None,
            float(np.mean(pnl[split:]) * 100) if len(pnl[split:]) else None,
        ],
        "mean_ex_best_10pct_days_c": (
            float(np.mean(trimmed) * 100) if trimmed else None
        ),
        "max_day_gross_positive_share": (
            max(gross_by_day.values()) / gross_total if gross_total else None
        ),
        "trade_rows": trades,
    }


def score(markets_path: Path, spot_path: Path, spot_lag_s: int = 0) -> dict:
    markets = json.loads(markets_path.read_text())["markets"]
    spot = load_spot(spot_path)
    trades = []
    for market in sorted(markets, key=lambda row: int(row["open_ts"])):
        t0 = int(market["open_ts"])
        close = int(market["close_ts"])
        try:
            strike = float(market["floor_strike"])
        except (KeyError, TypeError, ValueError):
            continue
        bars = {int(row["ts"]): row for row in market.get("bars") or []}
        for seconds_left in SECONDS:
            stamp = close - seconds_left
            spot_stamp = stamp - spot_lag_s
            bar = bars.get(stamp)
            if (
                not bar
                or spot_stamp not in spot
                or spot_stamp - 60 not in spot
            ):
                continue
            yes_ask = float(bar["a"])
            no_ask = 1.0 - float(bar["b"])
            if yes_ask >= no_ask:
                side, entry = "yes", yes_ask
                signed_buffer = (spot[spot_stamp] - strike) / strike * 100
                momentum = (
                    spot[spot_stamp] / spot[spot_stamp - 60] - 1.0
                ) * 100
                momentum_ok = momentum >= -0.04
            else:
                side, entry = "no", no_ask
                signed_buffer = (strike - spot[spot_stamp]) / strike * 100
                momentum = (
                    spot[spot_stamp] / spot[spot_stamp - 60] - 1.0
                ) * 100
                momentum_ok = momentum <= 0.04
            required = 0.10 * math.sqrt(seconds_left / 60.0)
            if not (0.94 <= entry <= 0.97):
                continue
            if signed_buffer < required or not momentum_ok:
                continue
            payout = 1.0 if market.get("result") == side else 0.0
            trades.append(
                {
                    "ticker": market["ticker"],
                    "t0": t0,
                    "day": t0 // 86400,
                    "seconds_left": seconds_left,
                    "spot_lag_s": spot_lag_s,
                    "side": side,
                    "entry": entry,
                    "spot": spot[spot_stamp],
                    "strike": strike,
                    "signed_buffer_pct": signed_buffer,
                    "required_buffer_pct": required,
                    "momentum_pct": momentum,
                    "result": market.get("result"),
                    "payout": payout,
                    "pnl": payout - entry - fee(entry),
                }
            )
            break
    return summarize(trades)


def period_pass(row: dict) -> bool:
    return bool(
        row["trades"] >= 100
        and row["one_cent_stressed_mean_c"] is not None
        and row["one_cent_stressed_mean_c"] > 0
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
    lagged_later = score(LATER_MARKETS, LATER_SPOT, spot_lag_s=60)
    lagged_earlier = score(EARLIER_MARKETS, EARLIER_SPOT, spot_lag_s=60)
    later["passes_period_gate"] = period_pass(later)
    earlier["passes_period_gate"] = period_pass(earlier)
    report = {
        "preregistration": "PREREG_resolution_rider_replication_20261006.md",
        "evidence": "historical same-minute candle paper",
        "later": later,
        "earlier_replication": earlier,
        "posthoc_60s_lag_diagnostic": {
            "later": lagged_later,
            "earlier": lagged_earlier,
        },
        "passes_both_periods": bool(
            later["passes_period_gate"] and earlier["passes_period_gate"]
        ),
    }
    OUTPUT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
