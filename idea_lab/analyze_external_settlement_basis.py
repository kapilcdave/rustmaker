"""Causal pre-period settlement-basis probit, frozen before local OOS scoring."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.special import ndtr

try:
    from idea_lab.common import day_cluster_se, fee
except ModuleNotFoundError:
    from common import day_cluster_se, fee


TRAIN_MARKETS = Path("/Users/kapil/proj/kalshi-scalp/data/kxbtc15m_candles.json")
TRAIN_SPOT = Path("/Users/kapil/proj/kalshi-scalp/data/cb_btc_1m.json")
TEST_MARKETS = Path(
    "/Users/kapil/proj/kalshi-scalp/data/oos_20261006/candles_BTC.json"
)
TEST_SPOT = Path(
    "/Users/kapil/proj/kalshi-scalp/data/oos_20261006/cb_BTC-USD.json"
)
OUTPUT = Path("idea_lab/external_settlement_basis_report.json")


def load_spot(path: Path) -> dict[int, float]:
    payload = json.loads(path.read_text())
    rows = payload.get("bars") if isinstance(payload, dict) else payload
    return {int(row[0]): float(row[4]) for row in rows}


def samples(markets_path: Path, spot_path: Path) -> list[dict]:
    markets = json.loads(markets_path.read_text())["markets"]
    spot = load_spot(spot_path)
    rows = []
    for market in sorted(markets, key=lambda row: int(row["open_ts"])):
        if market.get("result") not in ("yes", "no"):
            continue
        close = int(market["close_ts"])
        quote_ts = close - 60
        causal_spot_ts = quote_ts - 60
        bars = {
            int(row["ts"]): row
            for row in market.get("bars") or []
            if not row.get("post_close")
        }
        quote = bars.get(quote_ts)
        if quote is None or causal_spot_ts not in spot:
            continue
        try:
            strike = float(market["floor_strike"])
        except (KeyError, TypeError, ValueError):
            continue
        rows.append(
            {
                "ticker": market["ticker"],
                "close_ts": close,
                "day": close // 86400,
                "distance": spot[causal_spot_ts] - strike,
                "spot": spot[causal_spot_ts],
                "strike": strike,
                "yes_ask": float(quote["a"]),
                "no_ask": 1.0 - float(quote["b"]),
                "outcome": 1 if market["result"] == "yes" else 0,
                "result": market["result"],
            }
        )
    return rows


def fit_probit(rows: list[dict]) -> dict:
    distance = np.asarray([row["distance"] for row in rows], dtype=float)
    outcome = np.asarray([row["outcome"] for row in rows], dtype=float)

    def objective(theta: np.ndarray) -> float:
        offset, log_scale = theta
        scale = math.exp(float(log_scale))
        probability = np.clip(ndtr((distance + offset) / scale), 1e-8, 1 - 1e-8)
        return float(
            -np.sum(outcome * np.log(probability) + (1 - outcome) * np.log1p(-probability))
        )

    initial_scale = max(10.0, float(np.std(distance)))
    result = minimize(
        objective,
        np.asarray([0.0, math.log(initial_scale)]),
        method="L-BFGS-B",
        bounds=[(-500.0, 500.0), (math.log(1.0), math.log(2000.0))],
    )
    offset = float(result.x[0])
    scale = math.exp(float(result.x[1]))
    probability = np.clip(ndtr((distance + offset) / scale), 1e-8, 1 - 1e-8)
    return {
        "n": len(rows),
        "offset_dollars": offset,
        "scale_dollars": scale,
        "negative_log_likelihood": objective(result.x),
        "brier": float(np.mean((probability - outcome) ** 2)),
        "converged": bool(result.success),
        "message": str(result.message),
    }


def summarize(trades: list[dict]) -> dict:
    pnl = np.asarray([row["pnl"] for row in trades], dtype=float)
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
        "by_side": {},
        "rows": trades,
    }
    for side in ("yes", "no"):
        values = [row["pnl"] for row in trades if row["side"] == side]
        report["by_side"][side] = {
            "trades": len(values),
            "mean_c": float(np.mean(values) * 100) if values else None,
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


def score(rows: list[dict], model: dict) -> dict:
    trades = []
    offset = model["offset_dollars"]
    scale = model["scale_dollars"]
    for row in rows:
        p_yes = float(ndtr((row["distance"] + offset) / scale))
        candidates = [
            ("yes", row["yes_ask"], p_yes),
            ("no", row["no_ask"], 1.0 - p_yes),
        ]
        side, entry, probability = max(
            candidates, key=lambda item: item[2] - item[1] - fee(item[1])
        )
        modeled_edge = probability - entry - fee(entry)
        if modeled_edge < 0.02:
            continue
        payout = 1.0 if row["result"] == side else 0.0
        trades.append(
            {
                **row,
                "side": side,
                "entry": entry,
                "predicted_yes": p_yes,
                "modeled_edge": modeled_edge,
                "payout": payout,
                "pnl": payout - entry - fee(entry),
            }
        )
    return summarize(trades)


def main() -> None:
    train_rows = samples(TRAIN_MARKETS, TRAIN_SPOT)
    test_rows = samples(TEST_MARKETS, TEST_SPOT)
    model = fit_probit(train_rows)
    report = {
        "preregistration": "PREREG_external_settlement_basis_oos_20261006.md",
        "evidence": "causal_completed_spot_plus_actual_kalshi_ask_candle",
        "decision_seconds_before_close": 60,
        "spot_lag_seconds": 60,
        "train_rows": len(train_rows),
        "test_rows": len(test_rows),
        "model": model,
        "result": score(test_rows, model),
    }
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
