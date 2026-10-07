#!/usr/bin/env python3
"""Recompute trade summaries without rerunning expensive probability models."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import analyze_cf_rolling_endgame as base


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("trades", type=Path)
    p.add_argument("--arms", nargs="+", required=True)
    p.add_argument("--iterations", type=int, default=10_000)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    rows = [json.loads(line) for line in args.trades.read_text().splitlines() if line]
    report = base.summarize_trades(rows, args.iterations, arms=args.arms)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
