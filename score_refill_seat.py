#!/usr/bin/env python3
"""Price the REFILL seat on real prints: if we replenished our quote at the same price after being
hit, instead of holding/capping out, what would those contracts have settled at?

This is the seat the ~7.5 ms makers appear to occupy (79% of sub-1ms touch adds are hit-side
refill). It is unreachable at --max-pos 1 because our own position cap blocks it, so the question
is what removing the cap buys.

Method. For each of our real maker fills at price p on side s, every later print at EXACTLY p on
the side that would have hit a resting order of ours is a refill we could have taken. Credit
min(clip, print size) contracts and score to settlement: pnl/ct = sgn * (y - p).

Side convention (kalshi-taker-side-convention-confirmed): a yes-print lands on the ASK, a no-print
on the BID. So a fill on our bid is refilled by later no-prints at the same price.

This is an UPPER bound: it credits us ahead of everyone already queued at that price, ignores that
our reappearance changes the taker's behaviour, and ignores the fee (zero for 15M crypto makers).
A negative upper bound is therefore decisive; a positive one is not.

Usage: python3 score_refill_seat.py [journal.jsonl.gz ...]
"""
import bisect
import gzip
import json
import math
import sys
from collections import defaultdict

import numpy as np

RESULTS = "data/leftover/results.json"
WINDOWS_US = [1_500_000, 10_000_000, 60_000_000]   # 1.5 s (the old freeze), 10 s, 60 s
CLIP = 1.0


def px(s):
    return int(round(float(s) * 1000))


def run(paths):
    res = json.load(open(RESULTS))
    fills = []
    prints = defaultdict(list)
    for path in paths:
        with gzip.open(path, "rt") as f:
            for line in f:
                if not line.startswith('{"k":"'):
                    continue
                k = line[6:line.index('"', 6)]
                if k == "fill":
                    d = json.loads(line)["v"]
                    if d.get("is_taker"):
                        continue
                    fills.append((json.loads(line)["t"], d["market_ticker"],
                                  px(d["yes_price_dollars"]), d["book_side"]))
                elif k == "T":
                    v = json.loads(line)
                    tk, _, side, price, cnt = v["v"]
                    prints[tk].append((v["t"], px(price), side == "yes", float(cnt)))
    for v in prints.values():
        v.sort()
    idx = {tk: [r[0] for r in v] for tk, v in prints.items()}
    print(f"{len(fills)} real maker fills, {sum(len(v) for v in prints.values())} prints on the tape")

    out = {}
    for w in WINDOWS_US:
        rows = []
        for t, tk, p, bside in fills:
            r = res.get(tk)
            if not r or r.get("result") not in ("yes", "no"):
                continue
            y = 1000 * 100 if r["result"] == "yes" else 0      # tenths of a cent, x100 -> same units
            y = 100.0 if r["result"] == "yes" else 0.0
            sgn = 1.0 if bside == "bid" else -1.0
            want_yes = bside == "ask"                           # our ask is hit by yes-prints
            v, ix = prints.get(tk), idx.get(tk)
            if not v:
                continue
            i = bisect.bisect_right(ix, t)
            rnd = tk.split("-")[1] if "-" in tk else tk
            while i < len(v) and v[i][0] <= t + w:
                pt, pp, pyes, pct = v[i]
                if pp == p and pyes == want_yes:
                    ct = min(CLIP, pct)
                    rows.append((sgn * ct * (y - p / 1000.0), ct, rnd))
                i += 1
        if not rows:
            print(f"\nwindow {w/1e6:>4.1f}s: no refill opportunities")
            continue
        pnl = np.array([r[0] for r in rows])
        ct = np.array([r[1] for r in rows])
        g = defaultdict(float)
        for a, _, rr in rows:
            g[rr] += a
        m = len(g)
        se_c = math.sqrt(np.array(list(g.values())).var(ddof=1) * m) / ct.sum() if m > 1 else float("nan")
        cpc = pnl.sum() / ct.sum()
        half = len(pnl) // 2
        print(f"\nwindow {w/1e6:>4.1f}s: {len(rows)} refills, {ct.sum():.0f} contracts, "
              f"{pnl.sum()/100:+.2f}$")
        print(f"   c/ct {cpc:+.3f}   round-clustered se {se_c:.3f}   lo95 {cpc-1.96*se_c:+.3f}   "
              f"H1 {pnl[:half].sum()/max(ct[:half].sum(),1):+.3f} / H2 {pnl[half:].sum()/max(ct[half:].sum(),1):+.3f}")
        print(f"   refills per real fill: {ct.sum()/len(fills):.2f}")
        out[w] = cpc
    return out


if __name__ == "__main__":
    run(sys.argv[1:] or ["data/box/live_penny_capped/live_1790635109218.jsonl.gz"])
