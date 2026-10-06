"""Score the prospective direct-book thin-altcoin spot-gap journal."""
from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, "/Users/kapil/proj/kalshi-scalp")
import fetch_15m_candles as F  # type: ignore


def fee(price: float) -> float:
    return math.ceil(0.07 * price * (1.0 - price) * 100.0 - 1e-9) / 100.0


def outcome(ticker: str) -> str | None:
    data = F.get(f"/markets/{ticker}")
    if not data or data is F.MISSING:
        return None
    result = (data.get("market") or {}).get("result")
    return result if result in ("yes", "no") else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "journal", nargs="?", default="idea_lab/altspot_tilt_live_20261006.jsonl.gz"
    )
    ap.add_argument(
        "--output", default="idea_lab/altspot_tilt_live_report.json"
    )
    ap.add_argument(
        "--preregistration", default="PREREG_altspot_tilt_live_20261006.md"
    )
    ap.add_argument("--fetch-outcomes", action="store_true")
    ap.add_argument("--single-asset", action="store_true")
    args = ap.parse_args()
    rows = []
    with gzip.open(args.journal, "rt", encoding="utf-8") as fh:
        try:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except EOFError:
            pass
    decisions = [row for row in rows if row.get("kind") == "decision"]
    decisions = [
        row
        for row in decisions
        if 2 <= float(row.get("decision_lag_s", 1e9)) <= 15
    ]
    complete = [
        row for row in decisions
        if row.get("spot") is not None and row.get("book") is not None
    ]
    signals = [row for row in complete if row.get("signal") is not None]
    outcomes = {}
    if args.fetch_outcomes:
        for ticker in sorted({row["market"]["ticker"] for row in signals}):
            outcomes[ticker] = outcome(ticker)
    settled = []
    for row in signals:
        result = outcomes.get(row["market"]["ticker"])
        if result not in ("yes", "no"):
            continue
        signal = row["signal"]
        price = float(signal["price"])
        win = result == signal["side"]
        pnl = float(win) - price - fee(price)
        settled.append(
            {
                "asset": row["market"]["asset"],
                "ticker": row["market"]["ticker"],
                "k": row["k"],
                "side": signal["side"],
                "price": price,
                "displayed_size": signal.get("size"),
                "result": result,
                "pnl": pnl,
            }
        )
    values = [row["pnl"] for row in settled]
    half = len(values) // 2
    by_asset: dict[str, float] = defaultdict(float)
    gross: dict[str, float] = defaultdict(float)
    for row in settled:
        by_asset[row["asset"]] += row["pnl"]
        gross[row["asset"]] += max(0.0, row["pnl"])
    gross_total = sum(gross.values())
    report = {
        "preregistration": args.preregistration,
        "decisions": len(decisions),
        "complete_decisions": len(complete),
        "operational_completion": len(complete) / max(len(decisions), 1),
        "signals": len(signals),
        "settled_signals": len(settled),
        "mean_pnl_c": 100 * float(np.mean(values)) if values else None,
        "chronological_halves_c": (
            [
                100 * float(np.mean(values[:half])),
                100 * float(np.mean(values[half:])),
            ]
            if half
            else [None, None]
        ),
        "per_asset_pnl": dict(sorted(by_asset.items())),
        "largest_asset_share_gross_positive": (
            max(gross.values()) / gross_total if gross_total else None
        ),
        "passes_operational_gate": len(decisions) > 0
        and len(complete) / len(decisions) >= 0.95,
        "passes_paper_economics_gate": bool(
            len(settled) >= 20
            and np.mean(values) > 0
            and half
            and np.mean(values[:half]) > 0
            and np.mean(values[half:]) > 0
            and (
                args.single_asset
                or (
                    gross_total
                    and max(gross.values()) / gross_total <= 0.60
                )
            )
        ),
        "settled_rows": settled,
    }
    Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
