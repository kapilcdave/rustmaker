"""Score the frozen Polymarket US x Kalshi exact BTC-15m pair.

The collector lives in ../polymarket-us-mm/xvenue_btc15.py and has no order
path. This scorer intentionally works from executable top levels, exact
one-contract fee rounding, strict cross-venue freshness, and one signal per
window.
"""
from __future__ import annotations

import argparse
import json
import math
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path


CUTOFF_TS = 1_791_302_112.315213
MIN_RAW_NET = 0.03
MAX_PM_AGE_S = 0.010
MAX_CONSECUTIVE_GAP_S = 1.5


def kalshi_fee(price: float) -> float:
    return math.ceil(0.07 * price * (1.0 - price) * 100.0 - 1e-9) / 100.0


def pm_fee(price: float) -> float:
    p = Decimal(str(price))
    raw = Decimal("0.0695") * p * (Decimal(1) - p)
    return float(raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))


def _best_bid(levels: list[list[float]]) -> tuple[float, float] | None:
    valid = [(float(price), float(size)) for price, size in levels if float(size) >= 1.0]
    return max(valid) if valid else None


def _best_ask(levels: list[list[float]]) -> tuple[float, float] | None:
    valid = [(float(price), float(size)) for price, size in levels if float(size) >= 1.0]
    return min(valid) if valid else None


def candidates(row: dict) -> list[dict]:
    if row.get("err") or row.get("pm_err"):
        return []
    if row.get("pm_state") != "MARKET_STATE_OPEN":
        return []
    if abs(float(row.get("pm_age", 99.0))) > MAX_PM_AGE_S:
        return []
    pm_yes_ask = _best_ask(row.get("pm_asks") or [])
    pm_yes_bid = _best_bid(row.get("pm_bids") or [])
    k_yes_bid = _best_bid(row.get("k_yes_bids") or [])
    k_no_bid = _best_bid(row.get("k_no_bids") or [])
    if not all((pm_yes_ask, pm_yes_bid, k_yes_bid, k_no_bid)):
        return []

    pma, pma_size = pm_yes_ask
    pmb, pmb_size = pm_yes_bid
    kyb, kyb_size = k_yes_bid
    knb, knb_size = k_no_bid
    rows = [
        {
            "direction": "pm_yes_kalshi_no",
            "pm_entry": pma,
            "kalshi_entry": 1.0 - kyb,
            "displayed_size": min(pma_size, kyb_size),
        },
        {
            "direction": "kalshi_yes_pm_no",
            "pm_entry": 1.0 - pmb,
            "kalshi_entry": 1.0 - knb,
            "displayed_size": min(pmb_size, knb_size),
        },
    ]
    for item in rows:
        item["pm_fee"] = pm_fee(item["pm_entry"])
        item["kalshi_fee"] = kalshi_fee(item["kalshi_entry"])
        item["cost"] = (
            item["pm_entry"]
            + item["kalshi_entry"]
            + item["pm_fee"]
            + item["kalshi_fee"]
        )
        item["net"] = 1.0 - item["cost"]
    return rows


def load(path: Path, cutoff: float) -> list[dict]:
    found = []
    if not path.exists():
        return found
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if float(row.get("ts", 0.0)) >= cutoff:
            found.append(row)
    return found


def score(rows: list[dict]) -> dict:
    eligible = []
    for row in rows:
        cs = candidates(row)
        if not cs:
            continue
        eligible.append(
            {
                "ts": float(row["ts"]),
                "slug": row.get("slug"),
                "ticker": row.get("ticker"),
                "pm_age_ms": float(row["pm_age"]) * 1000.0,
                "candidates": cs,
            }
        )

    signals = []
    seen_windows: set[str] = set()
    prior: dict[tuple[str, str], dict] = {}
    for index, row in enumerate(eligible):
        slug = str(row["slug"])
        by_direction = {item["direction"]: item for item in row["candidates"]}
        for direction, item in by_direction.items():
            key = (slug, direction)
            previous = prior.get(key)
            prior[key] = {"ts": row["ts"], "net": item["net"]}
            if slug in seen_windows or item["net"] < MIN_RAW_NET or previous is None:
                continue
            if previous["net"] < MIN_RAW_NET:
                continue
            if row["ts"] - previous["ts"] > MAX_CONSECUTIVE_GAP_S:
                continue

            next_net = None
            for later in eligible[index + 1 :]:
                if later["slug"] != slug:
                    break
                if later["ts"] - row["ts"] > MAX_CONSECUTIVE_GAP_S:
                    break
                later_items = {
                    candidate["direction"]: candidate
                    for candidate in later["candidates"]
                }
                if direction in later_items:
                    next_net = float(later_items[direction]["net"])
                    break
            signal = {
                **item,
                "slug": slug,
                "ticker": row["ticker"],
                "ts": row["ts"],
                "pm_age_ms": row["pm_age_ms"],
                "previous_net_c": previous["net"] * 100.0,
                "net_c": item["net"] * 100.0,
                "stressed_net_c": (item["net"] - 0.02) * 100.0,
                "next_sample_net_c": None if next_net is None else next_net * 100.0,
                "next_sample_survives": next_net is not None and next_net >= 0.0,
            }
            signals.append(signal)
            seen_windows.add(slug)
            break

    stressed = [row["stressed_net_c"] for row in signals]
    split = len(stressed) // 2
    halves = [
        (sum(stressed[:split]) / split) if split else None,
        (
            sum(stressed[split:]) / len(stressed[split:])
            if stressed[split:]
            else None
        ),
    ]
    next_known = [row for row in signals if row["next_sample_net_c"] is not None]
    survival = (
        sum(row["next_sample_survives"] for row in next_known) / len(next_known)
        if next_known
        else None
    )
    report = {
        "preregistration": "PREREG_pm_us_exact_pair_live_20261006.md",
        "cutoff_ts": CUTOFF_TS,
        "raw_rows": len(rows),
        "fresh_paired_rows": len(eligible),
        "persistent_candidate_windows": len(signals),
        "mean_raw_net_c": (
            sum(row["net_c"] for row in signals) / len(signals) if signals else None
        ),
        "mean_stressed_net_c": (
            sum(stressed) / len(stressed) if stressed else None
        ),
        "stressed_halves_c": halves,
        "next_sample_known": len(next_known),
        "next_sample_survival": survival,
        "minimum_displayed_size": (
            min(row["displayed_size"] for row in signals) if signals else None
        ),
        "signals": signals,
    }
    report["passes_short_screen"] = bool(
        len(signals) >= 5
        and all(value is not None and value > 0 for value in halves)
        and survival is not None
        and survival >= 0.80
        and report["minimum_displayed_size"] is not None
        and report["minimum_displayed_size"] >= 1.0
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
        default=Path("idea_lab/pm_us_exact_pair_live_report.json"),
    )
    parser.add_argument("--cutoff", type=float, default=CUTOFF_TS)
    args = parser.parse_args()
    report = score(load(args.input, args.cutoff))
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
