"""Causal post-publication replication of brandononchain/kalshibot's GBM rule."""
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
OUTPUT = Path("idea_lab/external_brandon_gbm_report.json")
SOURCE_COMMIT = "a01af900f96a6038db081de18ebbb1e98cb3cbb2"
POST_PUBLICATION_TS = int(
    datetime(2026, 9, 25, tzinfo=timezone.utc).timestamp()
)


def normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def spot_rows(path: Path) -> dict[int, list]:
    return {int(row[0]): row for row in json.loads(path.read_text())["bars"]}


def model_probability(
    rows: dict[int, list], t0: int, decision_ts: int
) -> tuple[float, dict] | None:
    """Use only Coinbase bars completed by the Kalshi decision timestamp."""
    opening = rows.get(t0)
    latest = rows.get(decision_ts - 60)
    if opening is None or latest is None:
        return None
    open_price = float(opening[3])
    current_price = float(latest[4])
    closes = []
    for offset in range(16, 0, -1):
        row = rows.get(decision_ts - offset * 60)
        if row is not None:
            closes.append(float(row[4]))
    if len(closes) < 10 or open_price <= 0 or current_price <= 0:
        return None
    returns = np.diff(np.log(np.asarray(closes, dtype=float)))
    sigma = float(np.std(returns, ddof=0) * math.sqrt(15.0))
    remaining_fraction = max(0.001, (t0 + 900 - decision_ts) / 900.0)
    remaining_sigma = sigma * math.sqrt(remaining_fraction)
    move = (current_price - open_price) / open_price
    if remaining_sigma < 0.00001:
        probability = 0.99 if move > 0 else 0.01
    else:
        probability = min(
            0.99, max(0.01, normal_cdf(move / remaining_sigma))
        )
    return probability, {
        "open_price": open_price,
        "current_price": current_price,
        "move": move,
        "sigma_15m": sigma,
        "remaining_sigma": remaining_sigma,
        "spot_timestamp": decision_ts - 60,
    }


def score(
    markets: list[dict],
    spot: dict[int, list],
    *,
    min_edge: float,
    max_minute: int,
    slippage: float = 0.01,
) -> list[dict]:
    trades = []
    for market in sorted(markets, key=lambda row: int(row["open_ts"])):
        t0 = int(market["open_ts"])
        if t0 < POST_PUBLICATION_TS or market.get("result") not in ("yes", "no"):
            continue
        bars = {
            int(row["ts"]): row
            for row in market.get("bars") or []
            if not row.get("post_close")
        }
        for minute in range(1, max_minute + 1):
            decision_ts = t0 + minute * 60
            found = model_probability(spot, t0, decision_ts)
            quote = bars.get(decision_ts)
            if found is None or quote is None:
                continue
            probability_up, diagnostics = found
            candidates = []
            for side, ask, probability in (
                ("yes", float(quote["a"]), probability_up),
                ("no", 1.0 - float(quote["b"]), 1.0 - probability_up),
            ):
                if not 0.35 <= ask <= 0.65:
                    continue
                entry = ask + slippage
                if entry >= 1.0:
                    continue
                edge = probability - entry - fee(entry)
                if edge > min_edge:
                    candidates.append((edge, side, ask, entry, probability))
            if not candidates:
                continue
            edge, side, ask, entry, probability = max(candidates)
            payout = 1.0 if market["result"] == side else 0.0
            trades.append(
                {
                    "ticker": market["ticker"],
                    "t0": t0,
                    "day": t0 // 86400,
                    "minute": minute,
                    "side": side,
                    "ask": ask,
                    "entry": entry,
                    "model_probability": probability,
                    "model_edge": edge,
                    "result": market["result"],
                    "pnl": payout - entry - fee(entry),
                    **diagnostics,
                }
            )
            break
    return trades


def summarize(rows: list[dict]) -> dict:
    values = np.asarray([row["pnl"] for row in rows], dtype=float)
    days = np.asarray([row["day"] for row in rows])
    if not len(values):
        return {
            "trades": 0,
            "mean_c": None,
            "clustered_low_c": None,
            "passes": False,
            "rows": [],
        }
    mean, se = day_cluster_se(values, days)
    half = len(values) // 2
    by_day_positive: dict[int, float] = defaultdict(float)
    for row in rows:
        by_day_positive[row["day"]] += max(0.0, row["pnl"])
    gross_positive = sum(by_day_positive.values())
    max_day_share = (
        max(by_day_positive.values()) / gross_positive
        if gross_positive
        else None
    )
    report = {
        "trades": len(rows),
        "wins": int(np.sum(values > 0)),
        "mean_c": float(mean * 100),
        "clustered_se_c": float(se * 100),
        "clustered_low_c": float((mean - 1.96 * se) * 100),
        "additional_1c_stress_mean_c": float(mean * 100 - 1.0),
        "halves_c": [
            float(np.mean(values[:half]) * 100) if half else None,
            float(np.mean(values[half:]) * 100) if len(values[half:]) else None,
        ],
        "max_day_share_gross_positive": max_day_share,
        "rows": rows,
    }
    report["passes"] = bool(
        len(rows) >= 100
        and report["mean_c"] > 0
        and report["clustered_low_c"] > 0
        and report["additional_1c_stress_mean_c"] > 0
        and all(value is not None and value > 0 for value in report["halves_c"])
        and max_day_share is not None
        and max_day_share <= 0.60
    )
    return report


def main() -> None:
    markets = json.loads(MARKETS.read_text())["markets"]
    spot = spot_rows(SPOT)
    report = {
        "preregistration": "PREREG_external_brandon_gbm_oos_20261006.md",
        "source_commit": SOURCE_COMMIT,
        "post_publication_start_utc": "2026-09-25T00:00:00Z",
        "evidence": "causal minute-resolution actual-ask proxy; not fills",
        "source_capture_simulator_governing": summarize(
            score(markets, spot, min_edge=0.15, max_minute=4)
        ),
        "source_runtime_env_diagnostic": summarize(
            score(markets, spot, min_edge=0.08, max_minute=10)
        ),
    }
    OUTPUT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

