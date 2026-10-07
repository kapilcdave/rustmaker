#!/usr/bin/env python3
"""Cache public one-second CF rolling-average histories for existing print tapes.

The source is Kalshi's public ``/live_data/events/{event_ticker}`` endpoint.
Output is one gzip-compressed JSON object per market and is resumable.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import json
import random
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import requests


REST = "https://external-api.kalshi.com/trade-api/v2"
DEFAULT_ASSETS = ["ETH", "SOL", "XRP", "DOGE", "BNB", "HYPE", "NEAR", "ZEC"]


def tape_rows(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open() as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("prints") and not row.get("truncated"):
                yield row


def event_from_ticker(ticker: str) -> str:
    return ticker.rsplit("-", 1)[0]


def fetch_one(
    row: Dict[str, Any],
    output_dir: Path,
    retries: int,
    timeout: float,
) -> tuple[str, str, int]:
    ticker = row["ticker"]
    path = output_dir / f"{ticker}.json.gz"
    if path.exists() and path.stat().st_size > 1000:
        return ticker, "cached", path.stat().st_size
    event_ticker = event_from_ticker(ticker)
    url = f"{REST}/live_data/events/{event_ticker}"
    error: Optional[Exception] = None
    for attempt in range(retries):
        try:
            response = requests.get(url, timeout=timeout)
            if response.status_code == 429:
                time.sleep(min(8.0, 0.25 * 2**attempt) + random.random() * 0.2)
                continue
            response.raise_for_status()
            live = response.json()["live_data"]
            details = live["details"]
            timeseries = details.get("timeseries") or []
            if len(timeseries) < 900:
                raise RuntimeError(f"short timeseries: {len(timeseries)}")
            payload = {
                "ticker": ticker,
                "event_ticker": event_ticker,
                "close_ts": row.get("close_ts"),
                "result": row.get("result"),
                "source": url,
                "downloaded_at": time.time(),
                "live_data": live,
            }
            temp = path.with_suffix(path.suffix + ".partial")
            with gzip.open(temp, "wt", encoding="utf-8", compresslevel=6) as handle:
                json.dump(payload, handle, separators=(",", ":"))
            temp.replace(path)
            return ticker, "downloaded", path.stat().st_size
        except Exception as exc:
            error = exc
            time.sleep(min(8.0, 0.25 * 2**attempt) + random.random() * 0.2)
    return ticker, f"error:{type(error).__name__}:{error}", 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--assets", nargs="+", default=DEFAULT_ASSETS)
    p.add_argument("--tape-dir", type=Path, default=Path("../kalshi-scalp/data/rpl_tapes"))
    p.add_argument("--output-dir", type=Path, default=Path("data/cf_exact/history"))
    p.add_argument("--markets-per-asset", type=int, default=250)
    p.add_argument(
        "--skip-latest",
        type=int,
        default=0,
        help="skip this many latest eligible tape markets before taking the requested tail",
    )
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--retries", type=int, default=6)
    p.add_argument("--timeout", type=float, default=20)
    args = p.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    jobs: list[Dict[str, Any]] = []
    for asset in [a.upper() for a in args.assets]:
        path = args.tape_dir / f"KX{asset}15M.jsonl"
        rows = list(tape_rows(path))
        if args.skip_latest:
            rows = rows[: -args.skip_latest]
        if args.markets_per_asset:
            rows = rows[-args.markets_per_asset :]
        jobs.extend(rows)
        print(f"{asset}: queued {len(rows)} markets from {path}")

    counts: Dict[str, int] = {}
    bytes_written = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [
            pool.submit(fetch_one, row, args.output_dir, args.retries, args.timeout)
            for row in jobs
        ]
        for i, future in enumerate(concurrent.futures.as_completed(futures), 1):
            ticker, status, size = future.result()
            key = status.split(":", 1)[0]
            counts[key] = counts.get(key, 0) + 1
            bytes_written += size
            if status.startswith("error"):
                print(f"{ticker}: {status}")
            if i % 50 == 0 or i == len(futures):
                print(
                    f"{i}/{len(futures)} {counts} "
                    f"compressed={bytes_written / 1024 / 1024:.1f} MiB",
                    flush=True,
                )


if __name__ == "__main__":
    main()
