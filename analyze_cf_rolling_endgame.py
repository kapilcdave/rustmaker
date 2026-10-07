#!/usr/bin/env python3
"""Score exact rolling-average endgame models on real Kalshi print tapes.

This implements Amendment 1 of ``PREREG_cf_exact_endgame_20261006.md``.
Historical trades are an optimistic executable upper bound, not proof that an
additional taker would have received the same fill.
"""
from __future__ import annotations

import argparse
import bisect
import gzip
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence

import numpy as np


ASSETS = ["ETH", "SOL", "XRP", "DOGE", "BNB", "HYPE", "NEAR", "ZEC"]
THRESHOLDS = (0.02, 0.05, 0.10)
DECISION_SECONDS = (60, 45, 30, 20, 10, 5, 1)
DISTANCE_BINS = (-math.inf, -3, -2, -1, -0.5, 0, 0.5, 1, 2, 3, math.inf)
TIME_BINS = ((1, 5), (6, 10), (11, 20), (21, 30), (31, 45), (46, 60))


def ceil_centicent(value: float) -> float:
    if value <= 0:
        return 0.0
    return math.ceil((value - 1e-12) * 10_000) / 10_000


def taker_fee(price: float, contracts: float = 1.0) -> float:
    return ceil_centicent(0.07 * contracts * price * (1.0 - price))


def normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def load_metadata(path: Path) -> Dict[str, Dict[str, Any]]:
    return {row["ticker"]: row for row in json.loads(path.read_text())}


def load_tapes(path: Path) -> Dict[str, Dict[str, Any]]:
    out = {}
    with path.open() as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("prints") and not row.get("truncated"):
                out[row["ticker"]] = row
    return out


def quantile(values: Sequence[float], q: float) -> float:
    return float(np.quantile(np.asarray(values, dtype=float), q))


class Market:
    def __init__(self, asset: str, cached: Dict[str, Any], meta: Dict[str, Any], tape: Dict[str, Any]):
        self.asset = asset
        self.ticker = cached["ticker"]
        self.close_ms = int(tape["close_ts"] * 1000)
        self.target = float(meta["floor_strike"])
        self.label = 1 if str(meta["result"]).lower() == "yes" else 0
        ts = cached["live_data"]["details"]["timeseries"]
        self.times = np.asarray([int(x["t"]) for x in ts], dtype=np.int64)
        self.values = np.asarray([float(x["v"]) for x in ts], dtype=float)
        self.prints = sorted(tape["prints"], key=lambda x: x[0])

    def at_or_before(self, when_ms: int) -> Optional[int]:
        i = bisect.bisect_right(self.times, when_ms) - 1
        return i if i >= 0 else None

    def horizon_sd(self, index: int, horizon_s: int, lookback_s: int = 1800) -> Optional[float]:
        if horizon_s <= 0:
            return 0.0
        now = int(self.times[index])
        lo = now - lookback_s * 1000
        start = bisect.bisect_left(self.times, lo + horizon_s * 1000)
        diffs = []
        for j in range(start, index + 1):
            prior_t = int(self.times[j]) - horizon_s * 1000
            k = bisect.bisect_right(self.times, prior_t) - 1
            if k >= 0 and self.times[k] >= lo:
                diffs.append(float(self.values[j] - self.values[k]))
        if len(diffs) < 20:
            return None
        sd = statistics.pstdev(diffs)
        return sd if sd > 1e-12 else None

    def trend_shift(self, index: int, horizon_s: int) -> float:
        now = int(self.times[index])
        lo30 = now - 1800 * 1000
        lo15 = now - 15 * 1000
        start30 = bisect.bisect_left(self.times, lo30)
        changes = [
            float(self.values[j] - self.values[j - 1])
            for j in range(max(1, start30), index + 1)
            if self.times[j] - self.times[j - 1] <= 1500
        ]
        recent = [
            float(self.values[j] - self.values[j - 1])
            for j in range(max(1, bisect.bisect_left(self.times, lo15)), index + 1)
            if self.times[j] - self.times[j - 1] <= 1500
        ]
        if not changes or not recent:
            return 0.0
        lo, hi = quantile(changes, 0.10), quantile(changes, 0.90)
        slope = min(hi, max(lo, statistics.median(recent)))
        return slope * horizon_s

    def probabilities(self, source_index: int, seconds_left: int) -> Optional[Dict[str, float]]:
        sd = self.horizon_sd(source_index, seconds_left)
        if sd is None:
            return None
        value = float(self.values[source_index])
        b1 = normal_cdf((value - self.target) / sd)
        shift = self.trend_shift(source_index, seconds_left)
        b2 = normal_cdf((value + shift - self.target) / sd)
        return {"B1": b1, "B2": b2, "sd": sd, "value": value, "trend_shift": shift}


def load_markets(args: argparse.Namespace) -> list[Market]:
    markets = []
    for asset in args.assets:
        meta = load_metadata(args.metadata_dir / f"mkts_KX{asset}15M.json")
        tapes = load_tapes(args.tape_dir / f"KX{asset}15M.jsonl")
        paths = sorted(args.history_dir.glob(f"KX{asset}15M-*.json.gz"))
        if args.markets_per_asset:
            paths = paths[-args.markets_per_asset :]
        for path in paths:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                cached = json.load(handle)
            ticker = cached["ticker"]
            if ticker not in meta or ticker not in tapes:
                continue
            try:
                market = Market(asset, cached, meta[ticker], tapes[ticker])
            except (KeyError, TypeError, ValueError):
                continue
            if market.times.size >= 1800:
                markets.append(market)
    return sorted(markets, key=lambda m: (m.close_ms, m.asset))


def fixed_decisions(markets: Sequence[Market]) -> list[Dict[str, Any]]:
    rows = []
    for market in markets:
        for seconds_left in DECISION_SECONDS:
            index = market.at_or_before(market.close_ms - seconds_left * 1000)
            if index is None:
                continue
            probs = market.probabilities(index, seconds_left)
            if probs is None:
                continue
            rows.append(
                {
                    "asset": market.asset,
                    "ticker": market.ticker,
                    "close_ms": market.close_ms,
                    "seconds_left": seconds_left,
                    "label": market.label,
                    "distance": (probs["value"] - market.target) / probs["sd"],
                    **probs,
                }
            )
    return rows


def time_bin(seconds_left: int) -> tuple[int, int]:
    for lo, hi in TIME_BINS:
        if lo <= seconds_left <= hi:
            return lo, hi
    raise ValueError(seconds_left)


def distance_bin(value: float) -> tuple[float, float]:
    i = bisect.bisect_right(DISTANCE_BINS, value) - 1
    i = max(0, min(i, len(DISTANCE_BINS) - 2))
    return DISTANCE_BINS[i], DISTANCE_BINS[i + 1]


def empirical_walkforward(rows: Sequence[Dict[str, Any]]) -> list[Dict[str, Any]]:
    """B3, updated only after an entire market has been scored."""
    by_market: Dict[str, list[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_market[row["ticker"]].append(row)
    ordered = sorted(by_market.values(), key=lambda rs: (rs[0]["close_ms"], rs[0]["asset"]))
    counts: Dict[tuple, list[int]] = defaultdict(lambda: [0, 0])
    out = []
    for market_rows in ordered:
        # A market can contribute more than one fixed second to the same
        # coarse cell (notably 1s and 5s). Settlement is one Bernoulli
        # observation, so update a cell at most once per market.
        pending: Dict[tuple, int] = {}
        for row in market_rows:
            slope_sign = 1 if row["trend_shift"] > 0 else (-1 if row["trend_shift"] < 0 else 0)
            key = (
                row["asset"],
                time_bin(row["seconds_left"]),
                distance_bin(row["distance"]),
                slope_sign,
            )
            losses, wins = counts[key]
            n = losses + wins
            if n >= 30:
                out.append({**row, "B3": (wins + 1) / (n + 2), "B3_train_n": n})
            pending[key] = row["label"]
        for key, label in pending.items():
            counts[key][label] += 1
    return out


def scoring(rows: Sequence[Dict[str, Any]], arms: Sequence[str]) -> Dict[str, Any]:
    report: Dict[str, Any] = {}
    for arm in arms:
        selected = [r for r in rows if arm in r]
        if not selected:
            continue
        by_sec = {}
        for seconds_left in DECISION_SECONDS:
            cell = [r for r in selected if r["seconds_left"] == seconds_left]
            if not cell:
                continue
            probs = np.asarray([r[arm] for r in cell])
            labels = np.asarray([r["label"] for r in cell])
            clipped = np.clip(probs, 1e-6, 1 - 1e-6)
            by_sec[str(seconds_left)] = {
                "n": len(cell),
                "windows": len({r["close_ms"] for r in cell}),
                "brier": float(np.mean((probs - labels) ** 2)),
                "log_loss": float(-np.mean(labels * np.log(clipped) + (1 - labels) * np.log(1 - clipped))),
                "accuracy": float(np.mean((probs >= 0.5) == labels)),
                "mean_p": float(np.mean(probs)),
                "win_rate": float(np.mean(labels)),
            }
        report[arm] = by_sec
    return report


def print_upper_bound(markets: Sequence[Market]) -> list[Dict[str, Any]]:
    trades = []
    used = set()
    for market in markets:
        probability_cache: Dict[tuple[int, int], Optional[Dict[str, float]]] = {}
        for raw in market.prints:
            if all((market.ticker, arm, threshold) in used
                   for arm in ("B1", "B2") for threshold in THRESHOLDS):
                break
            print_us, yes_cents, count, taker_side = raw
            print_ms = int(print_us // 1000)
            if not (market.close_ms - 60_000 <= print_ms < market.close_ms):
                continue
            # The exact rolling-average sample must have been public for a full
            # second before this observed execution.
            source_index = market.at_or_before(print_ms - 1000)
            if source_index is None:
                continue
            source_ms = int(market.times[source_index])
            seconds_left = max(1, int(math.ceil((market.close_ms - source_ms) / 1000)))
            if seconds_left > 60:
                continue
            cache_key = (source_index, seconds_left)
            if cache_key not in probability_cache:
                probability_cache[cache_key] = market.probabilities(source_index, seconds_left)
            probs = probability_cache[cache_key]
            if probs is None:
                continue
            side = str(taker_side).lower()
            if side == "yes":
                entry = float(yes_cents) / 100.0
                payout = float(market.label)
                model_prob = {arm: probs[arm] for arm in ("B1", "B2")}
            elif side == "no":
                entry = 1.0 - float(yes_cents) / 100.0
                payout = float(1 - market.label)
                model_prob = {arm: 1.0 - probs[arm] for arm in ("B1", "B2")}
            else:
                continue
            fee = taker_fee(entry)
            for arm, probability in model_prob.items():
                edge = probability - entry - fee
                for threshold in THRESHOLDS:
                    key = (market.ticker, arm, threshold)
                    if key in used or edge <= threshold:
                        continue
                    used.add(key)
                    trades.append(
                        {
                            "asset": market.asset,
                            "ticker": market.ticker,
                            "close_ms": market.close_ms,
                            "arm": arm,
                            "threshold": threshold,
                            "side": side,
                            "seconds_left": seconds_left,
                            "source_to_print_ms": print_ms - source_ms,
                            "entry": entry,
                            "fee": fee,
                            "model_probability": probability,
                            "edge": edge,
                            "label": market.label,
                            "pnl": payout - entry - fee,
                            "print_count": float(count),
                        }
                    )
    return trades


def bootstrap_window_mean(trades: Sequence[Dict[str, Any]], stress: float, iterations: int = 10_000) -> list[float]:
    groups: Dict[int, list[float]] = defaultdict(list)
    for t in trades:
        groups[t["close_ms"]].append(t["pnl"] - stress)
    keys = sorted(groups)
    if not keys:
        return [math.nan, math.nan]
    rng = np.random.default_rng(20261006)
    draws = np.empty(iterations)
    for i in range(iterations):
        picked = rng.integers(0, len(keys), size=len(keys))
        values = [p for j in picked for p in groups[keys[j]]]
        draws[i] = np.mean(values)
    return [float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))]


def bootstrap_equal_window_mean(
    trades: Sequence[Dict[str, Any]],
    stress: float,
    iterations: int = 10_000,
) -> list[float]:
    groups: Dict[int, list[float]] = defaultdict(list)
    for trade in trades:
        groups[trade["close_ms"]].append(trade["pnl"] - stress)
    values = np.asarray(
        [statistics.fmean(groups[key]) for key in sorted(groups)],
        dtype=float,
    )
    if not len(values):
        return [math.nan, math.nan]
    rng = np.random.default_rng(20261006)
    picked = rng.integers(0, len(values), size=(iterations, len(values)))
    draws = values[picked].mean(axis=1)
    return [float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))]


def summarize_trades(
    trades: Sequence[Dict[str, Any]],
    iterations: int,
    arms: Sequence[str] = ("B1", "B2"),
) -> Dict[str, Any]:
    report: Dict[str, Any] = {}
    for arm in arms:
        report[arm] = {}
        for threshold in THRESHOLDS:
            cell = [t for t in trades if t["arm"] == arm and t["threshold"] == threshold]
            if not cell:
                continue
            windows = sorted({t["close_ms"] for t in cell})
            by_window = defaultdict(float)
            by_window_values: Dict[int, list[float]] = defaultdict(list)
            for t in cell:
                by_window[t["close_ms"]] += t["pnl"]
                by_window_values[t["close_ms"]].append(t["pnl"])
            window_means = {
                window: statistics.fmean(values)
                for window, values in by_window_values.items()
            }
            best_n = max(1, math.ceil(len(windows) * 0.10))
            best = set(sorted(windows, key=lambda w: by_window[w], reverse=True)[:best_n])
            trimmed = [t["pnl"] for t in cell if t["close_ms"] not in best]
            best_equal = set(
                sorted(windows, key=lambda w: window_means[w], reverse=True)[:best_n]
            )
            trimmed_equal = [
                value for window, value in window_means.items() if window not in best_equal
            ]
            first_half = windows[: len(windows) // 2]
            first = [t["pnl"] for t in cell if t["close_ms"] in set(first_half)]
            second = [t["pnl"] for t in cell if t["close_ms"] not in set(first_half)]
            first_equal = [window_means[w] for w in first_half]
            second_equal = [window_means[w] for w in windows[len(windows) // 2 :]]
            asset_pnl = {
                asset: sum(t["pnl"] for t in cell if t["asset"] == asset)
                for asset in sorted({t["asset"] for t in cell})
            }
            stresses = {}
            for stress in (0.0, 0.005, 0.01):
                values = [t["pnl"] - stress for t in cell]
                stresses[f"{stress*100:.1f}c"] = {
                    "mean_c": statistics.fmean(values) * 100,
                    "ci_c": [x * 100 for x in bootstrap_window_mean(cell, stress, iterations)],
                    "equal_window_mean_c": (
                        statistics.fmean(window_means.values()) - stress
                    )
                    * 100,
                    "equal_window_ci_c": [
                        x * 100
                        for x in bootstrap_equal_window_mean(cell, stress, iterations)
                    ],
                }
            clips = {}
            for clip in (1, 3, 10, 25):
                dollars = sum((t["pnl"] - 0.01) * min(clip, t["print_count"]) for t in cell)
                clips[str(clip)] = {
                    "total_dollars_after_1c_stress": dollars,
                    "full_size_share": sum(t["print_count"] >= clip for t in cell) / len(cell),
                }
            total_abs = sum(abs(v) for v in asset_pnl.values()) or 1.0
            report[arm][f"{threshold:.2f}"] = {
                "trades": len(cell),
                "windows": len(windows),
                "losses": sum(t["pnl"] < 0 for t in cell),
                "mean_c": statistics.fmean(t["pnl"] for t in cell) * 100,
                "median_c": statistics.median(t["pnl"] for t in cell) * 100,
                "mean_ex_best_10pct_windows_c": statistics.fmean(trimmed) * 100 if trimmed else None,
                "equal_window_mean_c": statistics.fmean(window_means.values()) * 100,
                "equal_window_median_c": statistics.median(window_means.values()) * 100,
                "equal_window_mean_ex_best_10pct_windows_c": (
                    statistics.fmean(trimmed_equal) * 100 if trimmed_equal else None
                ),
                "chronological_halves_c": [
                    statistics.fmean(first) * 100 if first else None,
                    statistics.fmean(second) * 100 if second else None,
                ],
                "equal_window_chronological_halves_c": [
                    statistics.fmean(first_equal) * 100 if first_equal else None,
                    statistics.fmean(second_equal) * 100 if second_equal else None,
                ],
                "mean_seconds_left": statistics.fmean(t["seconds_left"] for t in cell),
                "mean_source_to_print_ms": statistics.fmean(t["source_to_print_ms"] for t in cell),
                "asset_pnl_dollars": asset_pnl,
                "largest_asset_abs_share": max(abs(v) for v in asset_pnl.values()) / total_abs,
                "stress": stresses,
                "clips": clips,
            }
    return report


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--assets", nargs="+", default=ASSETS)
    p.add_argument("--history-dir", type=Path, default=Path("data/cf_exact/history"))
    p.add_argument("--tape-dir", type=Path, default=Path("../kalshi-scalp/data/rpl_tapes"))
    p.add_argument("--metadata-dir", type=Path, default=Path("../kalshi-scalp/data"))
    p.add_argument("--iterations", type=int, default=10_000)
    p.add_argument("--markets-per-asset", type=int, default=0)
    p.add_argument("--output", type=Path, default=Path("data/cf_exact/report.json"))
    p.add_argument("--trades-output", type=Path)
    args = p.parse_args()
    args.assets = [a.upper() for a in args.assets]

    markets = load_markets(args)
    fixed = fixed_decisions(markets)
    b3 = empirical_walkforward(fixed)
    trades = print_upper_bound(markets)
    report = {
        "preregistration": "PREREG_cf_exact_endgame_20261006.md Amendment 1",
        "markets": len(markets),
        "assets": args.assets,
        "close_windows": len({m.close_ms for m in markets}),
        "fixed_decision_rows": len(fixed),
        "b3_rows": len(b3),
        "probability_scoring": scoring(fixed, ("B1", "B2")),
        "empirical_scoring": scoring(b3, ("B3",)),
        "print_upper_bound_trades": len(trades),
        "print_upper_bound": summarize_trades(trades, args.iterations),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True))
    if args.trades_output:
        args.trades_output.parent.mkdir(parents=True, exist_ok=True)
        with args.trades_output.open("w") as handle:
            for trade in trades:
                handle.write(json.dumps(trade, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
