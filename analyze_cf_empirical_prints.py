#!/usr/bin/env python3
"""Score preregistered walk-forward empirical probabilities at real prints.

Implements Amendment 3 of ``PREREG_cf_exact_endgame_20261006.md``. Historical
prints remain an optimistic execution upper bound.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

import analyze_cf_rolling_endgame as base


ARMS = ("C1", "C2", "C3")


def row_key(row: Dict[str, Any]) -> tuple:
    slope_sign = 1 if row["trend_shift"] > 0 else (-1 if row["trend_shift"] < 0 else 0)
    return (
        row["asset"],
        base.time_bin(row["seconds_left"]),
        base.distance_bin(row["distance"]),
        slope_sign,
    )


def fixed_rows_for_market(market: base.Market) -> list[Dict[str, Any]]:
    rows = []
    for seconds_left in base.DECISION_SECONDS:
        index = market.at_or_before(market.close_ms - seconds_left * 1000)
        if index is None:
            continue
        probs = market.probabilities(index, seconds_left)
        if probs is None:
            continue
        rows.append(
            {
                "asset": market.asset,
                "seconds_left": seconds_left,
                "distance": (probs["value"] - market.target) / probs["sd"],
                "trend_shift": probs["trend_shift"],
                "label": market.label,
            }
        )
    return rows


def print_candidates(
    market: base.Market,
    counts: Dict[tuple, list[int]],
) -> list[Dict[str, Any]]:
    candidates = []
    probability_cache: Dict[tuple[int, int], Optional[Dict[str, float]]] = {}
    for raw in market.prints:
        print_us, yes_cents, count, taker_side = raw
        print_ms = int(print_us // 1000)
        if not (market.close_ms - 60_000 <= print_ms < market.close_ms):
            continue
        source_index = market.at_or_before(print_ms - 1_000)
        if source_index is None:
            continue
        source_ms = int(market.times[source_index])
        seconds_left = max(1, int(math.ceil((market.close_ms - source_ms) / 1_000)))
        if seconds_left > 60:
            continue
        cache_key = (source_index, seconds_left)
        if cache_key not in probability_cache:
            probability_cache[cache_key] = market.probabilities(source_index, seconds_left)
        probs = probability_cache[cache_key]
        if probs is None:
            continue
        state = {
            "asset": market.asset,
            "seconds_left": seconds_left,
            "distance": (probs["value"] - market.target) / probs["sd"],
            "trend_shift": probs["trend_shift"],
        }
        losses, wins = counts[row_key(state)]
        n = losses + wins
        if n < 30:
            continue
        p_c1_yes = (wins + 1) / (n + 2)
        p_c2_yes = (100 * probs["B2"] + n * p_c1_yes) / (100 + n)
        side = str(taker_side).lower()
        if side == "yes":
            entry = float(yes_cents) / 100
            payout = float(market.label)
            side_probs = {"B2": probs["B2"], "C1": p_c1_yes, "C2": p_c2_yes}
        elif side == "no":
            entry = 1 - float(yes_cents) / 100
            payout = float(1 - market.label)
            side_probs = {
                "B2": 1 - probs["B2"],
                "C1": 1 - p_c1_yes,
                "C2": 1 - p_c2_yes,
            }
        else:
            continue
        fee = base.taker_fee(entry)
        candidates.append(
            {
                "asset": market.asset,
                "ticker": market.ticker,
                "close_ms": market.close_ms,
                "side": side,
                "seconds_left": seconds_left,
                "source_to_print_ms": print_ms - source_ms,
                "entry": entry,
                "fee": fee,
                "label": market.label,
                "payout": payout,
                "print_count": float(count),
                "train_markets": n,
                "p_B2": side_probs["B2"],
                "p_C1": side_probs["C1"],
                "p_C2": side_probs["C2"],
            }
        )
    return candidates


def score(markets: Sequence[base.Market]) -> tuple[list[Dict[str, Any]], Dict[str, Any]]:
    counts: Dict[tuple, list[int]] = defaultdict(lambda: [0, 0])
    trades = []
    eligible_candidates = 0
    markets_with_candidates = 0
    for market in markets:
        candidates = print_candidates(market, counts)
        eligible_candidates += len(candidates)
        markets_with_candidates += bool(candidates)
        used = set()
        for candidate in candidates:
            arm_probability = {
                "C1": candidate["p_C1"],
                "C2": candidate["p_C2"],
                "C3": min(candidate["p_B2"], candidate["p_C1"]),
            }
            for arm, probability in arm_probability.items():
                for threshold in base.THRESHOLDS:
                    key = (arm, threshold)
                    if key in used:
                        continue
                    if arm == "C3":
                        qualifies = (
                            candidate["p_B2"] - candidate["entry"] - candidate["fee"] > threshold
                            and candidate["p_C1"] - candidate["entry"] - candidate["fee"] > threshold
                        )
                    else:
                        qualifies = probability - candidate["entry"] - candidate["fee"] > threshold
                    if not qualifies:
                        continue
                    used.add(key)
                    trades.append(
                        {
                            **candidate,
                            "arm": arm,
                            "threshold": threshold,
                            "model_probability": probability,
                            "edge": probability - candidate["entry"] - candidate["fee"],
                            "pnl": candidate["payout"] - candidate["entry"] - candidate["fee"],
                        }
                    )

        # Settlement is one observation. Update only after every decision in
        # the market has been scored, and once per cell.
        pending = {row_key(row): row["label"] for row in fixed_rows_for_market(market)}
        for key, label in pending.items():
            counts[key][label] += 1

    diagnostics = {
        "eligible_print_candidates": eligible_candidates,
        "markets_with_eligible_candidates": markets_with_candidates,
        "trained_cells": len(counts),
    }
    return trades, diagnostics


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--assets", nargs="+", default=base.ASSETS)
    p.add_argument("--history-dir", type=Path, default=Path("data/cf_exact/history"))
    p.add_argument("--tape-dir", type=Path, default=Path("../kalshi-scalp/data/rpl_tapes"))
    p.add_argument("--metadata-dir", type=Path, default=Path("../kalshi-scalp/data"))
    p.add_argument("--markets-per-asset", type=int, default=0)
    p.add_argument("--iterations", type=int, default=10_000)
    p.add_argument("--output", type=Path, default=Path("data/cf_exact/report_empirical_prints.json"))
    p.add_argument("--trades-output", type=Path)
    args = p.parse_args()
    args.assets = [a.upper() for a in args.assets]

    markets = base.load_markets(args)
    trades, diagnostics = score(markets)
    report = {
        "preregistration": "PREREG_cf_exact_endgame_20261006.md Amendment 3",
        "markets": len(markets),
        "close_windows": len({m.close_ms for m in markets}),
        **diagnostics,
        "trades": len(trades),
        "summary": base.summarize_trades(trades, args.iterations, arms=ARMS),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True))
    if args.trades_output:
        args.trades_output.parent.mkdir(parents=True, exist_ok=True)
        with args.trades_output.open("w") as handle:
            for trade in trades:
                handle.write(json.dumps(trade, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
