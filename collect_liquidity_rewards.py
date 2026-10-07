"""Capture public books for active Kalshi liquidity-incentive markets. No auth/orders."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import gzip
import json
import time
import urllib.error
import urllib.request
from pathlib import Path


BASE = "https://external-api.kalshi.com/trade-api/v2"


def get_json(path: str, timeout: float = 8.0) -> dict:
    request = urllib.request.Request(BASE + path, headers={"User-Agent": "kalshi-reward-probe/1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def parse_time(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def active_programs(prefix: str) -> list[dict]:
    now = dt.datetime.now(dt.timezone.utc)
    rows = get_json("/incentive_programs?limit=1000").get("incentive_programs", [])
    return [
        row for row in rows
        if row.get("market_ticker", "").startswith(prefix)
        and parse_time(row["start_date"]) <= now < parse_time(row["end_date"])
    ]


def fetch_book(program: dict) -> dict:
    ticker = program["market_ticker"]
    try:
        payload = get_json(f"/markets/{ticker}/orderbook?depth=100")
        return {"kind": "book", "recv_ns": time.time_ns(), "program": program, "payload": payload}
    except Exception as error:  # noqa: BLE001
        return {"kind": "error", "recv_ns": time.time_ns(), "ticker": ticker, "error": repr(error)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--duration", type=float, default=10_800.0)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--prefix", default="KXCRYPTOLEAD15M-")
    args = parser.parse_args()

    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + args.duration
    next_refresh = 0.0
    programs: list[dict] = []
    snapshots = errors = 0
    with gzip.open(output, "wt", compresslevel=3) as stream, concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        stream.write(json.dumps({"kind": "start", "recv_ns": time.time_ns(), "args": vars(args)}) + "\n")
        while time.monotonic() < deadline:
            started = time.monotonic()
            if started >= next_refresh:
                try:
                    programs = active_programs(args.prefix)
                    stream.write(json.dumps({
                        "kind": "programs", "recv_ns": time.time_ns(), "programs": programs
                    }, separators=(",", ":")) + "\n")
                except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
                    stream.write(json.dumps({"kind": "program_error", "recv_ns": time.time_ns(), "error": repr(error)}) + "\n")
                    errors += 1
                next_refresh = started + 30.0
            for row in pool.map(fetch_book, programs):
                stream.write(json.dumps(row, separators=(",", ":")) + "\n")
                snapshots += row["kind"] == "book"
                errors += row["kind"] == "error"
            stream.flush()
            elapsed = time.monotonic() - started
            if elapsed < args.interval:
                time.sleep(args.interval - elapsed)
        stream.write(json.dumps({"kind": "final", "recv_ns": time.time_ns(), "snapshots": snapshots, "errors": errors}) + "\n")
    print(json.dumps({"out": str(output), "snapshots": snapshots, "errors": errors}))


if __name__ == "__main__":
    main()
