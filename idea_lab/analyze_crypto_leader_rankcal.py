"""Frozen temporally held-out softmax rank calibration for Coin Race."""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

try:
    from idea_lab.common import day_cluster_se, fee
except ModuleNotFoundError:
    from common import day_cluster_se, fee

ASSETS = ("BTC", "ETH", "SOL", "XRP", "HYPE")
KS = (5, 8, 12)
CUTOFF = int(datetime(2026, 9, 25, tzinfo=timezone.utc).timestamp())
SOURCE = Path("idea_lab/crypto_leader_history_sample.json")
DATA = Path("/Users/kapil/proj/kalshi-scalp/data/oos_20261006")
OUTPUT = Path("idea_lab/crypto_leader_rankcal_report.json")


def standardized_returns(cb: dict, t0: int, k: int) -> np.ndarray | None:
    hist: dict[str, list[float]] = {asset: [] for asset in ASSETS}
    for stamp in range(t0 - 120 * 60, t0, 60):
        if any(stamp not in cb[a] or stamp - 60 not in cb[a] for a in ASSETS):
            continue
        for asset in ASSETS:
            hist[asset].append(math.log(cb[asset][stamp] / cb[asset][stamp - 60]))
    if min(map(len, hist.values())) < 80:
        return None
    decision = t0 + 60 * (k - 1)
    if any(decision not in cb[a] or t0 - 60 not in cb[a] for a in ASSETS):
        return None
    z = []
    for asset in ASSETS:
        scale = float(np.std(hist[asset], ddof=1)) * math.sqrt(k)
        if not math.isfinite(scale) or scale <= 0:
            return None
        ret = math.log(cb[asset][decision] / cb[asset][t0 - 60])
        z.append(float(np.clip(ret / scale, -5.0, 5.0)))
    return np.asarray(z)


def targets(legs: dict) -> np.ndarray | None:
    values = []
    for asset in ASSETS:
        raw = legs[asset].get("settlement_value_dollars")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = 1.0 if legs[asset].get("result") == "yes" else 0.0
        values.append(value)
    y = np.asarray(values)
    total = float(y.sum())
    return y / total if total > 0 else None


def fit_softmax(rows: list[tuple[np.ndarray, np.ndarray]]) -> np.ndarray:
    def objective(params: np.ndarray) -> tuple[float, np.ndarray]:
        alpha = params[: len(ASSETS)]
        beta = params[-1]
        loss = 0.5 * float(params @ params)
        grad = params.copy()
        for z, y in rows:
            scores = alpha + beta * z
            scores -= scores.max()
            probs = np.exp(scores)
            probs /= probs.sum()
            loss -= float(y @ np.log(probs))
            delta = probs - y
            grad[: len(ASSETS)] += delta
            grad[-1] += float(delta @ z)
        return loss, grad

    result = minimize(
        objective,
        np.zeros(len(ASSETS) + 1),
        jac=True,
        method="L-BFGS-B",
        options={"maxiter": 2000, "ftol": 1e-12},
    )
    if not result.success:
        raise RuntimeError(result.message)
    return np.asarray(result.x)


def main() -> None:
    events = json.loads(SOURCE.read_text()).get("events") or {}
    cb = {}
    for asset in ASSETS:
        payload = json.loads((DATA / f"cb_{asset}-USD.json").read_text())
        cb[asset] = {int(row[0]): float(row[4]) for row in payload["bars"]}

    prepared = {}
    train: dict[int, list[tuple[np.ndarray, np.ndarray]]] = defaultdict(list)
    for event, event_row in sorted(events.items()):
        legs = event_row["legs"]
        t0 = int(legs["BTC"]["open_ts"])
        y = targets(legs)
        if y is None:
            continue
        prepared[event] = {"t0": t0, "legs": legs, "z": {}}
        for k in KS:
            z = standardized_returns(cb, t0, k)
            if z is not None:
                prepared[event]["z"][k] = z
                if t0 < CUTOFF:
                    train[k].append((z, y))

    params = {k: fit_softmax(train[k]) for k in KS}
    trades = []
    eligible_test = 0
    for event, row in sorted(prepared.items(), key=lambda item: item[1]["t0"]):
        t0 = row["t0"]
        if t0 < CUTOFF:
            continue
        legs = row["legs"]
        leader_bars = {
            asset: {int(bar["ts"]): bar for bar in legs[asset]["bars"]}
            for asset in ASSETS
        }
        if not row["z"]:
            continue
        eligible_test += 1
        for k in KS:
            if k not in row["z"]:
                continue
            quote_stamp = t0 + 60 * k
            if any(
                quote_stamp not in leader_bars[asset]
                or leader_bars[asset][quote_stamp].get("ask") is None
                for asset in ASSETS
            ):
                continue
            z = row["z"][k]
            model = params[k]
            scores = model[: len(ASSETS)] + model[-1] * z
            scores -= scores.max()
            probabilities = np.exp(scores)
            probabilities /= probabilities.sum()
            choices = []
            for index, asset in enumerate(ASSETS):
                ask = float(leader_bars[asset][quote_stamp]["ask"])
                if 0 < ask < 1:
                    edge = float(probabilities[index]) - ask - fee(ask)
                    choices.append((edge, asset, ask, float(probabilities[index])))
            if not choices:
                continue
            edge, asset, ask, probability = max(choices)
            if edge <= 0.10:
                continue
            raw = legs[asset].get("settlement_value_dollars")
            try:
                payout = float(raw)
            except (TypeError, ValueError):
                payout = 1.0 if legs[asset].get("result") == "yes" else 0.0
            trades.append(
                {
                    "event": event,
                    "asset": asset,
                    "t0": t0,
                    "day": t0 // 86400,
                    "k": k,
                    "z": z.tolist(),
                    "ask": ask,
                    "probability": probability,
                    "edge": edge,
                    "payout": payout,
                    "pnl": payout - ask - fee(ask),
                }
            )
            break

    pnl = np.asarray([row["pnl"] for row in trades])
    days = np.asarray([row["day"] for row in trades])
    mean = se = None
    if len(pnl):
        mean, se = day_cluster_se(pnl, days)
    split = len(trades) // 2
    by_asset: dict[str, list[float]] = defaultdict(list)
    for row in trades:
        by_asset[row["asset"]].append(row["pnl"])
    day_totals = {
        day: float(pnl[days == day].sum()) for day in sorted(set(days.tolist()))
    }
    remove_n = max(1, math.ceil(0.10 * len(day_totals))) if day_totals else 0
    removed = set(
        sorted(day_totals, key=lambda day: day_totals[day], reverse=True)[:remove_n]
    )
    trimmed = [row["pnl"] for row in trades if row["day"] not in removed]
    gross = {
        asset: sum(max(value, 0.0) for value in values)
        for asset, values in by_asset.items()
    }
    gross_total = sum(gross.values())
    halves = [
        float(np.mean(pnl[:split]) * 100) if split else None,
        float(np.mean(pnl[split:]) * 100) if len(pnl[split:]) else None,
    ]
    report = {
        "preregistration": "PREREG_crypto_leader_rankcal_20261006.md",
        "evidence": "chronological historical candle paper",
        "development_events": sum(
            1 for row in prepared.values() if row["t0"] < CUTOFF
        ),
        "test_events": sum(1 for row in prepared.values() if row["t0"] >= CUTOFF),
        "eligible_test_events": eligible_test,
        "training_rows_by_k": {str(k): len(train[k]) for k in KS},
        "parameters_by_k": {str(k): params[k].tolist() for k in KS},
        "trades": len(trades),
        "mean_c": None if mean is None else mean * 100,
        "se_c": None if se is None else se * 100,
        "lo95_c": None if mean is None else (mean - 1.96 * se) * 100,
        "one_cent_stressed_mean_c": None if mean is None else mean * 100 - 1,
        "halves_c": halves,
        "mean_ex_best_10pct_days_c": (
            float(np.mean(trimmed) * 100) if trimmed else None
        ),
        "per_asset": {
            asset: {
                "n": len(values),
                "mean_c": float(np.mean(values) * 100),
                "gross_positive_share": gross[asset] / gross_total
                if gross_total
                else None,
            }
            for asset, values in sorted(by_asset.items())
        },
        "trade_rows": trades,
    }
    report["passes_paper_screen"] = bool(
        len(trades) >= 20
        and report["mean_c"] is not None
        and report["mean_c"] > 0
        and report["one_cent_stressed_mean_c"] > 0
        and report["lo95_c"] > 0
        and all(value is not None and value > 0 for value in halves)
        and report["mean_ex_best_10pct_days_c"] is not None
        and report["mean_ex_best_10pct_days_c"] > 0
        and all(
            item["gross_positive_share"] is None
            or item["gross_positive_share"] <= 0.60
            for item in report["per_asset"].values()
        )
    )
    OUTPUT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
