"""Prospective strict-through audit of a cross-venue dual-maker complete set."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from idea_lab.analyze_pm_us_crossvenue_hedged_maker import improve, kalshi_tick
    from idea_lab.analyze_pm_us_exact_pair_live import _best_ask, _best_bid, load
except ModuleNotFoundError:
    from analyze_pm_us_crossvenue_hedged_maker import improve, kalshi_tick
    from analyze_pm_us_exact_pair_live import _best_ask, _best_bid, load


CUTOFF_TS = 1_791_303_001.936524
MIN_MARGIN = 0.03
MAX_PM_AGE_S = 0.010
MAX_GAP_S = 1.5


def quotes(row: dict) -> list[dict]:
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

    p_yes_bid, _ = pb
    p_yes_ask, _ = pa
    k_yes_bid, _ = ky
    k_no_bid, _ = kn
    p_no_bid, p_no_ask = 1.0 - p_yes_ask, 1.0 - p_yes_bid
    k_yes_ask, k_no_ask = 1.0 - k_no_bid, 1.0 - k_yes_bid

    p_yes_quote = improve(p_yes_bid, p_yes_ask, 0.01)
    p_no_quote = improve(p_no_bid, p_no_ask, 0.01)
    k_yes_quote = improve(k_yes_bid, k_yes_ask, kalshi_tick(k_yes_bid))
    k_no_quote = improve(k_no_bid, k_no_ask, kalshi_tick(k_no_bid))
    raw = [
        ("pm_yes_k_no", p_yes_quote, k_no_quote),
        ("k_yes_pm_no", p_no_quote, k_yes_quote),
    ]
    out = []
    for direction, pm_price, k_price in raw:
        if pm_price is None or k_price is None:
            continue
        out.append(
            {
                "direction": direction,
                "pm_price": pm_price,
                "kalshi_price": k_price,
                "margin": 1.0 - pm_price - k_price,
            }
        )
    return out


def _asks(row: dict, direction: str) -> tuple[float | None, float | None]:
    pb = _best_bid(row.get("pm_bids") or [])
    pa = _best_ask(row.get("pm_asks") or [])
    ky = _best_bid(row.get("k_yes_bids") or [])
    kn = _best_bid(row.get("k_no_bids") or [])
    if not all((pb, pa, ky, kn)):
        return None, None
    p_yes_bid, _ = pb
    p_yes_ask, _ = pa
    k_yes_bid, _ = ky
    k_no_bid, _ = kn
    if direction == "pm_yes_k_no":
        return p_yes_ask, 1.0 - k_yes_bid
    return 1.0 - p_yes_bid, 1.0 - k_no_bid


def _fill_witnesses(
    rows: list[dict], start_index: int, slug: str, candidate: dict
) -> tuple[float | None, float | None]:
    pm_fill = None
    k_fill = None
    for row in rows[start_index + 1 :]:
        if str(row.get("slug")) != slug:
            break
        if row.get("err") or row.get("pm_err"):
            continue
        pm_ask, k_ask = _asks(row, candidate["direction"])
        ts = float(row.get("ts", 0.0))
        if (
            pm_fill is None
            and pm_ask is not None
            and pm_ask < candidate["pm_price"] - 1e-9
        ):
            pm_fill = ts
        if (
            k_fill is None
            and k_ask is not None
            and k_ask < candidate["kalshi_price"] - 1e-9
        ):
            k_fill = ts
        if pm_fill is not None and k_fill is not None:
            break
    return pm_fill, k_fill


def score(rows: list[dict]) -> dict:
    prior: dict[tuple[str, str], tuple[float, float]] = {}
    seen: set[str] = set()
    candidates = []
    for index, row in enumerate(rows):
        slug = str(row.get("slug"))
        for item in quotes(row):
            key = (slug, item["direction"])
            previous = prior.get(key)
            prior[key] = (float(row["ts"]), item["margin"])
            if slug in seen or item["margin"] < MIN_MARGIN or previous is None:
                continue
            if previous[1] < MIN_MARGIN:
                continue
            if float(row["ts"]) - previous[0] > MAX_GAP_S:
                continue
            pm_fill, k_fill = _fill_witnesses(rows, index, slug, item)
            paired = pm_fill is not None and k_fill is not None
            orphan = (pm_fill is None) != (k_fill is None)
            candidates.append(
                {
                    **item,
                    "slug": slug,
                    "ticker": row.get("ticker"),
                    "ts": float(row["ts"]),
                    "margin_c": item["margin"] * 100.0,
                    "previous_margin_c": previous[1] * 100.0,
                    "pm_strict_through_ts": pm_fill,
                    "kalshi_strict_through_ts": k_fill,
                    "paired_strict_through": paired,
                    "orphan": orphan,
                    "fill_gap_s": (
                        abs(pm_fill - k_fill) if paired else None
                    ),
                }
            )
            seen.add(slug)
            break

    paired_rows = [row for row in candidates if row["paired_strict_through"]]
    margins = [row["margin_c"] for row in paired_rows]
    split = len(margins) // 2
    halves = [
        sum(margins[:split]) / split if split else None,
        (
            sum(margins[split:]) / len(margins[split:])
            if margins[split:]
            else None
        ),
    ]
    candidate_count = len(candidates)
    paired_rate = len(paired_rows) / candidate_count if candidate_count else None
    orphan_rate = (
        sum(row["orphan"] for row in candidates) / candidate_count
        if candidate_count
        else None
    )
    gaps = [row["fill_gap_s"] for row in paired_rows]
    report = {
        "preregistration": "PREREG_pm_us_dual_maker_complete_set_20261006.md",
        "cutoff_ts": CUTOFF_TS,
        "raw_rows": len(rows),
        "candidate_windows": candidate_count,
        "paired_strict_through_windows": len(paired_rows),
        "orphan_windows": sum(row["orphan"] for row in candidates),
        "paired_fill_rate": paired_rate,
        "orphan_rate": orphan_rate,
        "mean_paired_margin_c": (
            sum(margins) / len(margins) if margins else None
        ),
        "paired_halves_c": halves,
        "max_fill_gap_s": max(gaps) if gaps else None,
        "candidates": candidates,
    }
    report["passes_short_screen"] = bool(
        len(paired_rows) >= 5
        and all(value is not None and value > 0 for value in halves)
        and paired_rate is not None
        and paired_rate >= 0.60
        and orphan_rate is not None
        and orphan_rate <= 0.25
        and report["max_fill_gap_s"] is not None
        and report["max_fill_gap_s"] <= 15.0
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
        default=Path("idea_lab/pm_us_dual_maker_complete_set_report.json"),
    )
    parser.add_argument("--cutoff", type=float, default=CUTOFF_TS)
    args = parser.parse_args()
    report = score(load(args.input, args.cutoff))
    report["cutoff_ts"] = args.cutoff
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
