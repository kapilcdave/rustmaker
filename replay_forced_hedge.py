"""Replay residual-flattening policies over recorded live book-residual journals.

The opening fills are the real fills from the frozen run. Synthetic closes use only
top-of-book states received by that time. This is an exploratory exit overlay, not
a full counterfactual: closing inventory would have changed later live quotes and
therefore later opening fills.
"""

import argparse
import gzip
import heapq
import json
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Book:
    yes_bid: float
    yes_bid_size: float
    yes_ask: float
    yes_ask_size: float


@dataclass
class Lot:
    lot_id: int
    ticker: str
    side: str
    cost: float
    quantity: float
    opened_us: int
    pending: bool = False
    forced: bool = False


@dataclass(frozen=True)
class Policy:
    max_hold_s: Optional[float]
    take_profit_c: Optional[float]
    stop_loss_c: Optional[float]
    latency_ms: float
    slippage_c: float

    @property
    def name(self):
        def value(number, unit):
            return "none" if number is None else f"{number:g}{unit}"

        return (
            f"hold={value(self.max_hold_s, 's')},tp={value(self.take_profit_c, 'c')},"
            f"sl={value(self.stop_loss_c, 'c')},lat={self.latency_ms:g}ms,"
            f"slip={self.slippage_c:g}c"
        )


def load_events(paths):
    markets = defaultdict(list)
    for path in paths:
        with gzip.open(path, "rt") as stream:
            for line in stream:
                row = json.loads(line)
                if row.get("k") == "B":
                    value = row["v"]
                    markets[value[0]].append((row["t"], "book", value))
                elif row.get("k") == "fill":
                    fill = row["v"]
                    markets[fill["market_ticker"]].append((row["t"], "fill", fill))
    for events in markets.values():
        events.sort(key=lambda event: (event[0], event[1] != "book"))
    return markets


def load_results(paths):
    results = {}
    expected_contracts = 0.0
    expected_pnl = 0.0
    for path in paths:
        report = json.loads(path.read_text())
        expected_contracts += report["contracts"]
        expected_pnl += report["settled_total_pnl_dollars"]
        for market in report["markets"]:
            result = market.get("result")
            if result:
                old = results.setdefault(market["ticker"], result)
                if old != result:
                    raise ValueError(f"conflicting result for {market['ticker']}")
    return results, expected_contracts, expected_pnl


def fill_cost(fill):
    yes_price = float(fill["yes_price_dollars"])
    return yes_price if fill["purchased_side"] == "yes" else 1.0 - yes_price


def exit_quote(book, side):
    if side == "yes":
        return book.yes_bid, max(0.0, book.yes_bid_size - 1.0)
    return 1.0 - book.yes_ask, max(0.0, book.yes_ask_size - 1.0)


def summarize_market(ticker, result, actual_contracts, organic_pairs, organic_pnl,
                     synthetic_contracts, synthetic_pnl, settled_contracts,
                     settlement_pnl, exits, failed_exits, max_age_s):
    return {
        "ticker": ticker,
        "asset": "BTC" if ticker.startswith("KXBTC15M-") else "ETH",
        "result": result,
        "actual_contracts": actual_contracts,
        "organic_paired_contracts": organic_pairs,
        "organic_paired_pnl_dollars": organic_pnl,
        "synthetic_exit_contracts": synthetic_contracts,
        "synthetic_exit_pnl_dollars": synthetic_pnl,
        "settlement_residual_contracts": settled_contracts,
        "settlement_residual_pnl_dollars": settlement_pnl,
        "gross_pnl_dollars": organic_pnl + synthetic_pnl + settlement_pnl,
        "synthetic_exits": exits,
        "failed_exit_attempts": failed_exits,
        "max_synthetic_hold_s": max_age_s,
    }


def replay_market(ticker, events, result, policy):
    inventory = {"yes": deque(), "no": deque()}
    lots = {}
    pending = []
    sequence = 0
    next_lot_id = 0
    book = None
    actual_contracts = 0.0
    organic_pairs = 0.0
    organic_pnl = 0.0
    synthetic_contracts = 0.0
    synthetic_pnl = 0.0
    exits = 0
    failed_exits = 0
    max_age_s = 0.0

    def clean(side):
        while inventory[side] and inventory[side][0].quantity <= 1e-9:
            inventory[side].popleft()

    def schedule(lot, now_us, reason):
        nonlocal sequence
        if lot.quantity <= 1e-9 or lot.pending or book is None:
            if reason == "timeout":
                lot.forced = True
            return
        quote, _ = exit_quote(book, lot.side)
        minimum = max(0.0, quote - policy.slippage_c / 100.0)
        lot.pending = True
        sequence += 1
        arrival = now_us + round(policy.latency_ms * 1_000)
        heapq.heappush(pending, (arrival, sequence, lot.lot_id, reason, minimum))

    def evaluate(now_us, sides=("yes", "no")):
        if book is None:
            return
        for side in sides:
            for lot in list(inventory[side]):
                if lot.quantity <= 1e-9 or lot.pending:
                    continue
                quote, _ = exit_quote(book, lot.side)
                pnl_c = (quote - lot.cost) * 100.0
                age_s = (now_us - lot.opened_us) / 1_000_000.0
                if lot.forced or (policy.max_hold_s is not None and age_s >= policy.max_hold_s):
                    schedule(lot, now_us, "timeout")
                elif policy.take_profit_c is not None and pnl_c >= policy.take_profit_c:
                    schedule(lot, now_us, "take_profit")
                elif policy.stop_loss_c is not None and pnl_c <= -policy.stop_loss_c:
                    schedule(lot, now_us, "stop_loss")

    def process_pending(until_us):
        nonlocal synthetic_contracts, synthetic_pnl, exits, failed_exits, max_age_s
        while pending and pending[0][0] <= until_us:
            arrival, _, lot_id, reason, minimum = heapq.heappop(pending)
            lot = lots[lot_id]
            if reason == "timeout_trigger":
                lot.forced = True
                schedule(lot, arrival, "timeout")
                continue
            if lot.quantity <= 1e-9:
                lot.pending = False
                continue
            if book is None:
                lot.pending = False
                lot.forced = reason == "timeout" or lot.forced
                failed_exits += 1
                continue
            quote, available = exit_quote(book, lot.side)
            if quote + 1e-12 < minimum or available + 1e-9 < lot.quantity:
                lot.pending = False
                lot.forced = reason == "timeout" or lot.forced
                failed_exits += 1
                continue
            quantity = lot.quantity
            synthetic_contracts += quantity
            synthetic_pnl += quantity * (quote - lot.cost)
            max_age_s = max(max_age_s, (arrival - lot.opened_us) / 1_000_000.0)
            lot.quantity = 0.0
            lot.pending = False
            exits += 1
            clean(lot.side)

    for now_us, kind, value in events:
        process_pending(now_us)
        if kind == "book":
            book = Book(
                yes_bid=value[2] / 10_000.0,
                yes_bid_size=value[3] / 100.0,
                yes_ask=value[4] / 10_000.0,
                yes_ask_size=value[5] / 100.0,
            )
            evaluate(now_us)
            continue

        side = value["purchased_side"]
        other = "no" if side == "yes" else "yes"
        quantity = float(value["count_fp"])
        cost = fill_cost(value)
        actual_contracts += quantity
        clean(other)
        while quantity > 1e-9 and inventory[other]:
            old = inventory[other][0]
            matched = min(quantity, old.quantity)
            organic_pnl += matched * (1.0 - cost - old.cost)
            organic_pairs += matched
            quantity -= matched
            old.quantity -= matched
            clean(other)
        if quantity > 1e-9:
            lot = Lot(next_lot_id, ticker, side, cost, quantity, now_us)
            next_lot_id += 1
            lots[lot.lot_id] = lot
            inventory[side].append(lot)
            if policy.max_hold_s is not None:
                if policy.max_hold_s <= 0.0:
                    schedule(lot, now_us, "timeout")
                else:
                    sequence += 1
                    trigger = now_us + round(policy.max_hold_s * 1_000_000)
                    heapq.heappush(pending, (trigger, sequence, lot.lot_id, "timeout_trigger", 0.0))
            evaluate(now_us, (side,))

    last_us = events[-1][0] if events else 0
    process_pending(last_us)

    settlement_contracts = 0.0
    settlement_pnl = 0.0
    for side in ("yes", "no"):
        for lot in inventory[side]:
            if lot.quantity <= 1e-9:
                continue
            settlement_contracts += lot.quantity
            settlement_pnl += lot.quantity * ((1.0 if side == result else 0.0) - lot.cost)

    return summarize_market(
        ticker, result, actual_contracts, organic_pairs, organic_pnl,
        synthetic_contracts, synthetic_pnl, settlement_contracts,
        settlement_pnl, exits, failed_exits, max_age_s,
    )


def aggregate(markets, scope):
    selected = [market for market in markets if scope == "ALL" or market["asset"] == scope]
    gross = sum(market["gross_pnl_dollars"] for market in selected)
    synthetic = sum(market["synthetic_exit_contracts"] for market in selected)
    return {
        "markets": len(selected),
        "actual_contracts": sum(market["actual_contracts"] for market in selected),
        "organic_paired_contracts": sum(market["organic_paired_contracts"] for market in selected),
        "organic_paired_pnl_dollars": sum(market["organic_paired_pnl_dollars"] for market in selected),
        "synthetic_exit_contracts": synthetic,
        "synthetic_exit_pnl_dollars": sum(market["synthetic_exit_pnl_dollars"] for market in selected),
        "settlement_residual_contracts": sum(market["settlement_residual_contracts"] for market in selected),
        "settlement_residual_pnl_dollars": sum(market["settlement_residual_pnl_dollars"] for market in selected),
        "gross_pnl_dollars": gross,
        "net_pnl_at_0_5c_taker_fee": gross - synthetic * 0.005,
        "net_pnl_at_1c_taker_fee": gross - synthetic * 0.01,
        "synthetic_exits": sum(market["synthetic_exits"] for market in selected),
        "failed_exit_attempts": sum(market["failed_exit_attempts"] for market in selected),
        "max_synthetic_hold_s": max((market["max_synthetic_hold_s"] for market in selected), default=0.0),
    }


def policies():
    yielded = set()

    def add(policy):
        if policy not in yielded:
            yielded.add(policy)
            return policy
        return None

    baseline = add(Policy(None, None, None, 5.44, 0.0))
    if baseline:
        yield baseline
    for latency in (5.44, 20.0, 50.0):
        for slippage in (0.0, 1.0):
            for hold in (0.0, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0):
                policy = add(Policy(hold, None, None, latency, slippage))
                if policy:
                    yield policy
    for latency in (5.44, 20.0):
        for hold in (1.0, 5.0, 30.0, 120.0):
            for take_profit in (0.0, 1.0, 2.0):
                for stop_loss in (1.0, 2.0, 5.0, 10.0, 20.0):
                    policy = add(Policy(hold, take_profit, stop_loss, latency, 1.0))
                    if policy:
                        yield policy


def self_test():
    ticker = "KXETH15M-TEST"
    events = [
        (1_000_000, "book", [ticker, 1_000_000, 4900, 1000, 5100, 1000]),
        (1_100_000, "fill", {"purchased_side": "yes", "count_fp": "1.00", "yes_price_dollars": "0.4800"}),
    ]
    immediate = replay_market(ticker, events, "yes", Policy(0.0, None, None, 0.0, 0.0))
    assert abs(immediate["gross_pnl_dollars"] - 0.01) < 1e-9
    assert immediate["settlement_residual_contracts"] == 0.0
    events.append((1_200_000, "fill", {"purchased_side": "no", "count_fp": "1.00", "yes_price_dollars": "0.5200"}))
    organic = replay_market(ticker, events, "yes", Policy(None, None, None, 0.0, 0.0))
    assert abs(organic["organic_paired_pnl_dollars"] - 0.04) < 1e-9
    print("self-test passed")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("journals", nargs="*", type=Path)
    parser.add_argument("--scores", nargs="+", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.journals or not args.scores or not args.out:
        parser.error("journals, --scores, and --out are required")

    events = load_events(args.journals)
    results, expected_contracts, expected_pnl = load_results(args.scores)
    events = {ticker: ticker_events for ticker, ticker_events in events.items()
              if any(kind == "fill" for _, kind, _ in ticker_events)}
    missing = sorted(set(events) - set(results))
    if missing:
        raise ValueError(f"missing settlement results: {missing}")

    reports = []
    for policy in policies():
        markets = [replay_market(ticker, ticker_events, results[ticker], policy)
                   for ticker, ticker_events in sorted(events.items())]
        reports.append({
            "policy": policy.name,
            "parameters": policy.__dict__,
            "scopes": {scope: aggregate(markets, scope) for scope in ("ALL", "BTC", "ETH")},
            "markets": markets,
        })

    baseline = reports[0]["scopes"]["ALL"]
    if abs(baseline["actual_contracts"] - expected_contracts) > 1e-8:
        raise ValueError("baseline contract reconciliation failed")
    if abs(baseline["gross_pnl_dollars"] - expected_pnl) > 1e-8:
        raise ValueError("baseline P&L reconciliation failed")
    pair_only = [report for report in reports
                 if report["scopes"]["ALL"]["settlement_residual_contracts"] <= 1e-9]
    output = {
        "method": (
            "Exploratory causal exit overlay on fixed real opening fills. Synthetic FOK closes use "
            "the latest received touch, subtract one contract from displayed depth, and cannot alter "
            "later recorded opening fills. Fee columns charge only synthetic taker contracts."
        ),
        "journals": [str(path) for path in args.journals],
        "scores": [str(path) for path in args.scores],
        "policies_tested": len(reports),
        "baseline_reconciliation": {
            "expected_contracts": expected_contracts,
            "replayed_contracts": baseline["actual_contracts"],
            "expected_pnl_dollars": expected_pnl,
            "replayed_pnl_dollars": baseline["gross_pnl_dollars"],
            "ok": True,
        },
        "baseline": baseline,
        "best_pair_only_gross": sorted(pair_only, key=lambda report: report["scopes"]["ALL"]["gross_pnl_dollars"], reverse=True)[:10],
        "best_pair_only_at_0_5c_fee": sorted(pair_only, key=lambda report: report["scopes"]["ALL"]["net_pnl_at_0_5c_taker_fee"], reverse=True)[:10],
        "best_pair_only_at_1c_fee": sorted(pair_only, key=lambda report: report["scopes"]["ALL"]["net_pnl_at_1c_taker_fee"], reverse=True)[:10],
        "reports": reports,
    }
    args.out.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps({
        "policies_tested": len(reports),
        "baseline": baseline,
        "best_pair_only_gross": output["best_pair_only_gross"][0]["scopes"]["ALL"] if pair_only else None,
        "best_pair_only_at_0_5c_fee": output["best_pair_only_at_0_5c_fee"][0]["scopes"]["ALL"] if pair_only else None,
        "best_pair_only_at_1c_fee": output["best_pair_only_at_1c_fee"][0]["scopes"]["ALL"] if pair_only else None,
    }, indent=2))


if __name__ == "__main__":
    main()
