"""How much of a bigger penny clip would the takers who hit us actually have filled?

For every real maker fill in the capped live journals, find the taker order that hit it (public T
rows in the same ticker, same venue millisecond, same taker side) and sum its size q. At clip c the
same taker would have filled min(c, q) of our quote, at our price, since the penny is the best level.
Held to settlement, a fill's P&L is s * (settle - price) exactly (pairs net out), so the clip-c
counterfactual is sum min(c, q) * edge_per_contract, market-clustered.

Upward-biased: ignores takers shrinking against a visible bigger quote, rivals re-pennying, and the
position caps (max-pos, round-net) that a bigger clip would hit sooner. Downward: ignores fills that
max-pos 1 blocked. Usage: python3 taker_size_capacity.py journal.jsonl.gz [...]
"""
import collections
import gzip
import json
import math
import os
import sys
import urllib.request

CLIPS = [1, 2, 3, 5, 10, 20, 50]
CACHE = os.path.join(os.path.dirname(__file__), "data", "settle_cache.json")


def load(path):
    takers = collections.defaultdict(float)  # (ticker, ms, taker_side) -> contracts
    fills = []
    with gzip.open(path, "rt") as f:
      try:
        for line in f:
            if line.startswith('{"k":"T"'):
                tk, ts_us, side, _px, cnt = json.loads(line)["v"]
                takers[(tk, ts_us // 1000, side)] += float(cnt)
            elif line.startswith('{"k":"fill"'):
                v = json.loads(line)["v"]
                if v.get("is_taker"):
                    continue
                fills.append(v)
      except EOFError:  # journal pulled while still being written
        print(f"{path}: truncated, using rows read so far", file=sys.stderr)
    return takers, fills


def settle_results(tickers):
    cache = {}
    if os.path.exists(CACHE):
        cache = json.load(open(CACHE))
    for tk in sorted(set(tickers) - set(cache)):
        url = f"https://api.elections.kalshi.com/trade-api/v2/markets/{tk}"
        try:
            m = json.load(urllib.request.urlopen(url, timeout=10))["market"]
            cache[tk] = m.get("result") or ""
        except Exception as e:  # leave unresolved, don't cache a failure
            print("settle lookup failed", tk, e, file=sys.stderr)
    json.dump(cache, open(CACHE, "w"))
    return cache


def main(paths):
    rows = []  # (ticker, q_taker, our_count, edge_c_per_ct)
    unmatched = 0
    for p in paths:
        takers, fills = load(p)
        for v in fills:
            tk, ms = v["market_ticker"], v["ts_ms"]
            # we bought yes -> taker sold yes (T side "no"); we sold yes -> taker side "yes"
            tside = "no" if v["action"] == "buy" else "yes"
            q = sum(takers.get((tk, ms + d, tside), 0.0) for d in (-1, 0, 1))
            if q <= 0:
                unmatched += 1
                continue
            rows.append((tk, q, float(v["count_fp"]), v["action"], float(v["yes_price_dollars"]) * 100))
        print(f"{p}: {len(fills)} maker fills", file=sys.stderr)
    res = settle_results([r[0] for r in rows])
    out = []
    for tk, q, n, act, px in rows:
        r = res.get(tk)
        if r not in ("yes", "no"):
            continue
        y = 100.0 if r == "yes" else 0.0
        edge = (y - px) if act == "buy" else (px - y)
        out.append((tk, q, n, edge))
    print(f"matched {len(rows)}, unmatched {unmatched}, settled {len(out)} fills in "
          f"{len({o[0] for o in out})} markets")

    qs = sorted(o[1] for o in out)
    pct = lambda a: qs[min(len(qs) - 1, int(a * len(qs)))]
    print(f"\ntaker size q hitting our penny: p25 {pct(.25):.2f} p50 {pct(.5):.2f} p75 {pct(.75):.2f} "
          f"p90 {pct(.9):.2f} p99 {pct(.99):.2f} max {qs[-1]:.0f}")
    for lo, hi in [(0, 1.01), (1.01, 3), (3, 10), (10, 30), (30, 1e9)]:
        b = [o for o in out if lo <= o[1] < hi]
        if not b:
            continue
        mk = collections.defaultdict(float)
        for o in b:
            mk[o[0]] += o[3]
        e = sum(o[3] for o in b) / len(b)
        print(f"  q in [{lo:>5},{hi if hi < 1e9 else 'inf':>5}): {len(b):5d} fills ({len(b)/len(out):5.1%}), "
              f"settle edge {e:+6.2f} c/ct")

    # clip counterfactual, clustered by market
    print("\nclip   contracts   c/ct     $ total   c/mkt  ± se      x clip1 $")
    base = None
    nm = len({o[0] for o in out})
    for c in CLIPS:
        mk = collections.defaultdict(float)
        cts = 0.0
        for tk, q, _n, e in out:
            f = min(c, q)
            cts += f
            mk[tk] += f * e
        vals = list(mk.values())
        tot = sum(vals)
        mean = tot / nm
        sd = math.sqrt(sum((x - mean) ** 2 for x in vals) / (nm - 1))
        base = base or tot
        print(f"{c:4d} {cts:10.0f} {tot/cts:+7.2f} {tot/100:+10.2f} {mean:+7.2f} ± {sd/math.sqrt(nm):5.2f} "
              f"{tot/base:9.2f}")


if __name__ == "__main__":
    main(sys.argv[1:])
