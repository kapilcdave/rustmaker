"""Descriptive robustness audit of the frozen altspot OOS rule.

This does not replace or alter PREREG_altspot_tilt_20261006's decision rule.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import fee, norm_cdf

ROOT = Path("/Users/kapil/proj/kalshi-scalp/data/oos_20261006")
BASIS_BPS = {
    "BNB": 2.44,
    "DOGE": 1.07,
    "HYPE": 1.77,
    "NEAR": 4.67,
    "ZEC": 1.87,
}
ASSETS = ("NEAR", "ZEC", "HYPE")
KS = (2, 3, 5, 8)
START = 1789357500
END = 1791244800


def num(value: object) -> float:
    return float(str(value).replace(",", ""))


def trades(asset: str) -> list[dict]:
    cb = {
        int(bar[0]): float(bar[4])
        for bar in json.loads((ROOT / f"cb_{asset}-USD.json").read_text())["bars"]
    }
    markets = json.loads((ROOT / f"candles_{asset}.json").read_text())["markets"]
    out = []
    for market in markets:
        if market.get("result") not in ("yes", "no"):
            continue
        if market.get("floor_strike") in (None, "None", ""):
            continue
        open_ts = int(market["open_ts"])
        if not START <= open_ts < END:
            continue
        strike = num(market["floor_strike"]) * math.exp(-BASIS_BPS[asset] * 1e-4)
        history = [
            cb[open_ts - 60 * i]
            for i in range(120, 0, -1)
            if open_ts - 60 * i in cb
        ]
        if len(history) < 40:
            continue
        sigma = float(np.std(np.diff(np.log(history))))
        if sigma < 2e-5:
            continue
        bars = {
            int(bar["ts"]): bar
            for bar in market["bars"]
            if not bar.get("post_close")
        }
        yes = market["result"] == "yes"
        for k in KS:
            bar = bars.get(open_ts + 60 * k)
            spot = cb.get(open_ts + 60 * (k - 1))
            if (
                not bar
                or spot is None
                or bar.get("a") is None
                or bar.get("b") is None
            ):
                continue
            fair = float(
                norm_cdf(
                    math.log(spot / strike)
                    / (sigma * math.sqrt(max(15 - k, 0.5)))
                )
            )
            if fair - float(bar["a"]) > 0.10:
                side, price, win = "yes", float(bar["a"]), yes
            elif float(bar["b"]) - fair > 0.10:
                side, price, win = "no", 1.0 - float(bar["b"]), not yes
            else:
                continue
            out.append(
                {
                    "asset": asset,
                    "ticker": market["ticker"],
                    "open_ts": open_ts,
                    "day": open_ts // 86400,
                    "k": k,
                    "side": side,
                    "price": price,
                    "fair": fair if side == "yes" else 1.0 - fair,
                    "win": win,
                    "pnl": float(win) - price - fee(price),
                }
            )
            break
    return out


def bootstrap_day_mean(rows: list[dict], stress: float, draws: int = 20_000) -> list[float]:
    by_day: dict[int, list[float]] = defaultdict(list)
    for row in rows:
        by_day[row["day"]].append(row["pnl"] - stress)
    groups = list(by_day.values())
    rng = np.random.default_rng(20261006)
    sims = []
    for _ in range(draws):
        chosen = rng.integers(0, len(groups), len(groups))
        flat = [value for index in chosen for value in groups[index]]
        sims.append(float(np.mean(flat)))
    return [float(x) for x in np.quantile(sims, [0.025, 0.975])]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets", default=",".join(ASSETS))
    ap.add_argument(
        "--output",
        type=Path,
        default=Path("idea_lab/altspot_oos_robustness_report.json"),
    )
    args = ap.parse_args()
    assets = tuple(x.strip().upper() for x in args.assets.split(",") if x.strip())
    rows = [row for asset in assets for row in trades(asset)]
    rows.sort(key=lambda row: (row["open_ts"], row["asset"]))
    days = sorted({row["day"] for row in rows})
    half = len(rows) // 2
    by_day: dict[int, float] = defaultdict(float)
    by_day_gross: dict[int, float] = defaultdict(float)
    for row in rows:
        by_day[row["day"]] += row["pnl"]
        by_day_gross[row["day"]] += max(0.0, row["pnl"])
    remove_n = max(1, math.ceil(0.10 * len(days)))
    removed = {
        day
        for day, _ in sorted(
            by_day_gross.items(), key=lambda item: item[1], reverse=True
        )[:remove_n]
    }
    report = {
        "rule": "PREREG_altspot_tilt_20261006.md unchanged",
        "evidence": "historical same-close non-atomic candle quotes",
        "trades": len(rows),
        "utc_days": len(days),
        "mean_pnl_c": 100 * float(np.mean([row["pnl"] for row in rows])),
        "day_bootstrap_ci_c": [
            100 * x for x in bootstrap_day_mean(rows, 0.0)
        ],
        "stress": {
            str(c): {
                "mean_c": 100 * float(np.mean([row["pnl"] - c / 100 for row in rows])),
                "day_bootstrap_ci_c": [
                    100 * x for x in bootstrap_day_mean(rows, c / 100)
                ],
            }
            for c in (0, 1, 2)
        },
        "chronological_halves_c": [
            100 * float(np.mean([row["pnl"] for row in rows[:half]])),
            100 * float(np.mean([row["pnl"] for row in rows[half:]])),
        ],
        "mean_after_removing_best_10pct_days_c": 100
        * float(np.mean([row["pnl"] for row in rows if row["day"] not in removed])),
        "largest_day_share_gross_positive_pnl": max(by_day_gross.values())
        / sum(by_day_gross.values()),
        "trades_by_k": {
            str(k): sum(row["k"] == k for row in rows) for k in KS
        },
        "win_rate": float(np.mean([row["win"] for row in rows])),
        "mean_price_c": 100 * float(np.mean([row["price"] for row in rows])),
        "per_asset": {
            asset: {
                "trades": len(selected := [row for row in rows if row["asset"] == asset]),
                "mean_pnl_c": 100 * float(np.mean([row["pnl"] for row in selected])),
            }
            for asset in assets
        },
        "rows": rows,
    }
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True))
    printable = dict(report)
    printable.pop("rows")
    print(json.dumps(printable, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
