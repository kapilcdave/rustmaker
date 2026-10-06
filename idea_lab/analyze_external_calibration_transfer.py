"""Transfer a pre-period market calibration map to local OOS execution."""
from __future__ import annotations

import datetime as dt
import gzip
import json
import math
from pathlib import Path

import numpy as np

try:
    from idea_lab.common import day_cluster_se, fee
except ModuleNotFoundError:
    from common import day_cluster_se, fee


HORIZONS = (10, 5, 3, 2, 1)
TRAIN = Path(
    "/tmp/kalshi_ext_20261006_7/"
    "theruviparambil_kalshi-btc-15m/data/windows.jsonl.gz"
)
TEST = Path(
    "/Users/kapil/proj/kalshi-scalp/data/oos_20261006/candles_BTC.json"
)
OUTPUT = Path("idea_lab/external_calibration_transfer_report.json")
SOURCE_COMMIT = "ed3230f21b0cb2b66f5b870e94695c1fd1e3bdc2"


def sigmoid(value: np.ndarray | float) -> np.ndarray | float:
    return 1.0 / (1.0 + np.exp(-np.clip(value, -35.0, 35.0)))


def logit(value: float) -> float:
    value = min(1.0 - 1e-9, max(1e-9, value))
    return math.log(value / (1.0 - value))


def fit_logistic(mids: list[float], outcomes: list[int]) -> dict:
    x = np.asarray([logit(value) for value in mids], dtype=float)
    y = np.asarray(outcomes, dtype=float)
    intercept = 0.0
    slope = 1.0
    converged = False
    for iteration in range(100):
        p = sigmoid(np.clip(intercept + slope * x, -30.0, 30.0))
        weights = np.maximum(p * (1.0 - p), 1e-8)
        residual = y - p
        g0 = float(np.sum(residual))
        g1 = float(np.sum(residual * x)) - 1e-3 * slope
        h00 = float(np.sum(weights))
        h01 = float(np.sum(weights * x))
        h11 = float(np.sum(weights * x * x)) + 1e-3
        determinant = h00 * h11 - h01 * h01
        if abs(determinant) < 1e-12:
            break
        step0 = max(-1.0, min(1.0, (h11 * g0 - h01 * g1) / determinant))
        step1 = max(-1.0, min(1.0, (-h01 * g0 + h00 * g1) / determinant))
        intercept += step0
        slope += step1
        if abs(step0) + abs(step1) < 1e-10:
            converged = True
            break
    return {
        "n": len(mids),
        "intercept": intercept,
        "slope": slope,
        "iterations": iteration + 1,
        "converged": converged,
    }


def fit_models() -> dict[int, dict]:
    sample = {horizon: ([], []) for horizon in HORIZONS}
    with gzip.open(TRAIN, "rt") as handle:
        for line in handle:
            market = json.loads(line)
            for horizon in HORIZONS:
                quote = market.get("q", {}).get(str(horizon))
                if not quote:
                    continue
                mid = (float(quote[0]) + float(quote[1])) / 2.0
                if not 0.05 < mid < 0.95:
                    continue
                sample[horizon][0].append(mid)
                sample[horizon][1].append(int(market["y"]))
    return {
        horizon: fit_logistic(*sample[horizon])
        for horizon in HORIZONS
    }


def predicted_yes(model: dict, mid: float) -> float:
    return float(sigmoid(model["intercept"] + model["slope"] * logit(mid)))


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
    report = {
        "trades": len(trades),
        "wins": sum(row["payout"] > 0 for row in trades),
        "losses": sum(row["payout"] == 0 for row in trades),
        "mean_c": None if mean is None else mean * 100,
        "lo95_c": None if mean is None else (mean - 1.96 * se) * 100,
        "extra_1c_stress_c": None if mean is None else mean * 100 - 1,
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
        "by_horizon": {},
        "by_side": {},
        "rows": trades,
    }
    for field in ("horizon", "side"):
        target = report[f"by_{field}"]
        for value in sorted(set(row[field] for row in trades), key=str):
            values = [row["pnl"] for row in trades if row[field] == value]
            target[str(value)] = {
                "trades": len(values),
                "mean_c": float(np.mean(values) * 100),
            }
    report["passes_gate"] = bool(
        report["trades"] >= 100
        and report["extra_1c_stress_c"] is not None
        and report["extra_1c_stress_c"] > 0
        and report["lo95_c"] is not None
        and report["lo95_c"] > 0
        and all(value is not None and value > 0 for value in report["halves_c"])
        and report["mean_ex_best_10pct_days_c"] is not None
        and report["mean_ex_best_10pct_days_c"] > 0
        and report["max_day_gross_positive_share"] is not None
        and report["max_day_gross_positive_share"] <= 0.20
    )
    return report


def score(models: dict[int, dict]) -> tuple[int, list[dict]]:
    markets = sorted(
        json.loads(TEST.read_text())["markets"],
        key=lambda row: int(row["open_ts"]),
    )
    trades = []
    for market in markets:
        if market.get("result") not in ("yes", "no"):
            continue
        close_ts = int(market["close_ts"])
        bars = {
            int(row["ts"]): row
            for row in market.get("bars") or []
            if not row.get("post_close")
        }
        for horizon in HORIZONS:
            quote = bars.get(close_ts - horizon * 60)
            if not quote:
                continue
            yes_bid = float(quote["b"])
            yes_ask = float(quote["a"])
            mid = (yes_bid + yes_ask) / 2.0
            if not 0.05 < mid < 0.95:
                continue
            p_yes = predicted_yes(models[horizon], mid)
            no_ask = 1.0 - yes_bid
            candidates = [
                ("yes", yes_ask, p_yes),
                ("no", no_ask, 1.0 - p_yes),
            ]
            side, entry, p_side = max(
                candidates,
                key=lambda value: value[2] - value[1] - fee(value[1]),
            )
            model_edge = p_side - entry - fee(entry)
            if model_edge < 0.02:
                continue
            payout = 1.0 if market["result"] == side else 0.0
            trades.append(
                {
                    "ticker": market["ticker"],
                    "close_ts": close_ts,
                    "day": close_ts // 86400,
                    "horizon": horizon,
                    "side": side,
                    "mid": mid,
                    "predicted_p_yes": p_yes,
                    "entry": entry,
                    "model_edge": model_edge,
                    "result": market["result"],
                    "payout": payout,
                    "pnl": payout - entry - fee(entry),
                }
            )
            break
    return len(markets), trades


def main() -> None:
    models = fit_models()
    market_count, trades = score(models)
    report = {
        "preregistration": "PREREG_external_calibration_transfer_oos_20261006.md",
        "source_commit": SOURCE_COMMIT,
        "train_period": "2026-06-27_to_2026-09-03",
        "test_period": "local_2026-09-14_to_2026-10-06",
        "horizons": list(HORIZONS),
        "minimum_modeled_edge_c": 2.0,
        "models": {str(key): value for key, value in models.items()},
        "market_count": market_count,
        "result": summarize(trades),
    }
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
