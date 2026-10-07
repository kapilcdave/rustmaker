"""Score real live-bookres fills into paired spread P&L and residual inventory."""
import argparse
import gzip
import json
from collections import defaultdict, deque
from pathlib import Path


def rows(paths):
    for path in paths:
        with gzip.open(path, "rt") as stream:
            for line in stream:
                yield json.loads(line)


def score(paths, results):
    fills = defaultdict(list)
    stop = None
    for row in rows(paths):
        if row.get("k") == "fill":
            fill = row["v"]
            fills[fill["market_ticker"]].append((row["t"], fill))
        elif row.get("k") == "stop":
            stop = row["v"]

    markets = []
    for ticker, events in sorted(fills.items()):
        inventory = {"yes": deque(), "no": deque()}
        paired_pnl = 0.0
        paired_contracts = 0.0
        total_contracts = 0.0
        for _, fill in sorted(events):
            side = fill["purchased_side"]
            other = "no" if side == "yes" else "yes"
            quantity = float(fill["count_fp"])
            yes_price = float(fill["yes_price_dollars"])
            cost = yes_price if side == "yes" else 1.0 - yes_price
            total_contracts += quantity
            while quantity > 1e-9 and inventory[other]:
                other_cost, other_quantity = inventory[other][0]
                matched = min(quantity, other_quantity)
                paired_pnl += matched * (1.0 - cost - other_cost)
                paired_contracts += matched
                quantity -= matched
                other_quantity -= matched
                if other_quantity <= 1e-9:
                    inventory[other].popleft()
                else:
                    inventory[other][0] = (other_cost, other_quantity)
            if quantity > 1e-9:
                inventory[side].append((cost, quantity))

        residual = []
        residual_pnl = None
        result = results.get(ticker)
        if result:
            residual_pnl = 0.0
        for side in ["yes", "no"]:
            for cost, quantity in inventory[side]:
                residual.append({"side": side, "cost": cost, "contracts": quantity})
                if result:
                    residual_pnl += quantity * ((1.0 if side == result else 0.0) - cost)
        markets.append({
            "ticker": ticker,
            "fills": len(events),
            "contracts": total_contracts,
            "paired_contracts": paired_contracts,
            "paired_pnl_dollars": paired_pnl,
            "result": result,
            "residual": residual,
            "residual_pnl_dollars": residual_pnl,
            "total_pnl_dollars": None if residual_pnl is None else paired_pnl + residual_pnl,
        })

    settled = [market for market in markets if market["total_pnl_dollars"] is not None]
    return {
        "files": [str(path) for path in paths],
        "markets": markets,
        "fills": sum(market["fills"] for market in markets),
        "contracts": sum(market["contracts"] for market in markets),
        "paired_contracts": sum(market["paired_contracts"] for market in markets),
        "paired_pnl_dollars": sum(market["paired_pnl_dollars"] for market in markets),
        "settled_total_pnl_dollars": sum(market["total_pnl_dollars"] for market in settled),
        "settled_markets": len(settled),
        "stop": stop,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("journals", nargs="+", type=Path)
    parser.add_argument("--result", action="append", default=[], metavar="TICKER=yes|no")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    results = {}
    for item in args.result:
        ticker, result = item.rsplit("=", 1)
        if result not in {"yes", "no"}:
            raise ValueError(item)
        results[ticker] = result
    report = score(args.journals, results)
    text = json.dumps(report, indent=2) + "\n"
    if args.out:
        args.out.write_text(text)
    print(text, end="")


if __name__ == "__main__":
    main()
