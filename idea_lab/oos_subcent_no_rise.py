"""Fresh OOS score for PREREG_subcent_no_rise_oos_20261006.md."""
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

try:
    from idea_lab.oos_subcent_sweep import ASSETS, DC, END, PRICE, START, STRESS
except ModuleNotFoundError:  # direct `python idea_lab/oos_subcent_no_rise.py`
    from oos_subcent_sweep import ASSETS, DC, END, PRICE, START, STRESS


def cheap_quote(bar: dict) -> tuple[str, float] | None:
    bid, ask = bar.get("b"), bar.get("a")
    if bid is None or ask is None or ask <= bid:
        return None
    if 0.5 * (float(bid) + float(ask)) < 0.5:
        return "yes", float(ask)
    return "no", 1.0 - float(bid)


def jobs_for(asset: str, root: Path) -> tuple[list[dict], dict[int, int], int]:
    data = json.loads((root / f"candles_{asset}.json").read_text())
    jobs: list[dict] = []
    coverage: dict[int, int] = defaultdict(int)
    raw_09 = 0
    for market in data.get("markets") or []:
        open_ts = int(market["open_ts"])
        if not START <= open_ts < END or market.get("result") not in ("yes", "no"):
            continue
        coverage[open_ts // 86400] += 1
        bars = sorted(
            (b for b in market.get("bars") or [] if not b.get("post_close")),
            key=lambda b: b["ts"],
        )
        quotes = [cheap_quote(b) for b in bars]
        had_09 = False
        chosen = None
        for i, (bar, quote) in enumerate(zip(bars, quotes)):
            if quote is None:
                continue
            minute = (int(bar["ts"]) - open_ts) // 60
            side, current = quote
            if not 12 <= minute <= 14 or int(round(current * 1000.0)) != DC:
                continue
            had_09 = True
            if i < 2 or quotes[i - 1] is None or quotes[i - 2] is None:
                continue
            side1, ask1 = quotes[i - 1]
            side2, ask2 = quotes[i - 2]
            if side1 != side or side2 != side:
                continue
            if not ask2 + 1e-12 >= ask1 >= current - 1e-12:
                continue
            hit = market["result"] == side
            chosen = {
                "ticker": market["ticker"],
                "asset": asset,
                "day": open_ts // 86400,
                "open_ts": open_ts,
                "join": {DC: int(minute)},
                "side": {int(minute): side},
                "hit": {int(minute): float(hit)},
                "walk": {int(minute): None},
            }
            break
        raw_09 += int(had_09)
        if chosen:
            jobs.append(chosen)
    return jobs, dict(coverage), raw_09


def expected_coverage(strikes: Path) -> dict[str, dict[int, int]]:
    raw = json.loads(strikes.read_text())
    out: dict[str, dict[int, int]] = {}
    for asset in ASSETS:
        counts: dict[int, int] = defaultdict(int)
        for market in raw.get(f"KX{asset}15M") or []:
            if market.get("result") not in ("yes", "no"):
                continue
            open_ts = int(
                dt.datetime.fromisoformat(
                    market["open_time"].replace("Z", "+00:00")
                ).timestamp()
            )
            if START <= open_ts < END:
                counts[open_ts // 86400] += 1
        out[asset] = dict(counts)
    return out


def day_bootstrap(rows: list[dict], days: list[int], draws: int = 20_000) -> list[float]:
    by_day: dict[int, float] = defaultdict(float)
    for row in rows:
        by_day[int(row["day"])] += float(row["filled"]) * (
            PRICE - float(row["hit"]) - STRESS
        )
    values = np.asarray([by_day.get(day, 0.0) for day in days], dtype=float)
    rng = np.random.default_rng(20261006)
    sims = values[rng.integers(0, len(values), size=(draws, len(values)))].mean(axis=1)
    return [float(x) for x in np.quantile(sims, [0.025, 0.975])]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--root",
        type=Path,
        default=Path("/Users/kapil/proj/kalshi-scalp/data/oos_20261006"),
    )
    ap.add_argument(
        "--strikes",
        type=Path,
        default=Path("/Users/kapil/proj/kalshi-scalp/data/oos_20261006/strikes.json"),
    )
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument(
        "--output", type=Path, default=Path("idea_lab/oos_subcent_no_rise_report.json")
    )
    args = ap.parse_args()

    jobs: list[dict] = []
    available: list[str] = []
    coverage: dict[str, dict[int, int]] = {}
    raw_joins: dict[str, int] = {}
    for asset in ASSETS:
        if not (args.root / f"candles_{asset}.json").exists():
            continue
        asset_jobs, asset_coverage, raw = jobs_for(asset, args.root)
        available.append(asset)
        jobs.extend(asset_jobs)
        coverage[asset] = asset_coverage
        raw_joins[asset] = raw
    if not jobs:
        raise SystemExit("no eligible no-rise joins")

    rows, stat = collect(Http(), jobs, args.workers)
    days = list(range(START // 86400, END // 86400))
    expected = expected_coverage(args.strikes)
    complete = set(available) == set(ASSETS) and all(
        coverage.get(asset, {}).get(day) == expected.get(asset, {}).get(day)
        for asset in ASSETS
        for day in days
    )

    by_day: dict[int, float] = defaultdict(float)
    by_asset: dict[str, float] = defaultdict(float)
    by_asset_stress: dict[str, float] = defaultdict(float)
    gross_positive: dict[str, float] = defaultdict(float)
    for row in rows:
        pnl = float(row["filled"]) * (PRICE - float(row["hit"]))
        stressed = pnl - float(row["filled"]) * STRESS
        by_day[int(row["day"])] += stressed
        by_asset[row["asset"]] += pnl
        by_asset_stress[row["asset"]] += stressed
        gross_positive[row["asset"]] += max(0.0, pnl)
    half = len(days) // 2
    halves = [
        float(np.mean([by_day.get(d, 0.0) for d in days[:half]])),
        float(np.mean([by_day.get(d, 0.0) for d in days[half:]])),
    ]
    ci = day_bootstrap(rows, days)
    adverse_days = {int(r["day"]) for r in rows if r["filled"] > 0 and r["hit"] > 0}
    gross = sum(gross_positive.values())
    concentration = max(gross_positive.values()) / gross if gross else None
    passes = bool(
        complete
        and stat["capped"] == 0
        and len(rows) >= 500
        and len(adverse_days) >= 8
        and all(x > 0 for x in halves)
        and ci[0] > 0
        and sum(v > 0 for v in by_asset_stress.values()) >= 6
        and concentration is not None
        and concentration <= 0.40
    )
    filled = sum(float(r["filled"]) for r in rows)
    report = {
        "preregistration": "PREREG_subcent_no_rise_oos_20261006.md",
        "assets_available": available,
        "complete_population": complete,
        "market_coverage_by_asset_day": coverage,
        "expected_market_coverage_by_asset_day": expected,
        "raw_0_9c_markets_by_asset": raw_joins,
        "no_rise_joined_markets": len(rows),
        "no_rise_retention_of_raw_0_9c_markets": len(rows) / max(sum(raw_joins.values()), 1),
        "capped_histories": stat["capped"],
        "credited_filled_contracts": filled,
        "mean_credited_fills_per_join": filled / len(rows),
        "adverse_days": len(adverse_days),
        "pnl_per_day_after_0_1c_stress": sum(by_day.values()) / len(days),
        "day_cluster_ci_after_0_1c_stress": ci,
        "chronological_halves_after_0_1c_stress": halves,
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
        key: value for key, value in stat.items() if not key.startswith("conc_")
    }
    print(json.dumps(printable, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
