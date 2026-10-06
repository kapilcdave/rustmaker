"""Historical candle audit of L_i AND U_j => U_i portfolios."""
from __future__ import annotations

import json
from pathlib import Path

from common import fee

ASSETS = ("BTC", "ETH", "SOL", "XRP", "HYPE")
LEADER = Path("idea_lab/crypto_leader_history_sample.json")
DIRECTION_DIR = Path("/Users/kapil/proj/kalshi-scalp/data/oos_20261006")
OUTPUT = Path("idea_lab/crypto_leader_directional_history_report.json")


def main() -> None:
    leader_events = json.loads(LEADER.read_text()).get("events") or {}
    directions = {}
    for asset in ASSETS:
        markets = json.loads((DIRECTION_DIR / f"candles_{asset}.json").read_text())["markets"]
        directions[asset] = {
            int(market["open_ts"]): market
            for market in markets
            if market.get("result") in ("yes", "no")
        }
    best = {}
    opportunities = []
    valid_events = observations = 0
    for event, event_row in leader_events.items():
        leader_legs = event_row["legs"]
        t0 = int(leader_legs["BTC"]["open_ts"])
        if any(t0 not in directions[asset] for asset in ASSETS):
            continue
        if any(
            int(directions[asset][t0]["close_ts"]) != int(leader_legs[asset]["close_ts"])
            for asset in ASSETS
        ):
            continue
        valid_events += 1
        leader_bars = {
            asset: {int(row["ts"]): row for row in leader_legs[asset]["bars"]}
            for asset in ASSETS
        }
        direction_bars = {
            asset: {
                int(row["ts"]): row
                for row in directions[asset][t0]["bars"]
                if not row.get("post_close")
            }
            for asset in ASSETS
        }
        common = set.intersection(
            *(set(leader_bars[a]) for a in ASSETS),
            *(set(direction_bars[a]) for a in ASSETS),
        )
        for stamp in sorted(common):
            if any(
                leader_bars[a][stamp].get("bid") is None
                or direction_bars[a][stamp].get("b") is None
                or direction_bars[a][stamp].get("a") is None
                for a in ASSETS
            ):
                continue
            observations += 1
            for i in ASSETS:
                for j in ASSETS:
                    if i == j:
                        continue
                    prices = [
                        1.0 - float(leader_bars[i][stamp]["bid"]),
                        1.0 - float(direction_bars[j][stamp]["b"]),
                        float(direction_bars[i][stamp]["a"]),
                    ]
                    cost = sum(price + fee(price) for price in prices)
                    item = {
                        "event": event,
                        "ts": stamp,
                        "relation": f"L_{i}+U_{j}=>U_{i}",
                        "cost": cost,
                        "margin": 1.0 - cost,
                        "margin_after_1c_per_leg": 0.97 - cost,
                    }
                    name = item["relation"]
                    if name not in best or item["margin"] > best[name]["margin"]:
                        best[name] = item
                    if item["margin"] > 0:
                        opportunities.append(item)
    report = {
        "preregistration": "PREREG_crypto_leader_directional_implication_20261006.md",
        "evidence": "historical same-minute candle closes; non-atomic",
        "sample_events": len(leader_events),
        "valid_events": valid_events,
        "observations": observations,
        "best": best,
        "opportunities": len(opportunities),
        "stressed_opportunities": sum(
            item["margin_after_1c_per_leg"] > 0 for item in opportunities
        ),
        "opportunity_events": len({item["event"] for item in opportunities}),
    }
    OUTPUT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
