#!/usr/bin/env python3
"""Score the preregistered overlap-aware rolling-mean trend model."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, Optional

import analyze_cf_rolling_endgame as base


ARM = "Q1"


def q1_probability(market: base.Market, index: int, seconds_left: int) -> Optional[Dict[str, float]]:
    probs = market.probabilities(index, seconds_left)
    if probs is None:
        return None
    decay = 1 - (seconds_left + 1) / 120
    mean = probs["value"] + probs["trend_shift"] * decay
    return {
        **probs,
        ARM: base.normal_cdf((mean - market.target) / probs["sd"]),
        "decay": decay,
        "forecast_mean": mean,
    }


def fixed_rows(markets: list[base.Market]) -> list[Dict[str, Any]]:
    rows = []
    for market in markets:
        for seconds_left in base.DECISION_SECONDS:
            index = market.at_or_before(market.close_ms - seconds_left * 1_000)
            if index is None:
                continue
            probs = q1_probability(market, index, seconds_left)
            if probs is None:
                continue
            rows.append(
                {
                    "asset": market.asset,
                    "ticker": market.ticker,
                    "close_ms": market.close_ms,
                    "seconds_left": seconds_left,
                    "label": market.label,
                    ARM: probs[ARM],
                }
            )
    return rows


def print_trades(markets: list[base.Market]) -> list[Dict[str, Any]]:
    trades = []
    used = set()
    for market in markets:
        cache: Dict[tuple[int, int], Optional[Dict[str, float]]] = {}
        for raw in market.prints:
            if all((market.ticker, threshold) in used for threshold in base.THRESHOLDS):
                break
            print_us, yes_cents, count, taker_side = raw
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
            key = (index, seconds_left)
            if key not in cache:
                cache[key] = q1_probability(market, index, seconds_left)
            probs = cache[key]
            if probs is None:
                continue
            side = str(taker_side).lower()
            if side == "yes":
                entry = float(yes_cents) / 100
                payout = float(market.label)
                probability = probs[ARM]
            elif side == "no":
                entry = 1 - float(yes_cents) / 100
                payout = float(1 - market.label)
                probability = 1 - probs[ARM]
            else:
                continue
            fee = base.taker_fee(entry)
            edge = probability - entry - fee
            for threshold in base.THRESHOLDS:
                trade_key = (market.ticker, threshold)
                if trade_key in used or edge <= threshold:
                    continue
                used.add(trade_key)
                trades.append(
                    {
                        "asset": market.asset,
                        "ticker": market.ticker,
                        "close_ms": market.close_ms,
                        "arm": ARM,
                        "threshold": threshold,
                        "side": side,
                        "seconds_left": seconds_left,
                        "source_to_print_ms": print_ms - source_ms,
                        "entry": entry,
                        "fee": fee,
                        "model_probability": probability,
                        "edge": edge,
                        "label": market.label,
                        "pnl": payout - entry - fee,
                        "print_count": float(count),
                    }
                )
    return trades


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--assets", nargs="+", default=base.ASSETS)
    p.add_argument("--history-dir", type=Path, default=Path("data/cf_exact/history"))
    p.add_argument("--tape-dir", type=Path, default=Path("../kalshi-scalp/data/rpl_tapes"))
    p.add_argument("--metadata-dir", type=Path, default=Path("../kalshi-scalp/data"))
    p.add_argument("--markets-per-asset", type=int, default=0)
    p.add_argument("--iterations", type=int, default=10_000)
    p.add_argument("--output", type=Path, default=Path("data/cf_exact/report_overlap_trend.json"))
    p.add_argument("--trades-output", type=Path)
    args = p.parse_args()
    args.assets = [a.upper() for a in args.assets]

    markets = base.load_markets(args)
    fixed = fixed_rows(markets)
    trades = print_trades(markets)
    report = {
        "preregistration": "PREREG_cf_exact_endgame_20261006.md Amendment 5",
        "markets": len(markets),
        "close_windows": len({m.close_ms for m in markets}),
        "fixed_rows": len(fixed),
        "probability_scoring": base.scoring(fixed, (ARM,)),
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
