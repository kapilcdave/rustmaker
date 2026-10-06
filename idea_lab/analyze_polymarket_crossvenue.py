"""Score prospective paired Kalshi/Polymarket BTC 15m direct books."""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np
import requests

from common import fee

CUTOFF_NS = 1_791_298_597_036_698_000
PATH = Path("idea_lab/polymarket_crossvenue_live_20261006.jsonl.gz")
OUTPUT = Path("idea_lab/polymarket_crossvenue_live_report.json")


def rows() -> list[dict]:
    found = []
    if not PATH.exists():
        return found
    with gzip.open(PATH, "rt", encoding="utf-8") as handle:
        while True:
            try:
                line = handle.readline()
            except EOFError:
                break
            if not line:
                break
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if int(row.get("wall_ns", 0)) >= CUTOFF_NS:
                found.append(row)
    return found


def kalshi_outcome(ticker: str) -> str | None:
    response = requests.get(
        f"https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}",
        timeout=10,
    )
    if not response.ok:
        return None
    return (response.json().get("market") or {}).get("result")


def poly_outcome(slug: str) -> str | None:
    response = requests.get(
        "https://gamma-api.polymarket.com/events",
        params={"slug": slug},
        timeout=10,
    )
    if not response.ok or not response.json():
        return None
    market = (response.json()[0].get("markets") or [{}])[0]
    try:
        prices = [float(value) for value in json.loads(market["outcomePrices"])]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not market.get("closed"):
        return None
    return "up" if prices[0] > prices[1] else "down"


def main() -> None:
    snapshots = rows()
    signals = []
    seen = set()
    complete = 0
    best_margin = None
    for row in snapshots:
        market = row["kalshi_market"]
        ticker = market["ticker"]
        responses = row["responses"]
        values = [responses[name].get("data") for name in ("kalshi", "poly_up", "poly_down")]
        if any(value is None for value in values):
            continue
        starts = [int(responses[name]["request_start_ns"]) for name in responses]
        finishes = [int(responses[name]["request_finish_ns"]) for name in responses]
        span_ms = (max(finishes) - min(starts)) / 1_000_000
        if span_ms > 1000:
            continue
        kalshi, poly_up, poly_down = values
        yes_bid = kalshi.get("yes_bid")
        no_bid = kalshi.get("no_bid")
        if yes_bid is None or no_bid is None:
            continue
        yes_ask = 1.0 - float(no_bid)
        no_ask = 1.0 - float(yes_bid)
        if poly_up.get("best_ask") is None or poly_down.get("best_ask") is None:
            continue
        complete += 1
        candidates = [
            {
                "pair": "kalshi_yes_poly_down",
                "kalshi_side": "yes",
                "poly_side": "down",
                "kalshi_entry": yes_ask,
                "poly_entry": float(poly_down["best_ask"]),
                "displayed_size": min(
                    float(kalshi.get("no_bid_size") or 0),
                    float(poly_down.get("best_ask_size") or 0),
                ),
            },
            {
                "pair": "kalshi_no_poly_up",
                "kalshi_side": "no",
                "poly_side": "up",
                "kalshi_entry": no_ask,
                "poly_entry": float(poly_up["best_ask"]),
                "displayed_size": min(
                    float(kalshi.get("yes_bid_size") or 0),
                    float(poly_up.get("best_ask_size") or 0),
                ),
            },
        ]
        for candidate in candidates:
            candidate["cost_after_fee_allowance"] = (
                candidate["kalshi_entry"]
                + fee(candidate["kalshi_entry"])
                + candidate["poly_entry"]
                + 0.01
            )
            candidate["margin"] = 1.0 - candidate["cost_after_fee_allowance"]
            best_margin = (
                candidate["margin"]
                if best_margin is None
                else max(best_margin, candidate["margin"])
            )
        candidate = min(candidates, key=lambda item: item["cost_after_fee_allowance"])
        if ticker in seen:
            continue
        if (
            candidate["cost_after_fee_allowance"] <= 0.97
            and candidate["displayed_size"] >= 5
        ):
            seen.add(ticker)
            signal = {
                **candidate,
                "ticker": ticker,
                "open_ts": int(market["open_ts"]),
                "close_ts": int(market["close_ts"]),
                "slug": row["polymarket"]["slug"],
                "wall_ns": int(row["wall_ns"]),
                "request_span_ms": span_ms,
            }
            signal["kalshi_result"] = kalshi_outcome(ticker)
            signal["poly_result"] = poly_outcome(signal["slug"])
            if signal["kalshi_result"] and signal["poly_result"]:
                signal["outcomes_match"] = (
                    ("yes" if signal["poly_result"] == "up" else "no")
                    == signal["kalshi_result"]
                )
                signal["realized_payout"] = (
                    (1.0 if signal["kalshi_result"] == signal["kalshi_side"] else 0.0)
                    + (1.0 if signal["poly_result"] == signal["poly_side"] else 0.0)
                )
                signal["pnl"] = (
                    signal["realized_payout"]
                    - signal["cost_after_fee_allowance"]
                )
            else:
                signal["outcomes_match"] = None
                signal["realized_payout"] = None
                signal["pnl"] = None
            signals.append(signal)
    settled = [row for row in signals if row["pnl"] is not None]
    pnls = [float(row["pnl"]) for row in settled]
    split = len(pnls) // 2
    report = {
        "preregistration": "PREREG_polymarket_crossvenue_live_20261006.md",
        "cutoff_ns": CUTOFF_NS,
        "evidence": "prospective sequential direct-book paper",
        "rows": len(snapshots),
        "complete_under_1000ms": complete,
        "best_margin_after_fee_allowance_c": (
            None if best_margin is None else best_margin * 100
        ),
        "signals": len(signals),
        "settled": len(settled),
        "outcome_mismatches": sum(
            row["outcomes_match"] is False for row in settled
        ),
        "mean_c": float(np.mean(pnls) * 100) if pnls else None,
        "one_cent_per_leg_stressed_mean_c": (
            float(np.mean(pnls) * 100 - 2) if pnls else None
        ),
        "halves_c": [
            float(np.mean(pnls[:split]) * 100) if split else None,
            float(np.mean(pnls[split:]) * 100) if pnls[split:] else None,
        ],
        "signal_rows": signals,
    }
    report["passes_short_screen"] = bool(
        len(settled) >= 5
        and report["outcome_mismatches"] == 0
        and report["one_cent_per_leg_stressed_mean_c"] is not None
        and report["one_cent_per_leg_stressed_mean_c"] > 0
        and all(value is not None and value > 0 for value in report["halves_c"])
    )
    OUTPUT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
