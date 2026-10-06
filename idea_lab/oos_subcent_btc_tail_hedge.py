"""Score PREREG_subcent_btc_tail_hedge_20261006.md from the base OOS rows."""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

try:
    from idea_lab.oos_subcent_sweep import ASSETS, END, START, STRESS, jobs_for
except ModuleNotFoundError:
    from oos_subcent_sweep import ASSETS, END, START, STRESS, jobs_for


def batch_taker_fee(price: float, contracts: int) -> float:
    return math.ceil(0.07 * contracts * price * (1.0 - price) * 100.0 - 1e-9) / 100.0


def max_drawdown(values: list[float]) -> float:
    equity = np.cumsum(np.asarray(values, dtype=float))
    if not len(equity):
        return 0.0
    peaks = np.maximum.accumulate(np.concatenate(([0.0], equity)))
    padded = np.concatenate(([0.0], equity))
    return float(np.max(peaks - padded))


def btc_quotes(root: Path) -> dict[int, dict]:
    data = json.loads((root / "candles_BTC.json").read_text())
    out = {}
    for market in data.get("markets") or []:
        open_ts = int(market["open_ts"])
        if not START <= open_ts < END or market.get("result") not in ("yes", "no"):
            continue
        bars = {
            (int(bar["ts"]) - open_ts) // 60: bar
            for bar in market.get("bars") or []
            if not bar.get("post_close")
        }
        bar = bars.get(14)
        if not bar or bar.get("a") is None or bar.get("b") is None:
            continue
        out[open_ts] = {
            "ticker": market["ticker"],
            "result": market["result"],
            "yes_ask": float(bar["a"]),
            "no_ask": 1.0 - float(bar["b"]),
        }
    return out


def bootstrap(values: list[float], draws: int = 20_000) -> list[float]:
    arr = np.asarray(values, dtype=float)
    rng = np.random.default_rng(20261006)
    sims = arr[rng.integers(0, len(arr), size=(draws, len(arr)))].mean(axis=1)
    return [float(x) for x in np.quantile(sims, [0.025, 0.975])]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--base-report",
        type=Path,
        default=Path("idea_lab/oos_subcent_sweep_report.json"),
    )
    ap.add_argument(
        "--root",
        type=Path,
        default=Path("/Users/kapil/proj/kalshi-scalp/data/oos_20261006"),
    )
    ap.add_argument(
        "--output",
        type=Path,
        default=Path("idea_lab/oos_subcent_btc_tail_hedge_report.json"),
    )
    args = ap.parse_args()

    base = json.loads(args.base_report.read_text())
    rows = base["rows"]
    join_lookup = {}
    for asset in ASSETS:
        jobs, _ = jobs_for(asset, args.root)
        for job in jobs:
            minute = next(iter(job["join"].values()))
            join_lookup[(job["ticker"], minute)] = (
                job["side"][minute],
                int(job["open_ts"]),
            )

    slots: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        minute = int(row["join_min"])
        side, open_ts = join_lookup[(row["ticker"], minute)]
        slots[open_ts].append(
            {**row, "side": side}
        )
    btc = btc_quotes(args.root)
    days = list(range(START // 86400, END // 86400))
    base_day: dict[int, float] = defaultdict(float)
    combined_day: dict[int, float] = defaultdict(float)
    hedge_rows = []

    for slot, slot_rows in slots.items():
        for row in slot_rows:
            pnl = float(row["filled"]) * (
                0.009 - float(row["hit"]) - STRESS
            )
            base_day[int(row["day"])] += pnl
            combined_day[int(row["day"])] += pnl
        filled_rows = [row for row in slot_rows if float(row["filled"]) > 0]
        assets = {row["asset"] for row in filled_rows}
        if len(assets) < 3 or slot not in btc:
            continue
        by_side = {
            side: sum(float(row["filled"]) for row in filled_rows if row["side"] == side)
            for side in ("yes", "no")
        }
        dominant, exposure = max(by_side.items(), key=lambda kv: kv[1])
        total = sum(by_side.values())
        if total <= 0 or exposure / total < 0.75:
            continue
        quote = btc[slot]
        price = quote[f"{dominant}_ask"]
        if price > 0.02 + 1e-12:
            continue
        contracts = min(200, int(exposure // 3))
        if contracts <= 0:
            continue
        fee = batch_taker_fee(price, contracts)
        payout = contracts if quote["result"] == dominant else 0.0
        pnl = payout - contracts * (price + 0.002) - fee
        day = slot // 86400
        combined_day[day] += pnl
        hedge_rows.append(
            {
                "open_ts": slot,
                "day": day,
                "btc_ticker": quote["ticker"],
                "side": dominant,
                "alt_filled_contracts": total,
                "dominant_filled_contracts": exposure,
                "contracts": contracts,
                "price": price,
                "fee": fee,
                "won": quote["result"] == dominant,
                "stressed_pnl": pnl,
            }
        )

    base_values = [base_day.get(day, 0.0) for day in days]
    combined_values = [combined_day.get(day, 0.0) for day in days]
    half = len(days) // 2
    halves = [
        float(np.mean(combined_values[:half])),
        float(np.mean(combined_values[half:])),
    ]
    base_total = sum(base_values)
    combined_total = sum(combined_values)
    base_worst = min(base_values)
    combined_worst = min(combined_values)
    ci = bootstrap(combined_values)
    complete = bool(
        base.get("complete_population")
        and (args.root / "candles_BTC.json").exists()
        and len(btc) > 0
    )
    passes = bool(
        complete
        and base.get("capped_histories") == 0
        and len(hedge_rows) >= 10
        and sum(row["won"] for row in hedge_rows) >= 3
        and all(x > 0 for x in halves)
        and ci[0] > 0
        and combined_worst >= 0.8 * base_worst
        and max_drawdown(combined_values) < max_drawdown(base_values)
        and combined_total >= 0.5 * base_total
    )
    report = {
        "preregistration": "PREREG_subcent_btc_tail_hedge_20261006.md",
        "complete_population": complete,
        "hedge_entries": len(hedge_rows),
        "winning_hedges": sum(row["won"] for row in hedge_rows),
        "base_stressed_total": base_total,
        "combined_stressed_total": combined_total,
        "combined_stressed_pnl_per_day": combined_total / len(days),
        "combined_day_cluster_ci": ci,
        "combined_chronological_halves": halves,
        "base_largest_losing_day": base_worst,
        "combined_largest_losing_day": combined_worst,
        "base_max_drawdown": max_drawdown(base_values),
        "combined_max_drawdown": max_drawdown(combined_values),
        "passes_oos_overlay_gate": passes,
        "hedges": hedge_rows,
    }
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
