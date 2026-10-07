#!/usr/bin/env python3
"""Score preregistered persistence vetoes on the exact rolling-average feed."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, Optional

import analyze_cf_rolling_endgame as base


ARMS = ("P1", "P2", "P3")


def side_probability(probability_yes: float, side: str) -> float:
    return probability_yes if side == "yes" else 1 - probability_yes


def score(markets: list[base.Market]) -> tuple[list[Dict[str, Any]], int]:
    trades = []
    candidate_count = 0
    for market in markets:
        used = set()
        cache: Dict[tuple[int, int], Optional[Dict[str, float]]] = {}
        for raw in market.prints:
            if len(used) == len(ARMS) * len(base.THRESHOLDS):
                break
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

            def probabilities(index: int) -> Optional[Dict[str, float]]:
                horizon = max(
                    1,
                    int(math.ceil((market.close_ms - int(market.times[index])) / 1_000)),
                )
                key = (index, horizon)
                if key not in cache:
                    cache[key] = market.probabilities(index, horizon)
                return cache[key]

            current = probabilities(source_index)
            if current is None:
                continue
            side = str(taker_side).lower()
            if side == "yes":
                entry = float(yes_cents) / 100
                payout = float(market.label)
            elif side == "no":
                entry = 1 - float(yes_cents) / 100
                payout = float(1 - market.label)
            else:
                continue
            fee = base.taker_fee(entry)

            prior = {}
            for lag in (2, 5):
                index = market.at_or_before(source_ms - lag * 1_000)
                prior[lag] = probabilities(index) if index is not None else None
            candidate_count += 1
            arm_pairs = {
                "P1": (current["B2"], prior[2]["B2"] if prior[2] else None),
                "P2": (current["B2"], prior[5]["B2"] if prior[5] else None),
                "P3": (current["B2"], current["B1"]),
            }
            for arm, pair in arm_pairs.items():
                if pair[1] is None:
                    continue
                probabilities_side = [side_probability(float(p), side) for p in pair]
                conservative = min(probabilities_side)
                for threshold in base.THRESHOLDS:
                    key = (arm, threshold)
                    if key in used:
                        continue
                    if not all(p - entry - fee > threshold for p in probabilities_side):
                        continue
                    used.add(key)
                    trades.append(
                        {
                            "asset": market.asset,
                            "ticker": market.ticker,
                            "close_ms": market.close_ms,
                            "arm": arm,
                            "threshold": threshold,
                            "side": side,
                            "seconds_left": seconds_left,
                            "source_to_print_ms": print_ms - source_ms,
                            "entry": entry,
                            "fee": fee,
                            "model_probability": conservative,
                            "edge": conservative - entry - fee,
                            "label": market.label,
                            "pnl": payout - entry - fee,
                            "print_count": float(count),
                        }
                    )
    return trades, candidate_count


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--assets", nargs="+", default=base.ASSETS)
    p.add_argument("--history-dir", type=Path, default=Path("data/cf_exact/history"))
    p.add_argument("--tape-dir", type=Path, default=Path("../kalshi-scalp/data/rpl_tapes"))
    p.add_argument("--metadata-dir", type=Path, default=Path("../kalshi-scalp/data"))
    p.add_argument("--markets-per-asset", type=int, default=0)
    p.add_argument("--iterations", type=int, default=10_000)
    p.add_argument("--output", type=Path, default=Path("data/cf_exact/report_persistence.json"))
    p.add_argument("--trades-output", type=Path)
    args = p.parse_args()
    args.assets = [a.upper() for a in args.assets]

    markets = base.load_markets(args)
    trades, candidates = score(markets)
    report = {
        "preregistration": "PREREG_cf_exact_endgame_20261006.md Amendment 4",
        "markets": len(markets),
        "close_windows": len({m.close_ms for m in markets}),
        "causal_print_candidates": candidates,
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
