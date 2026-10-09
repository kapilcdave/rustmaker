#!/usr/bin/env python3
"""Does our own LATENCY DISTRIBUTION predict fill quality? The operator's hypothesis.

Our reaction is not a constant 8.5 ms: feed_age (venue stamp -> our receipt) runs p1 3.6 ms /
p50 5.7 / p99 10.1. On the fast tail we are plausibly inside the ~7.5 ms competitor figure, and on
the slow tail we are badly late. Crucially we KNOW which we are in at decision time -- feed_age is
observable per message, before we act. So it can be a gate.

Test: for every order we actually sent, take the feed_age of the book message that triggered it
(the most recent B row for that ticker before our send), then score the fills that order produced
to settlement. If quotes placed on fresh information settle better, a freshness gate has content
and is implementable as `--max-feed-age-us`.

B row = [ticker, venue_ts_us, bid, bid_sz, ask, ask_sz]; the row's own `t` is OUR receipt (us).
Per-fill settlement P&L is exactly additive: sgn * ct * (y - px).

Usage: python3 score_freshness_gate.py <journal.jsonl.gz> [...]
"""
import gzip
import json
import math
import sys
from collections import defaultdict

import numpy as np

RESULTS = "data/leftover/results.json"


def load(paths, res):
    rows = []
    for path in paths:
        latest = {}          # ticker -> feed_age_us of the most recent book message
        order = {}           # coid -> (feed_age, ticker)
        # A journal can be truncated: the supervisor gzips a window as it ends, and one run died
        # mid-write on a full disk. Take the rows up to the break rather than losing the file.
        try:
            stream = gzip.open(path, "rt")
            for line in stream:
                    if not line.startswith('{"k":"'):
                        continue
                    k = line[6:line.index('"', 6)]
                    if k == "B":
                        v = json.loads(line)
                        latest[v["v"][0]] = v["t"] - v["v"][1]
                    elif k in ("new", "amend"):
                        v = json.loads(line)
                        d = v["v"]
                        b = d["body"]
                        cid = d["coid"] if k == "amend" else b["client_order_id"]
                        fa = latest.get(b["ticker"])
                        if fa is not None:
                            order[cid] = (fa, b["ticker"])
                    elif k == "fill":
                        d = json.loads(line)["v"]
                        if d.get("is_taker"):
                            continue
                        o = order.get(d.get("client_order_id"))
                        if o is None:
                            continue
                        tk = d["market_ticker"]
                        r = res.get(tk)
                        if not r or r.get("result") not in ("yes", "no"):
                            continue
                        px = float(d["yes_price_dollars"]) * 100.0
                        ct = float(d["count_fp"])
                        sgn = 1.0 if d["book_side"] == "bid" else -1.0
                        y = 100.0 if r["result"] == "yes" else 0.0
                        rows.append({"fa": o[0], "pnl": sgn * ct * (y - px), "ct": ct,
                                     "round": tk.split("-")[1] if "-" in tk else tk,
                                     "px": px})
        except (EOFError, OSError, json.JSONDecodeError) as e:
            print(f"    TRUNCATED {path.split('/')[-1]}: {type(e).__name__}, using rows up to the break")
        print(f"  {path.split('/')[-1]}: {len(order)} orders linked, {len(rows)} fills so far")
    return rows


def stat(rows, label):
    if not rows:
        print(f"  {label:26} -")
        return
    pnl = np.array([r["pnl"] for r in rows])
    ct = np.array([r["ct"] for r in rows])
    g = defaultdict(float)
    for r in rows:
        g[r["round"]] += r["pnl"]
    m = len(g)
    se = (math.sqrt(np.array(list(g.values())).var(ddof=1) * m) / ct.sum()) if m > 1 else float("nan")
    cpc = pnl.sum() / ct.sum()
    h = len(rows) // 2
    fa = np.array([r["fa"] for r in rows]) / 1000.0
    print(f"  {label:26} n={len(rows):5d} ct={ct.sum():7.0f} feed_age p50={np.median(fa):6.2f}ms "
          f"tot={pnl.sum()/100:+8.2f}$ c/ct={cpc:+7.3f} se={se:6.3f} lo95={cpc-1.96*se:+7.3f} "
          f"H1={pnl[:h].sum()/max(ct[:h].sum(),1):+7.3f} H2={pnl[h:].sum()/max(ct[h:].sum(),1):+7.3f}")


def main(paths):
    res = json.load(open(RESULTS))
    rows = load(paths, res)
    if not rows:
        print("no linked fills")
        return
    fa = np.array([r["fa"] for r in rows])
    print(f"\nlinked fills: {len(rows)}   feed_age at decision time, ms: "
          f"p1 {np.percentile(fa,1)/1000:.2f}  p25 {np.percentile(fa,25)/1000:.2f}  "
          f"p50 {np.percentile(fa,50)/1000:.2f}  p75 {np.percentile(fa,75)/1000:.2f}  "
          f"p99 {np.percentile(fa,99)/1000:.2f}")

    print("\n" + "=" * 118)
    print("SETTLEMENT P&L BY THE FRESHNESS OF THE INFORMATION THE QUOTE WAS PLACED ON")
    print("=" * 118)
    qs = [0, 25, 50, 75, 100]
    edges = [np.percentile(fa, q) for q in qs]
    for i in range(4):
        lo, hi = edges[i], edges[i + 1]
        sel = [r for r in rows if (lo <= r["fa"] < hi) or (i == 3 and r["fa"] == hi)]
        stat(sel, f"Q{i+1} {lo/1000:5.2f}-{hi/1000:5.2f} ms")

    print("\n  absolute thresholds (what a --max-feed-age-us gate would admit):")
    for thr_ms in (4, 5, 6, 7, 8):
        sel = [r for r in rows if r["fa"] < thr_ms * 1000]
        stat(sel, f"feed_age < {thr_ms} ms")
    print("\n  and what it would REJECT:")
    for thr_ms in (6, 7):
        sel = [r for r in rows if r["fa"] >= thr_ms * 1000]
        stat(sel, f"feed_age >= {thr_ms} ms")


if __name__ == "__main__":
    main(sys.argv[1:] or ["data/box/live_penny_capped/live_1790635109218.jsonl.gz"])
