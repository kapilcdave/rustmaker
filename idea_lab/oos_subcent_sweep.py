"""Fresh OOS score for PREREG_subcent_sweep_oos_20261006.md."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, "/Users/kapil/proj/kalshi-scalp")
from research_subcent_fills_from_sweeps import Http, collect  # type: ignore

ASSETS = ("ETH", "SOL", "XRP", "BNB", "ZEC", "DOGE", "HYPE", "NEAR")
START = 1789430400  # 2026-09-15T00:00:00Z
END = 1791244800  # 2026-10-06T00:00:00Z, exclusive
DC = 9
PRICE = DC / 1000.0
STRESS = 0.001


def jobs_for(asset: str, root: Path) -> tuple[list[dict], dict[int, int]]:
    path = root / f"candles_{asset}.json"
    data = json.loads(path.read_text())
    jobs = []
    coverage: dict[int, int] = defaultdict(int)
    for market in data.get("markets") or []:
        open_ts = int(market["open_ts"])
        if not START <= open_ts < END:
            continue
        if market.get("result") not in ("yes", "no"):
            continue
        coverage[open_ts // 86400] += 1
        bars = sorted(
            (b for b in market.get("bars") or [] if not b.get("post_close")),
            key=lambda b: b["ts"],
        )
        chosen = None
        for i, bar in enumerate(bars):
            bid, ask = bar.get("b"), bar.get("a")
            if bid is None or ask is None or ask <= bid:
                continue
            minute = (int(bar["ts"]) - open_ts) // 60
            if not 12 <= minute <= 14:
                continue
            if 0.5 * (bid + ask) < 0.5:
                cheap_ask = ask
                side = "yes"
                hit = market["result"] == "yes"
            else:
                cheap_ask = 1.0 - bid
                side = "no"
                hit = market["result"] == "no"
            if int(round(float(cheap_ask) * 1000.0)) != DC:
                continue
            next_dc = None
            if i + 1 < len(bars):
                nxt = bars[i + 1]
                if nxt.get("b") is not None and nxt.get("a") is not None:
                    next_ask = nxt["a"] if side == "yes" else 1.0 - nxt["b"]
                    next_dc = int(round(float(next_ask) * 1000.0))
            chosen = {
                "ticker": market["ticker"],
                "asset": asset,
                "day": open_ts // 86400,
                "open_ts": open_ts,
                "join": {DC: int(minute)},
                "side": {int(minute): side},
                "hit": {int(minute): float(hit)},
                "walk": {
                    int(minute): None if next_dc is None else bool(next_dc > DC)
                },
            }
            break
        if chosen:
            jobs.append(chosen)
    return jobs, dict(coverage)


def day_bootstrap(
    rows: list[dict], days: list[int], stress: float, draws: int = 20_000
) -> list[float]:
    by_day: dict[int, float] = defaultdict(float)
    for row in rows:
        by_day[int(row["day"])] += float(row["filled"]) * (
            PRICE - float(row["hit"]) - stress
        )
    for day in days:
        by_day.setdefault(day, 0.0)
    values = np.asarray([by_day[d] for d in days], dtype=float)
    rng = np.random.default_rng(20261006)
    sims = values[rng.integers(0, len(values), size=(draws, len(values)))].mean(axis=1)
    return [float(x) for x in np.quantile(sims, [0.025, 0.975])]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--root", type=Path, default=Path("/Users/kapil/proj/kalshi-scalp/data/oos_20261006")
    )
    ap.add_argument("--assets", default=",".join(ASSETS))
    ap.add_argument(
        "--strikes",
        type=Path,
        default=Path("/Users/kapil/proj/kalshi-scalp/data/oos_20261006/strikes.json"),
    )
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--output", type=Path, default=Path("idea_lab/oos_subcent_sweep_report.json"))
    args = ap.parse_args()
    assets = tuple(x.strip().upper() for x in args.assets.split(",") if x.strip())

    jobs = []
    available = []
    coverage: dict[str, dict[int, int]] = {}
    expected_days = set(range(START // 86400, END // 86400))
    strike_data = json.loads(args.strikes.read_text())
    expected_coverage: dict[str, dict[int, int]] = {}
    for asset in ASSETS:
        counts: dict[int, int] = defaultdict(int)
        for market in strike_data.get(f"KX{asset}15M") or []:
            if market.get("result") not in ("yes", "no"):
                continue
            open_ts = int(
                dt.datetime.fromisoformat(
                    market["open_time"].replace("Z", "+00:00")
                ).timestamp()
            )
            if START <= open_ts < END:
                counts[open_ts // 86400] += 1
        expected_coverage[asset] = dict(counts)
    for asset in assets:
        path = args.root / f"candles_{asset}.json"
        if not path.exists():
            continue
        available.append(asset)
        asset_jobs, asset_coverage = jobs_for(asset, args.root)
        jobs.extend(asset_jobs)
        coverage[asset] = asset_coverage
    if not jobs:
        raise SystemExit("no eligible 0.9-cent joins")
    print(f"assets={available} jobs={len(jobs)}", flush=True)

    rows, stat = collect(Http(), jobs, args.workers)
    by_day: dict[int, float] = defaultdict(float)
    by_day_stress: dict[int, float] = defaultdict(float)
    by_asset: dict[str, float] = defaultdict(float)
    by_asset_stress: dict[str, float] = defaultdict(float)
    gross_positive_by_asset: dict[str, float] = defaultdict(float)
    for row in rows:
        pnl = float(row["filled"]) * (PRICE - float(row["hit"]))
        stressed = pnl - float(row["filled"]) * STRESS
        by_day[int(row["day"])] += pnl
        by_day_stress[int(row["day"])] += stressed
        by_asset[row["asset"]] += pnl
        by_asset_stress[row["asset"]] += stressed
        gross_positive_by_asset[row["asset"]] += max(0.0, pnl)

    all_days = sorted(expected_days)
    for day in all_days:
        by_day.setdefault(day, 0.0)
        by_day_stress.setdefault(day, 0.0)
    half = len(all_days) // 2
    halves = [
        float(np.mean([by_day_stress[d] for d in all_days[:half]])),
        float(np.mean([by_day_stress[d] for d in all_days[half:]])),
    ]
    ci = day_bootstrap(rows, all_days, STRESS)
    filled = sum(float(r["filled"]) for r in rows)
    adverse_windows = {r["ticker"] for r in rows if r["filled"] > 0 and r["hit"] > 0}
    adverse_days = {int(r["day"]) for r in rows if r["filled"] > 0 and r["hit"] > 0}
    gross = sum(gross_positive_by_asset.values())
    concentration = (
        max(gross_positive_by_asset.values()) / gross if gross else None
    )
    complete = (
        set(available) == set(ASSETS)
        and all(
            coverage.get(asset, {}).get(day)
            == expected_coverage.get(asset, {}).get(day)
            for asset in ASSETS
            for day in expected_days
        )
    )
    passes = bool(
        complete
        and stat["capped"] == 0
        and len(rows) >= 1000
        and len(adverse_days) >= 10
        and all(x > 0 for x in halves)
        and ci[0] > 0
        and sum(v > 0 for v in by_asset_stress.values()) >= 6
        and concentration is not None
        and concentration <= 0.40
    )
    report = {
        "preregistration": "PREREG_subcent_sweep_oos_20261006.md",
        "assets_requested": list(assets),
        "assets_available": available,
        "complete_population": complete,
        "market_coverage_by_asset_day": coverage,
        "expected_market_coverage_by_asset_day": expected_coverage,
        "utc_days": len(expected_days),
        "joined_markets": len(rows),
        "capped_histories": stat["capped"],
        "credited_filled_contracts": filled,
        "mean_credited_fills_per_join": filled / len(rows),
        "adverse_windows": len(adverse_windows),
        "adverse_days": len(adverse_days),
        "pnl_per_day": sum(by_day.values()) / len(expected_days),
        "pnl_per_day_after_0_1c_stress": sum(by_day_stress.values()) / len(expected_days),
        "day_cluster_ci_after_0_1c_stress": ci,
        "chronological_halves_after_0_1c_stress": halves,
        "largest_losing_day": min(by_day_stress.items(), key=lambda kv: kv[1]),
        "per_asset_pnl": dict(sorted(by_asset.items())),
        "per_asset_pnl_after_0_1c_stress": dict(sorted(by_asset_stress.items())),
        "largest_asset_share_gross_positive_pnl": concentration,
        "passes_oos_promotion_gate": passes,
        "order_stats": stat,
        "rows": rows,
    }
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True))
    printable = dict(report)
    printable.pop("rows")
    printable["order_stats"] = {
        k: v for k, v in stat.items() if not k.startswith("conc_")
    }
    print(json.dumps(printable, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
