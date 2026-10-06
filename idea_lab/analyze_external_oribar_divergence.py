"""Post-publication executable test of Oribar's market-vs-spot divergence."""
from __future__ import annotations

import json
from pathlib import Path

try:
    from idea_lab.analyze_external_dead_contract import summarize
    from idea_lab.common import fee
except ModuleNotFoundError:
    from analyze_external_dead_contract import summarize
    from common import fee


MARKETS = Path(
    "/Users/kapil/proj/kalshi-scalp/data/oos_20261006/candles_BTC.json"
)
SPOT = Path(
    "/Users/kapil/proj/kalshi-scalp/data/oos_20261006/cb_BTC-USD.json"
)
OUTPUT = Path("idea_lab/external_oribar_divergence_report.json")
THRESHOLD = 0.65
MIN_VOLUME = 10.0


def main() -> None:
    markets = sorted(
        json.loads(MARKETS.read_text())["markets"],
        key=lambda row: int(row["open_ts"]),
    )
    # Coinbase bars are timestamped by open. At Kalshi close timestamp t,
    # the latest causally complete spot minute has open timestamp t-60.
    spot = {
        int(row[0]): float(row[4])
        for row in json.loads(SPOT.read_text())["bars"]
    }
    trades = []
    for market in markets:
        if market.get("result") not in ("yes", "no"):
            continue
        strike = float(str(market["floor_strike"]).replace(",", ""))
        t0 = int(market["open_ts"])
        close_ts = int(market["close_ts"])
        for row in sorted(market.get("bars") or [], key=lambda x: int(x["ts"])):
            if row.get("post_close"):
                continue
            ts = int(row["ts"])
            if ts >= close_ts:
                continue
            spot_price = spot.get(ts - 60)
            yes_bid = float(row["b"])
            if spot_price is None or float(row.get("v") or 0.0) < MIN_VOLUME:
                continue
            market_side = (
                "yes"
                if yes_bid > THRESHOLD
                else ("no" if yes_bid < 1.0 - THRESHOLD else None)
            )
            if market_side is None:
                continue
            spot_side = "yes" if spot_price > strike else "no"
            if spot_side == market_side:
                continue
            entry = (
                float(row["a"]) if market_side == "yes" else 1.0 - yes_bid
            )
            payout = float(market["result"] == market_side)
            trades.append(
                {
                    "ticker": market["ticker"],
                    "t0": t0,
                    "day": t0 // 86400,
                    "signal_ts": ts,
                    "minute": (ts - t0) // 60,
                    "market_side": market_side,
                    "spot_side": spot_side,
                    "spot": spot_price,
                    "strike": strike,
                    "yes_bid": yes_bid,
                    "entry": entry,
                    "result": market["result"],
                    "payout": payout,
                    "pnl": payout - entry - fee(entry),
                }
            )
            break
    report = {
        "preregistration": "PREREG_external_oribar_divergence_oos_20261006.md",
        "source_signal_commit": "59581f9ec5fbf7885af93946e674b9baaabb65bb",
        "source_volume_fix_commit": "7f00b0a8dd065bf7c6de4d2860d5ba3c08b2820f",
        "source_published_before_local_sample": True,
        "market_count": len(markets),
        "result": summarize(trades),
    }
    OUTPUT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
