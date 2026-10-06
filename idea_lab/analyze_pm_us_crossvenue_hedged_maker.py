"""Prospective screen for a maker fill hedged on the exact other venue."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from idea_lab.analyze_pm_us_exact_pair_live import (
        _best_ask,
        _best_bid,
        kalshi_fee,
        load,
        pm_fee,
    )
except ModuleNotFoundError:
    from analyze_pm_us_exact_pair_live import (
        _best_ask,
        _best_bid,
        kalshi_fee,
        load,
        pm_fee,
    )


CUTOFF_TS = 1_791_302_289.195273
MIN_MARGIN = 0.02
MAX_PM_AGE_S = 0.010
MAX_GAP_S = 1.5


def kalshi_tick(price: float) -> float:
    return 0.001 if price < 0.10 or price > 0.90 else 0.01


def improve(bid: float, ask: float, tick: float) -> float | None:
    quote = bid + tick if ask - bid > tick + 1e-9 else bid
    return quote if quote < ask - 1e-9 else None


def opportunities(row: dict) -> list[dict]:
    if row.get("err") or row.get("pm_err"):
        return []
    if row.get("pm_state") != "MARKET_STATE_OPEN":
        return []
    if abs(float(row.get("pm_age", 99.0))) > MAX_PM_AGE_S:
        return []
    pb = _best_bid(row.get("pm_bids") or [])
    pa = _best_ask(row.get("pm_asks") or [])
    ky = _best_bid(row.get("k_yes_bids") or [])
    kn = _best_bid(row.get("k_no_bids") or [])
    if not all((pb, pa, ky, kn)):
        return []
    pbp, pbs = pb
    pap, pas = pa
    kyb, kys = ky
    knb, kns = kn
    kya, kna = 1.0 - knb, 1.0 - kyb
    pm_no_bid, pm_no_ask = 1.0 - pap, 1.0 - pbp

    raw = []
    pm_yes_quote = improve(pbp, pap, 0.01)
    if pm_yes_quote is not None:
        raw.append(
            ("make_pm_yes_hedge_k_no", pm_yes_quote, kna, kalshi_fee(kna), kys)
        )
    pm_no_quote = improve(pm_no_bid, pm_no_ask, 0.01)
    if pm_no_quote is not None:
        raw.append(
            ("make_pm_no_hedge_k_yes", pm_no_quote, kya, kalshi_fee(kya), kns)
        )
    k_yes_quote = improve(kyb, kya, kalshi_tick(kyb))
    if k_yes_quote is not None:
        raw.append(
            ("make_k_yes_hedge_pm_no", k_yes_quote, pm_no_ask, pm_fee(pm_no_ask), pbs)
        )
    k_no_quote = improve(knb, kna, kalshi_tick(knb))
    if k_no_quote is not None:
        raw.append(
            ("make_k_no_hedge_pm_yes", k_no_quote, pap, pm_fee(pap), pas)
        )

    return [
        {
            "direction": direction,
            "maker_price": maker,
            "hedge_price": hedge,
            "hedge_fee": hedge_fee,
            "hedge_size": hedge_size,
            "margin": 1.0 - maker - hedge - hedge_fee,
        }
        for direction, maker, hedge, hedge_fee, hedge_size in raw
        if hedge_size >= 1.0
    ]


def score(rows: list[dict]) -> dict:
    eligible = []
    for row in rows:
        found = opportunities(row)
        if found:
            eligible.append(
                {
                    "ts": float(row["ts"]),
                    "slug": str(row["slug"]),
                    "ticker": row.get("ticker"),
                    "opportunities": found,
                }
            )
    prior = {}
    seen = set()
    signals = []
    for index, row in enumerate(eligible):
        by_direction = {item["direction"]: item for item in row["opportunities"]}
        for direction, item in by_direction.items():
            key = (row["slug"], direction)
            previous = prior.get(key)
            prior[key] = (row["ts"], item["margin"])
            if row["slug"] in seen or item["margin"] < MIN_MARGIN or previous is None:
                continue
            if previous[1] < MIN_MARGIN or row["ts"] - previous[0] > MAX_GAP_S:
                continue
            next_margin = None
            for later in eligible[index + 1 :]:
                if later["slug"] != row["slug"] or later["ts"] - row["ts"] > MAX_GAP_S:
                    break
                match = {
                    candidate["direction"]: candidate
                    for candidate in later["opportunities"]
                }.get(direction)
                if match is not None:
                    next_margin = match["margin"]
                    break
            signals.append(
                {
                    **item,
                    "slug": row["slug"],
                    "ticker": row["ticker"],
                    "ts": row["ts"],
                    "margin_c": item["margin"] * 100.0,
                    "previous_margin_c": previous[1] * 100.0,
                    "next_sample_margin_c": (
                        None if next_margin is None else next_margin * 100.0
                    ),
                    "next_sample_survives": (
                        next_margin is not None and next_margin >= 0.0
                    ),
                }
            )
            seen.add(row["slug"])
            break

    margins = [item["margin_c"] for item in signals]
    split = len(margins) // 2
    halves = [
        sum(margins[:split]) / split if split else None,
        (
            sum(margins[split:]) / len(margins[split:])
            if margins[split:]
            else None
        ),
    ]
    next_known = [item for item in signals if item["next_sample_margin_c"] is not None]
    survival = (
        sum(item["next_sample_survives"] for item in next_known) / len(next_known)
        if next_known
        else None
    )
    report = {
        "preregistration": "PREREG_pm_us_crossvenue_hedged_maker_20261006.md",
        "cutoff_ts": CUTOFF_TS,
        "raw_rows": len(rows),
        "fresh_paired_rows": len(eligible),
        "persistent_candidate_windows": len(signals),
        "mean_margin_c": sum(margins) / len(margins) if margins else None,
        "halves_c": halves,
        "next_sample_known": len(next_known),
        "next_sample_survival": survival,
        "minimum_hedge_size": (
            min(item["hedge_size"] for item in signals) if signals else None
        ),
        "signals": signals,
    }
    report["passes_short_screen"] = bool(
        len(signals) >= 5
        and all(value is not None and value > 0 for value in halves)
        and survival is not None
        and survival >= 0.8
        and report["minimum_hedge_size"] is not None
        and report["minimum_hedge_size"] >= 1.0
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("../polymarket-us-mm/data/xvenue_btc15_20261006.jsonl"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("idea_lab/pm_us_crossvenue_hedged_maker_report.json"),
    )
    parser.add_argument("--cutoff", type=float, default=CUTOFF_TS)
    args = parser.parse_args()
    report = score(load(args.input, args.cutoff))
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
