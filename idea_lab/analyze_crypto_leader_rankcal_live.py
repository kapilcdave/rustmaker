"""Prospective direct-book score for the frozen Coin Race rank calibrator."""
from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

try:
    from idea_lab.common import fee
    from idea_lab.collect_public_hourly_dominance import get
except ModuleNotFoundError:
    from common import fee
    from collect_public_hourly_dominance import get

sys.path.insert(0, "/Users/kapil/proj/kalshi-scalp")
from fetch_cb_minutes import fetch_chunk  # type: ignore

ASSETS = ("BTC", "ETH", "SOL", "XRP", "HYPE")
KS = (5, 8, 12)
CUTOFF_NS = 1_791_298_375_016_036_000
PARAMS = {
    5: np.asarray(
        [-0.3391638451, -0.2813714652, -0.0622432344, 0.2247149700,
         0.4580635747, 1.4958202938]
    ),
    8: np.asarray(
        [-0.3702546322, -0.4275506268, 0.0352786079, 0.3101045031,
         0.4524221481, 1.7622721386]
    ),
    12: np.asarray(
        [-0.2362998533, -0.3773055647, -0.0479355808, 0.3354599039,
         0.3260810949, 2.3957180714]
    ),
}


def load_rows(path: Path) -> list[dict]:
    rows = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        while True:
            try:
                line = handle.readline()
            except EOFError:
                # A collector may still be writing the final gzip member.
                break
            if not line:
                break
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                row.get("kind") == "snapshot"
                and int(row.get("wall_ns", 0)) >= CUTOFF_NS
                and row.get("complete")
            ):
                rows.append(row)
    return rows


def standardized_returns(cb: dict, t0: int, k: int) -> np.ndarray | None:
    decision = t0 + 60 * (k - 1)
    z = []
    for asset in ASSETS:
        returns = []
        for stamp in range(t0 - 120 * 60, t0, 60):
            if stamp in cb[asset] and stamp - 60 in cb[asset]:
                returns.append(math.log(cb[asset][stamp] / cb[asset][stamp - 60]))
        if len(returns) < 80 or decision not in cb[asset] or t0 - 60 not in cb[asset]:
            return None
        scale = float(np.std(returns, ddof=1)) * math.sqrt(k)
        if not math.isfinite(scale) or scale <= 0:
            return None
        value = math.log(cb[asset][decision] / cb[asset][t0 - 60]) / scale
        z.append(float(np.clip(value, -5.0, 5.0)))
    return np.asarray(z)


def outcome(ticker: str) -> tuple[str | None, float | None]:
    payload, *_ = get(f"/markets/{ticker}")
    market = None if not payload else payload.get("market")
    if not market:
        return None, None
    result = market.get("result")
    raw = market.get("settlement_value_dollars")
    try:
        payout = float(raw)
    except (TypeError, ValueError):
        payout = 1.0 if result == "yes" else (0.0 if result == "no" else None)
    return result, payout


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "journal",
        nargs="?",
        type=Path,
        default=Path("idea_lab/crypto_leader_complete_set_live_20261006.jsonl.gz"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("idea_lab/crypto_leader_rankcal_live_report.json"),
    )
    args = parser.parse_args()
    rows = load_rows(args.journal)
    by_event: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_event[row["event"]["event_ticker"]].append(row)

    if by_event:
        opens = [int(group[0]["event"]["open_ts"]) for group in by_event.values()]
        start, end = min(opens) - 121 * 60, max(opens) + 13 * 60
        cb = {}
        for asset in ASSETS:
            bars = fetch_chunk(f"{asset}-USD", start, end)
            cb[asset] = {int(bar[0]): float(bar[4]) for bar in bars}
    else:
        cb = {asset: {} for asset in ASSETS}

    decisions = []
    signals = []
    for event, group in sorted(
        by_event.items(), key=lambda item: int(item[1][0]["event"]["open_ts"])
    ):
        group.sort(key=lambda row: int(row["wall_ns"]))
        meta = group[0]["event"]
        t0 = int(meta["open_ts"])
        for k in KS:
            lower_ns = (t0 + 60 * k + 2) * 1_000_000_000
            upper_ns = (t0 + 60 * k + 15) * 1_000_000_000
            snap = next(
                (
                    row
                    for row in group
                    if lower_ns <= int(row["wall_ns"]) <= upper_ns
                ),
                None,
            )
            if snap is None:
                decisions.append(
                    {"event": event, "k": k, "complete": False, "reason": "no_timely_book"}
                )
                continue
            z = standardized_returns(cb, t0, k)
            if z is None:
                decisions.append(
                    {"event": event, "k": k, "complete": False, "reason": "spot_history"}
                )
                continue
            params = PARAMS[k]
            scores = params[: len(ASSETS)] + params[-1] * z
            scores -= scores.max()
            probabilities = np.exp(scores)
            probabilities /= probabilities.sum()
            choices = []
            for index, asset in enumerate(ASSETS):
                leg = snap["legs"][asset]
                book = leg["book"]
                no_bid = book.get("no_bid")
                if no_bid is None:
                    continue
                ask = 1.0 - float(no_bid)
                if not 0 < ask < 1:
                    continue
                edge = float(probabilities[index]) - ask - fee(ask)
                choices.append((edge, asset, ask, float(probabilities[index]), leg))
            if not choices:
                decisions.append(
                    {"event": event, "k": k, "complete": False, "reason": "no_asks"}
                )
                continue
            edge, asset, ask, probability, leg = max(choices)
            decision = {
                "event": event,
                "ticker": meta["tickers"][asset],
                "open_ts": t0,
                "close_ts": int(meta["close_ts"]),
                "wall_ns": int(snap["wall_ns"]),
                "k": k,
                "complete": True,
                "asset": asset,
                "z": z.tolist(),
                "probability": probability,
                "ask": ask,
                "fee": fee(ask),
                "edge": edge,
                "displayed_size": leg["book"].get("no_bid_size"),
                "request_start_ns": leg.get("request_start_ns"),
                "request_finish_ns": leg.get("request_finish_ns"),
            }
            decisions.append(decision)
            if edge > 0.10:
                result, payout = outcome(decision["ticker"])
                signal = dict(decision)
                signal["result"] = result
                signal["payout"] = payout
                signal["pnl"] = (
                    None if payout is None else payout - ask - decision["fee"]
                )
                signals.append(signal)
                break

    settled = [row for row in signals if row["pnl"] is not None]
    pnls = [float(row["pnl"]) for row in settled]
    split = len(pnls) // 2
    by_asset_pnl: dict[str, float] = defaultdict(float)
    gross_positive: dict[str, float] = defaultdict(float)
    for row in settled:
        value = float(row["pnl"])
        by_asset_pnl[row["asset"]] += value
        gross_positive[row["asset"]] += max(value, 0.0)
    gross_total = sum(gross_positive.values())
    shares = {
        asset: value / gross_total if gross_total else None
        for asset, value in gross_positive.items()
    }
    report = {
        "preregistration": "PREREG_crypto_leader_rankcal_live_20261006.md",
        "cutoff_ns": CUTOFF_NS,
        "evidence": "prospective sequential direct-orderbook paper",
        "journal_rows": len(rows),
        "events": len(by_event),
        "decisions": len(decisions),
        "complete_decisions": sum(bool(row.get("complete")) for row in decisions),
        "signals": len(signals),
        "settled": len(settled),
        "mean_c": float(np.mean(pnls) * 100) if pnls else None,
        "one_cent_stressed_mean_c": (
            float(np.mean(pnls) * 100 - 1) if pnls else None
        ),
        "halves_c": [
            float(np.mean(pnls[:split]) * 100) if split else None,
            float(np.mean(pnls[split:]) * 100) if pnls[split:] else None,
        ],
        "per_asset_pnl": dict(by_asset_pnl),
        "gross_positive_shares": shares,
        "decision_rows": decisions,
        "signal_rows": signals,
    }
    report["passes_short_prospective_screen"] = bool(
        len(settled) >= 10
        and report["mean_c"] is not None
        and report["mean_c"] > 0
        and report["one_cent_stressed_mean_c"] > 0
        and all(value is not None and value > 0 for value in report["halves_c"])
        and all(value is None or value <= 0.60 for value in shares.values())
    )
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
