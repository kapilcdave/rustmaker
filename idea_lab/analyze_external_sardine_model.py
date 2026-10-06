"""Transfer Sardine's causal BTC probability to local executable asks."""
from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path

try:
    from idea_lab.analyze_external_dead_contract import summarize
    from idea_lab.common import fee
except ModuleNotFoundError:
    from analyze_external_dead_contract import summarize
    from common import fee


SOURCE = Path("idea_lab/external_sardine_history_btc_15m.csv.gz")
MARKETS = Path(
    "/Users/kapil/proj/kalshi-scalp/data/oos_20261006/candles_BTC.json"
)
OUTPUT = Path("idea_lab/external_sardine_model_report.json")
CHECKPOINTS = (720, 480, 300, 120)
MIN_EDGE = 0.03


def load_probabilities() -> dict[tuple[str, int], float]:
    out = {}
    with gzip.open(SOURCE, "rt", newline="") as handle:
        for row in csv.DictReader(handle):
            seconds = int(row["checkpoint_seconds_left"])
            if seconds in CHECKPOINTS and row.get("sardine_p"):
                out[(row["ticker"], seconds)] = float(row["sardine_p"])
    return out


def main() -> None:
    probs = load_probabilities()
    markets = sorted(
        json.loads(MARKETS.read_text())["markets"],
        key=lambda row: int(row["open_ts"]),
    )
    trades = []
    joined = 0
    for market in markets:
        if market.get("result") not in ("yes", "no"):
            continue
        close_ts = int(market["close_ts"])
        bars = {
            int(row["ts"]): row
            for row in market.get("bars") or []
            if not row.get("post_close")
        }
        for seconds in CHECKPOINTS:
            p_yes = probs.get((market["ticker"], seconds))
            row = bars.get(close_ts - seconds)
            if p_yes is None or row is None:
                continue
            joined += 1
            yes_ask = float(row["a"])
            no_ask = 1.0 - float(row["b"])
            candidates = [
                ("yes", yes_ask, p_yes),
                ("no", no_ask, 1.0 - p_yes),
            ]
            side, entry, p_side = max(
                candidates, key=lambda x: x[2] - x[1] - fee(x[1])
            )
            model_edge = p_side - entry - fee(entry)
            if model_edge < MIN_EDGE:
                continue
            payout = float(market["result"] == side)
            trades.append(
                {
                    "ticker": market["ticker"],
                    "close_ts": close_ts,
                    "day": close_ts // 86400,
                    "checkpoint_seconds_left": seconds,
                    "side": side,
                    "entry": entry,
                    "sardine_p_yes": p_yes,
                    "model_edge": model_edge,
                    "result": market["result"],
                    "payout": payout,
                    "pnl": payout - entry - fee(entry),
                }
            )
            break
    report = {
        "preregistration": "PREREG_external_sardine_model_oos_20261006.md",
        "source_sha256": (
            "ee19f095a2909a449712ab943137e2979777100dd7680b2bdf09710eacf1c775"
        ),
        "source_windows": 7544,
        "market_count": len(markets),
        "joined_checkpoint_rows": joined,
        "checkpoints": list(CHECKPOINTS),
        "minimum_modeled_edge_c": MIN_EDGE * 100,
        "result": summarize(trades),
    }
    OUTPUT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
