#!/usr/bin/env python3
"""Score prospective paper signals from ``collect_public_cf_endgame.py``.

The live-data GET completes before the market GET in every poll. A signal
therefore uses only an exact rolling-average point already returned before the
book snapshot request. REST snapshots are not atomic and are never labeled as
fills.
"""
from __future__ import annotations

import argparse
import bisect
import gzip
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import numpy as np
import requests

import analyze_cf_rolling_endgame as base


REST = "https://external-api.kalshi.com/trade-api/v2"
ARMS = ("B1", "B2", "Q1", "P1", "P2", "P3")


def rows_from_open_gzip(path: Path) -> Iterable[Dict[str, Any]]:
    """Yield complete JSON lines and tolerate a writer's missing gzip trailer."""
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except (EOFError, gzip.BadGzipFile):
        return


def money(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    return float(str(value).replace("$", "").replace(",", ""))


def time_ms(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1_000)


class RollingState:
    def __init__(self) -> None:
        self.times: list[int] = []
        self.values: list[float] = []

    def add(self, points: Iterable[Dict[str, Any]]) -> None:
        for point in points:
            t, value = int(point["t"]), float(point["v"])
            if self.times and t == self.times[-1]:
                self.values[-1] = value
            elif not self.times or t > self.times[-1]:
                self.times.append(t)
                self.values.append(value)

    def at_or_before(self, when_ms: int) -> Optional[int]:
        i = bisect.bisect_right(self.times, when_ms) - 1
        return i if i >= 0 else None

    def horizon_sd(self, index: int, horizon_s: int, lookback_s: int = 1_800) -> Optional[float]:
        now = self.times[index]
        lo = now - lookback_s * 1_000
        start = bisect.bisect_left(self.times, lo + horizon_s * 1_000)
        diffs = []
        for j in range(start, index + 1):
            prior_t = self.times[j] - horizon_s * 1_000
            k = bisect.bisect_right(self.times, prior_t) - 1
            if k >= 0 and self.times[k] >= lo:
                diffs.append(self.values[j] - self.values[k])
        if len(diffs) < 20:
            return None
        sd = statistics.pstdev(diffs)
        return sd if sd > 1e-12 else None

    def trend_shift(self, index: int, horizon_s: int) -> float:
        now = self.times[index]
        lo30, lo15 = now - 1_800_000, now - 15_000
        start30 = bisect.bisect_left(self.times, lo30)
        changes = [
            self.values[j] - self.values[j - 1]
            for j in range(max(1, start30), index + 1)
            if self.times[j] - self.times[j - 1] <= 1_500
        ]
        recent = [
            self.values[j] - self.values[j - 1]
            for j in range(max(1, bisect.bisect_left(self.times, lo15)), index + 1)
            if self.times[j] - self.times[j - 1] <= 1_500
        ]
        if not changes or not recent:
            return 0.0
        lo, hi = np.quantile(changes, [0.10, 0.90])
        return min(float(hi), max(float(lo), statistics.median(recent))) * horizon_s

    def probabilities(self, index: int, horizon_s: int, target: float) -> Optional[Dict[str, float]]:
        sd = self.horizon_sd(index, horizon_s)
        if sd is None:
            return None
        value = self.values[index]
        shift = self.trend_shift(index, horizon_s)
        b1 = base.normal_cdf((value - target) / sd)
        b2 = base.normal_cdf((value + shift - target) / sd)
        decay = 1 - (horizon_s + 1) / 120
        q1 = base.normal_cdf((value + shift * decay - target) / sd)
        return {"B1": b1, "B2": b2, "Q1": q1, "sd": sd, "value": value, "shift": shift}


def choose_side(
    probability_yes: float,
    bid: Optional[float],
    ask: Optional[float],
) -> Optional[Dict[str, float | str]]:
    choices = []
    if ask is not None and 0 < ask < 1:
        fee = base.taker_fee(ask)
        choices.append(
            {
                "side": "yes",
                "entry": ask,
                "fee": fee,
                "edge": probability_yes - ask - fee,
                "side_probability": probability_yes,
            }
        )
    if bid is not None and 0 < bid < 1:
        entry = 1 - bid
        fee = base.taker_fee(entry)
        choices.append(
            {
                "side": "no",
                "entry": entry,
                "fee": fee,
                "edge": 1 - probability_yes - entry - fee,
                "side_probability": 1 - probability_yes,
            }
        )
    return max(choices, key=lambda choice: float(choice["edge"])) if choices else None


def score_capture(
    path: Path,
) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]], Dict[str, Any]]:
    states: Dict[str, RollingState] = defaultdict(RollingState)
    used = set()
    signals = []
    forecasts = []
    forecast_seen = set()
    latencies = defaultdict(list)
    edge_samples: Dict[str, list[float]] = defaultdict(list)
    counts = Counter()
    tickers = set()
    for row in rows_from_open_gzip(path):
        counts[row.get("kind", "unknown")] += 1
        if row.get("kind") != "poll":
            continue
        asset = row["asset"]
        states[asset].add(row.get("points") or [])
        book = row.get("book") or {}
        ticker = row.get("ticker")
        tickers.add(ticker)
        latencies["live_request_ms"].append((row["live_end_us"] - row["live_start_us"]) / 1_000)
        latencies["book_request_ms"].append((row["book_end_us"] - row["book_start_us"]) / 1_000)
        latencies["serial_gap_ms"].append((row["book_start_us"] - row["live_end_us"]) / 1_000)
        try:
            close_ms = time_ms(book["close_time"])
            target = float(book["floor_strike"])
        except (KeyError, TypeError, ValueError):
            continue
        decision_ms = row["book_end_us"] // 1_000
        if not (close_ms - 60_000 <= decision_ms < close_ms):
            continue
        counts["final_window_poll"] += 1
        bid, ask = money(book.get("yes_bid_dollars")), money(book.get("yes_ask_dollars"))
        if not ((ask is not None and 0 < ask < 1) or (bid is not None and 0 < bid < 1)):
            counts["final_window_bad_book"] += 1
            continue
        counts["final_window_valid_book"] += 1
        state = states[asset]
        index = state.at_or_before(row["live_end_us"] // 1_000)
        if index is None:
            counts["final_window_no_point"] += 1
            continue
        source_ms = state.times[index]
        horizon = max(1, int(math.ceil((close_ms - source_ms) / 1_000)))
        if horizon > 60:
            counts["final_window_stale_point"] += 1
            continue
        current = state.probabilities(index, horizon, target)
        if current is None:
            counts["final_window_no_model"] += 1
            continue
        counts["final_window_model"] += 1
        forecast_key = (ticker, source_ms)
        if forecast_key not in forecast_seen:
            forecast_seen.add(forecast_key)
            forecasts.append(
                {
                    "asset": asset,
                    "ticker": ticker,
                    "close_ms": close_ms,
                    "source_ms": source_ms,
                    "available_ms": row["live_end_us"] // 1_000,
                    "source_horizon_s": horizon,
                    "B1": current["B1"],
                    "B2": current["B2"],
                    "Q1": current["Q1"],
                }
            )
        prior = {}
        for lag in (2, 5):
            prior_index = state.at_or_before(source_ms - lag * 1_000)
            if prior_index is None:
                prior[lag] = None
                continue
            prior_horizon = max(1, int(math.ceil((close_ms - state.times[prior_index]) / 1_000)))
            prior[lag] = state.probabilities(prior_index, prior_horizon, target)

        arm_components: Dict[str, list[float]] = {
            "B1": [current["B1"]],
            "B2": [current["B2"]],
            "Q1": [current["Q1"]],
            "P1": [current["B2"], prior[2]["B2"]] if prior[2] else [],
            "P2": [current["B2"], prior[5]["B2"]] if prior[5] else [],
            "P3": [current["B2"], current["B1"]],
        }
        for arm, probabilities_yes in arm_components.items():
            if not probabilities_yes:
                continue
            choices = [choose_side(p, bid, ask) for p in probabilities_yes]
            if any(choice is None for choice in choices):
                continue
            sides = {choice["side"] for choice in choices}
            if len(sides) != 1:
                continue
            conservative = min(choices, key=lambda choice: float(choice["edge"]))
            edge_samples[arm].append(float(conservative["edge"]))
            for threshold in base.THRESHOLDS:
                key = (ticker, arm, threshold)
                if key in used or float(conservative["edge"]) <= threshold:
                    continue
                used.add(key)
                side = str(conservative["side"])
                depth = (
                    money(book.get("yes_ask_size_fp"))
                    if side == "yes"
                    else money(book.get("yes_bid_size_fp"))
                )
                signals.append(
                    {
                        "asset": asset,
                        "ticker": ticker,
                        "close_ms": close_ms,
                        "decision_ms": decision_ms,
                        "arm": arm,
                        "threshold": threshold,
                        "side": side,
                        "seconds_left": (close_ms - decision_ms) / 1_000,
                        "source_to_book_ms": decision_ms - source_ms,
                        # `summarize_trades` uses the historical field name.
                        # Here the executable observation is a REST book rather
                        # than a tape print, but the causal age is the same
                        # quantity and must be present for shared scoring.
                        "source_to_print_ms": decision_ms - source_ms,
                        "entry": float(conservative["entry"]),
                        "fee": float(conservative["fee"]),
                        "yes_bid": bid,
                        "yes_ask": ask,
                        "yes_bid_size": money(book.get("yes_bid_size_fp")),
                        "yes_ask_size": money(book.get("yes_ask_size_fp")),
                        "model_probability": float(conservative["side_probability"]),
                        "edge": float(conservative["edge"]),
                        "displayed_depth": depth,
                        "live_request_ms": (row["live_end_us"] - row["live_start_us"]) / 1_000,
                        "book_request_ms": (row["book_end_us"] - row["book_start_us"]) / 1_000,
                    }
                )

    latency_report = {}
    for name, values in latencies.items():
        latency_report[name] = {
            "n": len(values),
            "p50": float(np.quantile(values, 0.50)),
            "p90": float(np.quantile(values, 0.90)),
            "p99": float(np.quantile(values, 0.99)),
            "max": max(values),
        }
    edge_report = {}
    for arm, values in edge_samples.items():
        edge_report[arm] = {
            "n": len(values),
            "mean_c": statistics.fmean(values) * 100,
            "p50_c": float(np.quantile(values, 0.50)) * 100,
            "p90_c": float(np.quantile(values, 0.90)) * 100,
            "p99_c": float(np.quantile(values, 0.99)) * 100,
            "max_c": max(values) * 100,
            "positive_share": sum(value > 0 for value in values) / len(values),
            "over_2c_share": sum(value > 0.02 for value in values) / len(values),
        }
    return signals, forecasts, {
        "rows": dict(counts),
        "tickers": len(tickers),
        "latencies_ms": latency_report,
        "paper_edge_distribution": edge_report,
    }


def score_forecasts(
    forecasts: list[Dict[str, Any]],
    outcomes: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    by_ticker: Dict[str, list[Dict[str, Any]]] = defaultdict(list)
    for row in forecasts:
        if row["ticker"] in outcomes:
            by_ticker[row["ticker"]].append(row)
    report = {}
    for arm in ("B1", "B2", "Q1"):
        by_seconds = {}
        for seconds in base.DECISION_SECONDS:
            chosen = []
            for ticker, rows in by_ticker.items():
                close_ms = rows[0]["close_ms"]
                deadline = close_ms - seconds * 1_000
                eligible = [row for row in rows if row["available_ms"] <= deadline]
                if eligible:
                    chosen.append(max(eligible, key=lambda row: row["available_ms"]))
            if not chosen:
                continue
            probabilities = np.asarray([row[arm] for row in chosen], dtype=float)
            labels = np.asarray([outcomes[row["ticker"]]["label"] for row in chosen], dtype=float)
            clipped = np.clip(probabilities, 1e-6, 1 - 1e-6)
            by_seconds[str(seconds)] = {
                "n": len(chosen),
                "windows": len({row["close_ms"] for row in chosen}),
                "brier": float(np.mean((probabilities - labels) ** 2)),
                "log_loss": float(
                    -np.mean(
                        labels * np.log(clipped)
                        + (1 - labels) * np.log(1 - clipped)
                    )
                ),
                "accuracy": float(np.mean((probabilities >= 0.5) == labels)),
                "mean_data_age_ms": statistics.fmean(
                    row["available_ms"] - row["source_ms"] for row in chosen
                ),
            }
        report[arm] = by_seconds
    return report


def fetch_outcomes(tickers: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    out = {}
    session = requests.Session()
    for ticker in sorted(set(tickers)):
        response = session.get(f"{REST}/markets/{ticker}", timeout=15)
        if response.status_code != 200:
            continue
        market = response.json().get("market", {})
        result = str(market.get("result", "")).lower()
        if result not in ("yes", "no"):
            continue
        out[ticker] = {"label": int(result == "yes"), "result": result}
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("capture", type=Path)
    p.add_argument("--fetch-outcomes", action="store_true")
    p.add_argument("--iterations", type=int, default=10_000)
    p.add_argument("--output", type=Path, default=Path("data/cf_exact/live_report.json"))
    p.add_argument("--signals-output", type=Path)
    args = p.parse_args()

    signals, forecasts, diagnostics = score_capture(args.capture)
    outcome_tickers = {s["ticker"] for s in signals} | {f["ticker"] for f in forecasts}
    outcomes = fetch_outcomes(outcome_tickers) if args.fetch_outcomes else {}
    settled = []
    for signal in signals:
        outcome = outcomes.get(signal["ticker"])
        if not outcome:
            continue
        label = outcome["label"]
        payout = label if signal["side"] == "yes" else 1 - label
        settled.append(
            {
                **signal,
                "label": label,
                "pnl": payout - signal["entry"] - signal["fee"],
                "print_count": signal["displayed_depth"] or 0,
            }
        )
    report = {
        "capture": str(args.capture),
        "execution_status": "paper REST snapshots; non-atomic; no fills claimed",
        **diagnostics,
        "signals": len(signals),
        "signal_markets": len({s["ticker"] for s in signals}),
        "forecast_rows": len(forecasts),
        "settled_probability_scoring": (
            score_forecasts(forecasts, outcomes) if outcomes else {}
        ),
        "settled_signals": len(settled),
        "settled_summary": (
            base.summarize_trades(settled, args.iterations, arms=ARMS) if settled else {}
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True))
    if args.signals_output:
        args.signals_output.parent.mkdir(parents=True, exist_ok=True)
        with args.signals_output.open("w") as handle:
            for signal in signals:
                handle.write(json.dumps(signal, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
