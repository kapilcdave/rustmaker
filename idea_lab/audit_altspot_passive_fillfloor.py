"""Fetch and score strict-through maker fills for the frozen altspot signals."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from common import day_cluster_se, norm_cdf

DATA = Path("/Users/kapil/proj/kalshi-scalp/data/oos_20261006")
BASE = "https://api.elections.kalshi.com/trade-api/v2"
START = 1789357500
END = 1791244800
PRIMARY_ASSETS = ("NEAR", "ZEC", "HYPE")
ALL_ASSETS = ("NEAR", "ZEC", "HYPE", "BNB")
KS = (2, 3, 5, 8)
BASIS_BPS = {"NEAR": 4.67, "ZEC": 1.87, "HYPE": 1.77, "BNB": 2.44}


def parse_ts_us(value: str) -> int | None:
    value = value.rstrip("Z")
    if "." in value:
        head, frac = value.split(".", 1)
        frac = (frac + "000000")[:6]
    else:
        head, frac = value, "000000"
    try:
        base = dt.datetime.strptime(head, "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError:
        return None
    return int(base.timestamp()) * 1_000_000 + int(frac)


class Http:
    def __init__(self, qps: float) -> None:
        self.lock = threading.Lock()
        self.next = 0.0
        self.gap = 1.0 / qps
        self.errors: dict[str, int] = {}

    def get(self, path: str, tries: int = 7) -> dict | None:
        for attempt in range(tries):
            with self.lock:
                now = time.monotonic()
                wait = self.next - now
                if wait > 0:
                    time.sleep(wait)
                self.next = max(now, self.next) + self.gap
            req = urllib.request.Request(
                BASE + path,
                headers={
                    "User-Agent": "kalshi-mm15-readonly-research/1.0",
                    "Accept": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                key = f"http_{exc.code}"
                with self.lock:
                    self.errors[key] = self.errors.get(key, 0) + 1
                if exc.code in (429, 500, 502, 503, 504):
                    time.sleep(min(12.0, 0.5 * 2**attempt))
                    continue
                return None
            except Exception as exc:  # noqa: BLE001
                key = type(exc).__name__
                with self.lock:
                    self.errors[key] = self.errors.get(key, 0) + 1
                time.sleep(min(12.0, 0.5 * 2**attempt))
        return None


def signals(assets: tuple[str, ...]) -> list[dict]:
    out: list[dict] = []
    for asset in assets:
        cb_rows = json.load(open(DATA / f"cb_{asset}-USD.json"))["bars"]
        cb = {int(row[0]): (float(row[3]), float(row[4])) for row in cb_rows}
        markets = json.load(open(DATA / f"candles_{asset}.json"))["markets"]
        for market in markets:
            if market.get("result") not in ("yes", "no"):
                continue
            t0 = int(market["open_ts"])
            if not START <= t0 < END:
                continue
            try:
                strike = float(str(market["floor_strike"]).replace(",", ""))
            except (TypeError, ValueError):
                continue
            history = [
                cb[t0 - 60 * i][1]
                for i in range(120, 0, -1)
                if t0 - 60 * i in cb
            ]
            if len(history) < 40:
                continue
            sigma = float(np.std(np.diff(np.log(history))))
            if sigma < 2e-5:
                continue
            bars = {
                int(row["ts"]): row
                for row in market["bars"]
                if not row.get("post_close")
            }
            adjusted = strike * math.exp(-BASIS_BPS[asset] * 1e-4)
            for k in KS:
                bar = bars.get(t0 + 60 * k)
                spot = cb.get(t0 + 60 * (k - 1))
                if (
                    not bar
                    or not spot
                    or bar.get("a") is None
                    or bar.get("b") is None
                ):
                    continue
                fair = float(
                    norm_cdf(
                        math.log(spot[1] / adjusted)
                        / (sigma * math.sqrt(max(15 - k, 0.5)))
                    )
                )
                ask = float(bar["a"])
                bid = float(bar["b"])
                side = None
                limit = None
                if fair - ask > 0.10 and 0 < bid < 1:
                    side, limit = "yes", bid
                elif bid - fair > 0.10 and 0 < ask < 1:
                    side, limit = "no", 1.0 - ask
                if side:
                    out.append(
                        {
                            "asset": asset,
                            "ticker": market["ticker"],
                            "t0": t0,
                            "close_ts": int(market["close_ts"]),
                            "decision_ts": t0 + 60 * k,
                            "day": t0 // 86400,
                            "k": k,
                            "side": side,
                            "limit": limit,
                            "yes_boundary": bid if side == "yes" else ask,
                            "result": market["result"],
                        }
                    )
                    break
    return sorted(out, key=lambda row: (row["t0"], row["asset"], row["ticker"]))


def fetch_one(http: Http, signal: dict) -> dict:
    ticker = urllib.parse.quote(signal["ticker"])
    rows: list[list] = []
    cursor = None
    truncated = False
    for _ in range(40):
        path = f"/markets/trades?ticker={ticker}&limit=1000"
        if cursor:
            path += "&cursor=" + urllib.parse.quote(cursor)
        payload = http.get(path)
        if payload is None:
            truncated = True
            break
        trades = payload.get("trades") or []
        for trade in trades:
            if trade.get("is_block_trade"):
                continue
            taker = trade.get("taker_side")
            stamp = parse_ts_us(str(trade.get("created_time") or ""))
            try:
                yes_price = float(trade["yes_price_dollars"])
                size = float(trade.get("count_fp") or 0)
            except (KeyError, TypeError, ValueError):
                continue
            if stamp is not None and taker in ("yes", "no") and size > 0:
                rows.append([stamp, yes_price, size, taker])
        cursor = payload.get("cursor")
        if not cursor or not trades:
            break
    else:
        truncated = True
    rows.sort()
    return {**signal, "truncated": truncated, "prints": rows}


def clustered(vals: list[float], days: list[int]) -> dict:
    if not vals:
        return {"n": 0}
    mean, se = day_cluster_se(vals, days)
    return {
        "n": len(vals),
        "mean": mean,
        "se": se,
        "lo95": mean - 1.96 * se,
        "hi95": mean + 1.96 * se,
    }


def score(
    rows: list[dict],
    assets: tuple[str, ...],
    latency_s: int,
    stress: float = 0.0,
    single_asset: bool = False,
    min_fills: int = 100,
) -> dict:
    submissions: list[float] = []
    days: list[int] = []
    submission_rows: list[dict] = []
    filled: list[dict] = []
    by_asset: dict[str, list[float]] = {asset: [] for asset in assets}
    for row in rows:
        if row["truncated"]:
            continue
        earliest = (row["decision_ts"] + latency_s) * 1_000_000
        fill_print = None
        for print_row in row["prints"]:
            stamp, yes_price, size, taker = print_row
            if stamp < earliest or stamp >= row["close_ts"] * 1_000_000:
                continue
            if row["side"] == "yes":
                through = taker == "no" and yes_price < row["yes_boundary"] - 1e-9
            else:
                through = taker == "yes" and yes_price > row["yes_boundary"] + 1e-9
            if through:
                fill_print = print_row
                break
        pnl = 0.0
        if fill_print is not None:
            win = row["result"] == row["side"]
            pnl = (1.0 if win else 0.0) - row["limit"] - stress
            filled.append(
                {
                    "ticker": row["ticker"],
                    "asset": row["asset"],
                    "t0": row["t0"],
                    "day": row["day"],
                    "k": row["k"],
                    "decision_ts": row["decision_ts"],
                    "side": row["side"],
                    "limit": row["limit"],
                    "pnl": pnl,
                    "fill_print": fill_print,
                    "seconds_to_fill": fill_print[0] / 1_000_000 - (row["decision_ts"] + latency_s),
                }
            )
        submissions.append(pnl)
        days.append(row["day"])
        submission_rows.append(
            {
                "ticker": row["ticker"],
                "asset": row["asset"],
                "t0": row["t0"],
                "day": row["day"],
                "pnl": pnl,
                "filled": fill_print is not None,
            }
        )
        by_asset[row["asset"]].append(pnl)
    split = len(submissions) // 2
    gross_positive = {
        asset: sum(max(value, 0.0) for value in values)
        for asset, values in by_asset.items()
    }
    gross_total = sum(gross_positive.values())
    summary = clustered(submissions, days)
    day_totals = {
        day: sum(item["pnl"] for item in submission_rows if item["day"] == day)
        for day in sorted(set(days))
    }
    remove_n = max(1, math.ceil(0.10 * len(day_totals))) if day_totals else 0
    removed_days = set(
        sorted(day_totals, key=lambda day: day_totals[day], reverse=True)[:remove_n]
    )
    after_best_day_delete = [
        item["pnl"] for item in submission_rows if item["day"] not in removed_days
    ]
    positive_day_total = sum(max(value, 0.0) for value in day_totals.values())
    cumulative = np.cumsum(np.asarray(submissions, dtype=float))
    running_high = np.maximum.accumulate(np.concatenate(([0.0], cumulative)))
    drawdown = np.concatenate(([0.0], cumulative)) - running_high
    summary.update(
        {
            "latency_s": latency_s,
            "stress": stress,
            "submissions": len(submissions),
            "fills": len(filled),
            "fill_rate": len(filled) / len(submissions) if submissions else None,
            "pnl_per_fill": (
                sum(row["pnl"] for row in filled) / len(filled) if filled else None
            ),
            "filled_wins": sum(row["pnl"] > 0 for row in filled),
            "filled_losses": sum(row["pnl"] < 0 for row in filled),
            "halves_submission_mean": [
                float(np.mean(submissions[:split])) if split else None,
                float(np.mean(submissions[split:])) if submissions[split:] else None,
            ],
            "mean_ex_best_10pct_days": (
                float(np.mean(after_best_day_delete)) if after_best_day_delete else None
            ),
            "largest_day_share_gross_positive": (
                max((max(value, 0.0) for value in day_totals.values()), default=0.0)
                / positive_day_total
                if positive_day_total
                else None
            ),
            "max_drawdown_dollars": float(-drawdown.min()),
            "per_asset": {
                asset: {
                    "submissions": len(values),
                    "mean": float(np.mean(values)) if values else None,
                    "total": float(np.sum(values)),
                    "gross_positive_share": (
                        gross_positive[asset] / gross_total if gross_total else None
                    ),
                }
                for asset, values in by_asset.items()
            },
        }
    )
    summary["pass"] = bool(
        summary["fills"] >= min_fills
        and summary.get("lo95", -1) > 0
        and all(value is not None and value > 0 for value in summary["halves_submission_mean"])
        and all(item["mean"] is not None and item["mean"] >= 0 for item in summary["per_asset"].values())
        and (
            single_asset
            or all(
                item["gross_positive_share"] is None
                or item["gross_positive_share"] <= 0.60
                for item in summary["per_asset"].values()
            )
        )
    )
    summary["filled_rows"] = filled
    summary["submission_rows"] = submission_rows
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path("idea_lab/altspot_passive_fillfloor_tapes.jsonl"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("idea_lab/altspot_passive_fillfloor_report.json"),
    )
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--qps", type=float, default=5.0)
    parser.add_argument("--assets", nargs="+", choices=ALL_ASSETS, default=list(PRIMARY_ASSETS))
    parser.add_argument("--single-asset", action="store_true")
    parser.add_argument("--min-fills", type=int, default=100)
    parser.add_argument(
        "--preregistration",
        default="PREREG_altspot_passive_fillfloor_20261006.md",
    )
    args = parser.parse_args()

    assets = tuple(args.assets)
    sigs = signals(assets)
    cached: dict[str, dict] = {}
    if args.cache.exists():
        for line in args.cache.read_text().splitlines():
            try:
                row = json.loads(line)
                cached[row["ticker"]] = row
            except (json.JSONDecodeError, KeyError):
                continue
    todo = [row for row in sigs if row["ticker"] not in cached]
    http = Http(args.qps)
    if todo:
        args.cache.parent.mkdir(parents=True, exist_ok=True)
        with args.cache.open("a") as handle, ThreadPoolExecutor(args.workers) as pool:
            for index, row in enumerate(pool.map(lambda item: fetch_one(http, item), todo), 1):
                cached[row["ticker"]] = row
                handle.write(json.dumps(row, separators=(",", ":")) + "\n")
                if index % 100 == 0:
                    handle.flush()
                    print(f"fetched {index}/{len(todo)}", flush=True)
    rows = [cached[row["ticker"]] for row in sigs if row["ticker"] in cached]
    report = {
        "preregistration": args.preregistration,
        "signals": len(sigs),
        "fetched": len(rows),
        "truncated": sum(bool(row["truncated"]) for row in rows),
        "http_errors": http.errors,
        "scores": [
            score(
                rows,
                assets,
                latency,
                stress,
                args.single_asset,
                args.min_fills,
            )
            for latency in (2, 5, 10)
            for stress in (0.0, 0.01)
        ],
    }
    args.output.write_text(json.dumps(report, indent=2))
    for item in report["scores"]:
        item.pop("filled_rows", None)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
