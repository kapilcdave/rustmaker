"""Frozen covariance-Monte-Carlo Coin Race screen."""
from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

from common import day_cluster_se, fee

ASSETS = ("BTC", "ETH", "SOL", "XRP", "HYPE")
KS = (5, 8, 12)
DRAWS = 20_000
SEED = 20261006
SOURCE = Path("idea_lab/crypto_leader_history_sample.json")
DATA = Path("/Users/kapil/proj/kalshi-scalp/data/oos_20261006")
OUTPUT = Path("idea_lab/crypto_leader_covariance_report.json")


def main() -> None:
    events = json.loads(SOURCE.read_text()).get("events") or {}
    cb = {}
    for asset in ASSETS:
        payload = json.loads((DATA / f"cb_{asset}-USD.json").read_text())
        cb[asset] = {int(row[0]): float(row[4]) for row in payload["bars"]}
    trades = []
    eligible_events = 0
    for event, event_row in sorted(events.items()):
        legs = event_row["legs"]
        t0 = int(legs["BTC"]["open_ts"])
        if any(t0 - 60 not in cb[asset] for asset in ASSETS):
            continue
        # Aligned historical one-minute return vectors strictly before open.
        hist = []
        for stamp in range(t0 - 120 * 60, t0, 60):
            vector = []
            good = True
            for asset in ASSETS:
                if stamp not in cb[asset] or stamp - 60 not in cb[asset]:
                    good = False
                    break
                vector.append(math.log(cb[asset][stamp] / cb[asset][stamp - 60]))
            if good:
                hist.append(vector)
        if len(hist) < 80:
            continue
        covariance = np.cov(np.asarray(hist).T, ddof=1)
        if not np.all(np.isfinite(covariance)):
            continue
        eligible_events += 1
        leader_bars = {
            asset: {int(row["ts"]): row for row in legs[asset]["bars"]}
            for asset in ASSETS
        }
        starts = np.asarray([cb[asset][t0 - 60] for asset in ASSETS])
        for k in KS:
            decision_candle = t0 + 60 * (k - 1)
            quote_stamp = t0 + 60 * k
            if any(decision_candle not in cb[asset] for asset in ASSETS):
                continue
            if any(
                quote_stamp not in leader_bars[asset]
                or leader_bars[asset][quote_stamp].get("ask") is None
                for asset in ASSETS
            ):
                continue
            current = np.log(
                np.asarray([cb[asset][decision_candle] for asset in ASSETS]) / starts
            )
            rng = np.random.default_rng(SEED + t0 + k)
            scaled = 0.5 * (covariance + covariance.T) * max(15 - k, 0.5)
            eigenvalues, eigenvectors = np.linalg.eigh(scaled)
            root = eigenvectors @ np.diag(np.sqrt(np.maximum(eigenvalues, 0.0)))
            # NumPy's Accelerate-backed small-matrix matmul emits spurious
            # divide-by-zero/overflow warnings on this host even when every
            # operand is finite and O(1e-3).  The explicit contraction is the
            # identical linear transform without that broken BLAS path.
            standard = rng.standard_normal((DRAWS, len(ASSETS)))
            remaining = np.sum(
                standard[:, None, :] * root[None, :, :],
                axis=2,
            )
            terminal = current + remaining
            winners = np.argmax(terminal, axis=1)
            probabilities = np.bincount(winners, minlength=len(ASSETS)) / DRAWS
            choices = []
            for index, asset in enumerate(ASSETS):
                ask = float(leader_bars[asset][quote_stamp]["ask"])
                if not 0 < ask < 1:
                    continue
                edge = float(probabilities[index]) - ask - fee(ask)
                choices.append((edge, asset, ask, float(probabilities[index])))
            if not choices:
                continue
            edge, asset, ask, probability = max(choices)
            if edge <= 0.10:
                continue
            settlement = legs[asset].get("settlement_value_dollars")
            try:
                payout = float(settlement)
            except (TypeError, ValueError):
                payout = 1.0 if legs[asset].get("result") == "yes" else 0.0
            trades.append(
                {
                    "event": event,
                    "asset": asset,
                    "t0": t0,
                    "day": t0 // 86400,
                    "k": k,
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
    gross = {asset: sum(max(value, 0) for value in values) for asset, values in by_asset.items()}
    gross_total = sum(gross.values())
    report = {
        "preregistration": "PREREG_crypto_leader_covariance_20261006.md",
        "evidence": "historical Coinbase/Kalshi same-minute candle paper",
        "sample_events": len(events),
        "eligible_events": eligible_events,
        "trades": len(trades),
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
        "per_asset": {
            asset: {
                "n": len(values),
                "mean_c": float(np.mean(values) * 100),
                "gross_positive_share": gross[asset] / gross_total if gross_total else None,
            }
            for asset, values in sorted(by_asset.items())
        },
        "trade_rows": trades,
    }
    report["passes_paper_screen"] = bool(
        len(trades) >= 40
        and report["lo95_c"] is not None
        and report["lo95_c"] > 0
        and all(value is not None and value > 0 for value in report["halves_c"])
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
