"""Score the prospective direct-book BTC minute-3 cushion journal."""
from __future__ import annotations

import argparse
import gzip
import json
import math
import urllib.request
from pathlib import Path

import numpy as np

try:
    from idea_lab.common import fee
except ModuleNotFoundError:
    from common import fee

CUTOFF_NS = 1_791_302_699_288_579_000


def result(ticker: str) -> str | None:
    url = "https://api.elections.kalshi.com/trade-api/v2/markets/" + ticker
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            value = str((json.load(response).get("market") or {}).get("result") or "")
        return value if value in ("yes", "no") else None
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "journal", nargs="?", type=Path,
        default=Path("idea_lab/btc_cushion_m3_live_20261006.jsonl.gz"),
    )
    parser.add_argument("--fetch-outcomes", action="store_true")
    parser.add_argument(
        "--output", type=Path,
        default=Path("idea_lab/btc_cushion_m3_live_report.json"),
    )
    args = parser.parse_args()
    rows = []
    if args.journal.exists():
        try:
            with gzip.open(args.journal, "rt") as handle:
                for line in handle:
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if int(value.get("wall_ns", 0)) >= CUTOFF_NS:
                        rows.append(value)
        except EOFError:
            pass
    signals = [row for row in rows if row.get("kind") == "decision" and row.get("signal")]
    outcomes = {}
    if args.fetch_outcomes:
        for ticker in {row["market"]["ticker"] for row in signals}:
            outcomes[ticker] = result(ticker)
    settled = []
    for row in signals:
        signal = row["signal"]
        outcome = outcomes.get(row["market"]["ticker"])
        if outcome not in ("yes", "no"):
            continue
        price = float(signal["price"])
        pnl = float(outcome == signal["side"]) - price - fee(price)
        settled.append({
            "ticker": row["market"]["ticker"], "side": signal["side"],
            "z": row["z"], "price": price, "displayed_size": signal.get("size"),
            "result": outcome, "pnl": pnl,
        })
    values = [row["pnl"] for row in settled]
    split = len(values) // 2
    report = {
        "preregistration": "PREREG_btc_cushion_m3_live_20261006.md",
        "cutoff_ns": CUTOFF_NS,
        "records": len(rows),
        "decisions": sum(row.get("kind") == "decision" for row in rows),
        "signals": len(signals),
        "settled": len(settled),
        "mean_c": float(np.mean(values) * 100) if values else None,
        "extra_1c_stress_c": float(np.mean(values) * 100 - 1) if values else None,
        "halves_c": [
            float(np.mean(values[:split]) * 100) if split else None,
            float(np.mean(values[split:]) * 100) if values[split:] else None,
        ],
        "rows": settled,
    }
    report["passes_short_screen"] = False  # cannot meet preregistered n=20 overnight
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
