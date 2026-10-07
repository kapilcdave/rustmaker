#!/usr/bin/env python3
"""Train and test the preregistered counterparty-conditioned print calibration."""
from __future__ import annotations

import argparse
import bisect
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import analyze_cf_rolling_endgame as base


ARM = "R1"
PROBABILITY_BINS = (0, 0.05, 0.10, 0.20, 0.35, 0.50, 0.65, 0.80, 0.90, 0.95, 1.000001)
ENTRY_BINS = (0, 0.02, 0.05, 0.10, 0.20, 0.35, 0.50, 0.65, 0.80, 0.90, 0.95, 0.98, 1.000001)


def numeric_bin(value: float, boundaries: tuple[float, ...]) -> tuple[float, float]:
    i = bisect.bisect_right(boundaries, value) - 1
    i = max(0, min(i, len(boundaries) - 2))
    return boundaries[i], boundaries[i + 1]


def cell_key(candidate: Dict[str, Any]) -> tuple:
    return (
        base.time_bin(candidate["seconds_left"]),
        numeric_bin(candidate["p_B2"], PROBABILITY_BINS),
        numeric_bin(candidate["entry"], ENTRY_BINS),
    )


def candidates(market: base.Market) -> Iterable[Dict[str, Any]]:
    cache: Dict[tuple[int, int], Optional[Dict[str, float]]] = {}
    for print_us, yes_cents, count, taker_side in market.prints:
        print_ms = int(print_us // 1_000)
        if not (market.close_ms - 60_000 <= print_ms < market.close_ms):
            continue
        index = market.at_or_before(print_ms - 1_000)
        if index is None:
            continue
        source_ms = int(market.times[index])
        seconds_left = max(1, int(math.ceil((market.close_ms - source_ms) / 1_000)))
        if seconds_left > 60:
            continue
        cache_key = (index, seconds_left)
        if cache_key not in cache:
            cache[cache_key] = market.probabilities(index, seconds_left)
        probs = cache[cache_key]
        if probs is None:
            continue
        side = str(taker_side).lower()
        if side == "yes":
            entry = float(yes_cents) / 100
            p_side = probs["B2"]
            payout = float(market.label)
        elif side == "no":
            entry = 1 - float(yes_cents) / 100
            p_side = 1 - probs["B2"]
            payout = float(1 - market.label)
        else:
            continue
        yield {
            "asset": market.asset,
            "ticker": market.ticker,
            "close_ms": market.close_ms,
            "side": side,
            "seconds_left": seconds_left,
            "source_to_print_ms": print_ms - source_ms,
            "entry": entry,
            "fee": base.taker_fee(entry),
            "p_B2": p_side,
            "payout": payout,
            "label": market.label,
            "print_count": float(count),
        }


def train_panel(
    markets: list[base.Market],
    counts: Dict[tuple, list[int]],
) -> Dict[str, Any]:
    raw_candidates = 0
    market_cells = 0
    for market in markets:
        first: Dict[tuple, int] = {}
        for candidate in candidates(market):
            raw_candidates += 1
            key = cell_key(candidate)
            first.setdefault(key, int(candidate["payout"]))
        market_cells += len(first)
        for key, label in first.items():
            counts[key][label] += 1
    return {
        "training_markets": len(markets),
        "training_raw_candidates": raw_candidates,
        "training_market_cells": market_cells,
    }


def test(
    markets: list[base.Market],
    counts: Dict[tuple, list[int]],
) -> tuple[list[Dict[str, Any]], Dict[str, Any]]:
    trades = []
    raw_candidates = 0
    eligible_candidates = 0
    markets_with_eligible = 0
    for market in markets:
        used = set()
        any_eligible = False
        for candidate in candidates(market):
            raw_candidates += 1
            losses, wins = counts.get(cell_key(candidate), (0, 0))
            n = losses + wins
            if n < 100:
                continue
            any_eligible = True
            eligible_candidates += 1
            probability = (wins + 1) / (n + 2)
            edge = probability - candidate["entry"] - candidate["fee"]
            for threshold in base.THRESHOLDS:
                if threshold in used or edge <= threshold:
                    continue
                used.add(threshold)
                trades.append(
                    {
                        **candidate,
                        "arm": ARM,
                        "threshold": threshold,
                        "model_probability": probability,
                        "edge": edge,
                        "pnl": candidate["payout"] - candidate["entry"] - candidate["fee"],
                        "train_markets": n,
                    }
                )
        markets_with_eligible += any_eligible
    return trades, {
        "holdout_markets": len(markets),
        "holdout_raw_candidates": raw_candidates,
        "holdout_eligible_candidates": eligible_candidates,
        "holdout_markets_with_eligible": markets_with_eligible,
    }


def load_panel(
    history_dir: Path,
    assets: list[str],
    tape_dir: Path,
    metadata_dir: Path,
) -> list[base.Market]:
    args = argparse.Namespace(
        assets=assets,
        history_dir=history_dir,
        tape_dir=tape_dir,
        metadata_dir=metadata_dir,
        markets_per_asset=0,
    )
    return base.load_markets(args)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--training-history-dirs",
        nargs="+",
        type=Path,
        default=[Path("data/cf_exact/history"), Path("data/cf_exact/validation_history")],
    )
    p.add_argument("--holdout-history-dir", type=Path, default=Path("data/cf_exact/third_history"))
    p.add_argument("--assets", nargs="+", default=base.ASSETS)
    p.add_argument("--tape-dir", type=Path, default=Path("../kalshi-scalp/data/rpl_tapes"))
    p.add_argument("--metadata-dir", type=Path, default=Path("../kalshi-scalp/data"))
    p.add_argument("--iterations", type=int, default=10_000)
    p.add_argument(
        "--output",
        type=Path,
        default=Path("data/cf_exact/report_counterparty_calibration.json"),
    )
    p.add_argument("--trades-output", type=Path)
    args = p.parse_args()
    assets = [asset.upper() for asset in args.assets]

    counts: Dict[tuple, list[int]] = defaultdict(lambda: [0, 0])
    panel_sizes = {}
    train_diagnostics = Counter()
    for history_dir in args.training_history_dirs:
        panel = load_panel(history_dir, assets, args.tape_dir, args.metadata_dir)
        panel_sizes[str(history_dir)] = len(panel)
        train_diagnostics.update(train_panel(panel, counts))
        del panel
    holdout = load_panel(args.holdout_history_dir, assets, args.tape_dir, args.metadata_dir)
    trades, test_diagnostics = test(holdout, counts)
    report = {
        "preregistration": "PREREG_cf_exact_endgame_20261006.md Amendment 8",
        "training_panel_sizes": panel_sizes,
        **dict(train_diagnostics),
        "training_cells": len(counts),
        "training_cells_ge_100": sum(sum(v) >= 100 for v in counts.values()),
        **test_diagnostics,
        "holdout_close_windows": len({m.close_ms for m in holdout}),
        "trades": len(trades),
        "summary": base.summarize_trades(trades, args.iterations, arms=(ARM,)),
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
