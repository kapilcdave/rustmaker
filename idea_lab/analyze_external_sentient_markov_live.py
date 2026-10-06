"""Score the prospective Sentient Markov direct-book journal."""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import requests

try:
    from idea_lab.analyze_external_dead_contract import summarize
    from idea_lab.common import fee
except ModuleNotFoundError:
    from analyze_external_dead_contract import summarize
    from common import fee


INPUT = Path("idea_lab/external_sentient_markov_live_20261006.jsonl.gz")
OUTPUT = Path("idea_lab/external_sentient_markov_live_report.json")
REST = "https://api.elections.kalshi.com/trade-api/v2"


def outcome(ticker: str) -> str | None:
    response = requests.get(f"{REST}/markets/{ticker}", timeout=12)
    response.raise_for_status()
    result = response.json().get("market", {}).get("result")
    return result if result in ("yes", "no") else None


def main() -> None:
    raw = []
    with gzip.open(INPUT, "rt", encoding="utf-8") as handle:
        while True:
            try:
                line = handle.readline()
            except EOFError:
                break
            if not line:
                break
            if not line.strip():
                continue
            try:
                raw.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    decisions = [row for row in raw if row.get("kind") == "decision"]
    signals = [row for row in decisions if row.get("signal") is not None]
    rows = []
    for record in signals:
        signal = record["signal"]
        ticker = record["market"]["ticker"]
        result = outcome(ticker)
        if result is None:
            continue
        entry = float(signal["entry"])
        payout = 1.0 if result == signal["side"] else 0.0
        t0 = int(record["market"]["open_ts"])
        rows.append(
            {
                "ticker": ticker,
                "t0": t0,
                "day": t0 // 86400,
                "side": signal["side"],
                "entry": entry,
                "result": result,
                "pnl": payout - entry - fee(entry),
            }
        )
    report = {
        "preregistration": "PREREG_external_sentient_markov_live_20261006.md",
        "records": len(raw),
        "decisions": len(decisions),
        "signals": len(signals),
        "settled_signals": len(rows),
        "summary": summarize(rows),
    }
    OUTPUT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
