#!/usr/bin/env python3
"""Add settlement results for a journal's tickers to data/leftover/results.json.

exit_leftovers.py needs {ticker: {result, close_time, status}} and skips any market without a
FINALIZED result. GET /markets/{ticker} is public, and it is the per-market endpoint on purpose:
the list endpoint caches for 30 s (kalshi-list-endpoint-caches-quotes-30s). Only finalized rows are
written, so a market still settling is left absent rather than cached as a non-result.

Usage: python3 fetch_results.py <live_*.jsonl.gz> [...]
"""
import gzip, json, sys, time, urllib.request
from pathlib import Path

HOST = "https://api.elections.kalshi.com/trade-api/v2"
CACHE = Path("data/leftover/results.json")


def tickers(paths):
    """Only markets with FILLS matter: exit_leftovers buckets by the fill rows' ticker.

    The live penny journal is dict-keyed ({"k":"fill","v":{...}}), not the positional
    [tag, ts_ms, payload] window-journal shape -- read it the same way the study does.
    """
    seen = set()
    for p in paths:
        with gzip.open(p, "rt") as f:
            try:
                for line in f:
                    if line.startswith('{"k":"fill"'):
                        t = json.loads(line)["v"].get("market_ticker")
                        if t:
                            seen.add(t)
            except (EOFError, json.JSONDecodeError):
                pass  # a journal still being written
    return seen


def fetch(ticker):
    req = urllib.request.Request(f"{HOST}/markets/{ticker}", headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)["market"]


def main(paths):
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    want = sorted(t for t in tickers(paths) if t not in cache)
    print(f"{len(want)} tickers to fetch ({len(cache)} already cached)", flush=True)
    added = skipped = failed = 0
    for i, t in enumerate(want, 1):
        try:
            m = fetch(t)
        except Exception as e:
            failed += 1
            print(f"  {t}: {type(e).__name__} {e}", flush=True)
            continue
        status, result = m.get("status"), m.get("result")
        # Absent/empty result means not settled yet -- never cache that as an answer.
        if status == "finalized" and result in ("yes", "no"):
            cache[t] = {"result": result, "close_time": m.get("close_time"), "status": status}
            added += 1
        else:
            skipped += 1
        if i % 50 == 0:
            print(f"  {i}/{len(want)} added={added} skipped={skipped} failed={failed}", flush=True)
        time.sleep(0.05)
    CACHE.write_text(json.dumps(cache))
    print(f"added {added}, not-finalized {skipped}, failed {failed}; cache now {len(cache)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
