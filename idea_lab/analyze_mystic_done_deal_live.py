"""Score the prospective direct-book external Mystic journal."""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np
import requests

from common import fee

CUTOFF_NS = 1_791_298_875_631_679_000
PATH = Path("idea_lab/mystic_done_deal_live_20261006.jsonl.gz")
OUTPUT = Path("idea_lab/mystic_done_deal_live_report.json")


def load() -> list[dict]:
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


def outcome(ticker: str) -> str | None:
    response = requests.get(
        f"https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}",
        timeout=10,
    )
    if not response.ok:
        return None
    return (response.json().get("market") or {}).get("result")


def main() -> None:
    decisions = load()
    signals = [row for row in decisions if row.get("signal")]
    for row in signals:
        result = outcome(row["market"]["ticker"])
        row["result"] = result
        row["pnl"] = (
            None
            if result not in {"yes", "no"}
            else (1.0 if result == row["side"] else 0.0)
            - float(row["entry"])
            - fee(float(row["entry"]))
        )
    settled = [row for row in signals if row["pnl"] is not None]
    pnls = [float(row["pnl"]) for row in settled]
    split = len(pnls) // 2
    completion = (
        sum(bool(row.get("complete")) for row in decisions) / len(decisions)
        if decisions
        else None
    )
    report = {
        "preregistration": "PREREG_mystic_done_deal_live_20261006.md",
        "cutoff_ns": CUTOFF_NS,
        "evidence": "prospective sequential direct-orderbook paper",
        "decisions": len(decisions),
        "complete_decisions": sum(bool(row.get("complete")) for row in decisions),
        "operational_completion": completion,
        "signals": len(signals),
        "settled": len(settled),
        "losses": sum(row["pnl"] < 0 for row in settled),
        "mean_c": float(np.mean(pnls) * 100) if pnls else None,
        "one_cent_stressed_mean_c": (
            float(np.mean(pnls) * 100 - 1) if pnls else None
        ),
        "halves_c": [
            float(np.mean(pnls[:split]) * 100) if split else None,
            float(np.mean(pnls[split:]) * 100) if pnls[split:] else None,
        ],
        "signal_rows": signals,
    }
    report["passes_short_screen"] = bool(
        completion is not None
        and completion >= 0.95
        and len(settled) >= 10
        and (report["losses"] >= 1 or len(settled) >= 100)
        and report["one_cent_stressed_mean_c"] is not None
        and report["one_cent_stressed_mean_c"] > 0
        and all(value is not None and value > 0 for value in report["halves_c"])
    )
    OUTPUT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
