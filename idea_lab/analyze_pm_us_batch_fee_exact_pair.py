"""Screen the exact PM-US/Kalshi pair using conservative batch-rounded fees."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

try:
    from idea_lab.analyze_pm_us_exact_pair_live import _best_ask, _best_bid, load
except ModuleNotFoundError:
    from analyze_pm_us_exact_pair_live import _best_ask, _best_bid, load

CUTOFF_TS = 1_791_302_814.603325
MIN_MARGIN = 0.01
MAX_PM_AGE_S = 0.010
MAX_GAP_S = 1.5


def batch_fee(coeff: float, price: float, quantity: int) -> float:
    return math.ceil(coeff * quantity * price * (1 - price) * 100 - 1e-9) / 100


def opportunities(row: dict) -> list[dict]:
    if row.get("err") or row.get("pm_err"):
        return []
    if row.get("pm_state") != "MARKET_STATE_OPEN":
        return []
    if abs(float(row.get("pm_age", 99))) > MAX_PM_AGE_S:
        return []
    pa = _best_ask(row.get("pm_asks") or [])
    pb = _best_bid(row.get("pm_bids") or [])
    ky = _best_bid(row.get("k_yes_bids") or [])
    kn = _best_bid(row.get("k_no_bids") or [])
    if not all((pa, pb, ky, kn)):
        return []
    pma, pma_size = pa
    pmb, pmb_size = pb
    kyb, kyb_size = ky
    knb, knb_size = kn
    raw = [
        ("pm_yes_kalshi_no", pma, 1 - kyb, pma_size, kyb_size),
        ("kalshi_yes_pm_no", 1 - knb, 1 - pmb, knb_size, pmb_size),
    ]
    found = []
    for direction, pm_entry, k_entry, pm_size, k_size in raw:
        quantity = min(100, int(math.floor(min(pm_size, k_size))))
        if quantity < 10:
            continue
        pm_total_fee = batch_fee(0.0695, pm_entry, quantity)
        k_total_fee = batch_fee(0.07, k_entry, quantity)
        margin = (
            1.0 - pm_entry - k_entry
            - (pm_total_fee + k_total_fee) / quantity
        )
        found.append({
            "direction": direction,
            "pm_entry": pm_entry,
            "kalshi_entry": k_entry,
            "quantity": quantity,
            "pm_total_fee": pm_total_fee,
            "kalshi_total_fee": k_total_fee,
            "margin": margin,
        })
    return found


def score(rows: list[dict]) -> dict:
    eligible = []
    for row in rows:
        found = opportunities(row)
        if found:
            eligible.append({
                "ts": float(row["ts"]), "slug": str(row["slug"]),
                "ticker": row.get("ticker"), "opportunities": found,
            })
    prior = {}
    seen = set()
    signals = []
    for index, row in enumerate(eligible):
        for item in row["opportunities"]:
            key = (row["slug"], item["direction"])
            previous = prior.get(key)
            prior[key] = (row["ts"], item["margin"])
            if row["slug"] in seen or item["margin"] < MIN_MARGIN or previous is None:
                continue
            if previous[1] < MIN_MARGIN or row["ts"] - previous[0] > MAX_GAP_S:
                continue
            next_margin = None
            for later in eligible[index + 1:]:
                if later["slug"] != row["slug"] or later["ts"] - row["ts"] > MAX_GAP_S:
                    break
                match = next((x for x in later["opportunities"]
                              if x["direction"] == item["direction"]), None)
                if match:
                    next_margin = match["margin"]
                    break
            signals.append({
                **item, "slug": row["slug"], "ticker": row["ticker"],
                "ts": row["ts"], "margin_c": item["margin"] * 100,
                "previous_margin_c": previous[1] * 100,
                "stressed_margin_c": (item["margin"] - 0.02) * 100,
                "next_sample_margin_c": (
                    None if next_margin is None else next_margin * 100
                ),
                "next_sample_survives": next_margin is not None and next_margin >= 0,
            })
            seen.add(row["slug"])
            break
    values = [x["stressed_margin_c"] for x in signals]
    split = len(values) // 2
    known = [x for x in signals if x["next_sample_margin_c"] is not None]
    survival = (
        sum(x["next_sample_survives"] for x in known) / len(known)
        if known else None
    )
    report = {
        "preregistration": "PREREG_pm_us_batch_fee_exact_pair_20261006.md",
        "cutoff_ts": CUTOFF_TS,
        "raw_rows": len(rows),
        "fresh_depth_rows": len(eligible),
        "persistent_candidate_windows": len(signals),
        "mean_margin_c": (
            sum(x["margin_c"] for x in signals) / len(signals) if signals else None
        ),
        "mean_stressed_margin_c": (
            sum(values) / len(values) if values else None
        ),
        "stressed_halves_c": [
            sum(values[:split]) / split if split else None,
            sum(values[split:]) / len(values[split:]) if values[split:] else None,
        ],
        "next_sample_survival": survival,
        "minimum_quantity": min((x["quantity"] for x in signals), default=None),
        "signals": signals,
    }
    report["passes_short_screen"] = bool(
        len(signals) >= 5
        and all(x is not None and x > 0 for x in report["stressed_halves_c"])
        and survival is not None and survival >= 0.8
        and report["minimum_quantity"] is not None
        and report["minimum_quantity"] >= 10
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input", type=Path,
        default=Path("../polymarket-us-mm/data/xvenue_btc15_20261006.jsonl"),
    )
    parser.add_argument(
        "--output", type=Path,
        default=Path("idea_lab/pm_us_batch_fee_exact_pair_report.json"),
    )
    parser.add_argument("--cutoff", type=float, default=CUTOFF_TS)
    args = parser.parse_args()
    report = score(load(args.input, args.cutoff))
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

