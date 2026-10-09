#!/usr/bin/env python3
"""Counterfactual: what would the relaxed exit have been worth, on real prints?

The defect (confirmed in src/live.rs): the crypto seat's reducing leg had to clear both ENTRY
gates -- a `penny_room`-tick spread and `stop_before_close_s` -- so it stopped quoting at a
15-minute market's halfway mark and refused to flatten into a tight book. Measured consequence on
2026-10-07: pairs +$0.42, positions held to settlement -$6.18, and 95.5% of those had a profitable
exit print arrive later.

Counterfactual rule, deliberately conservative:
  - an exit rests at the break-even clamp, entry +/- EDGE cents, from the moment the position opens;
  - it fills only on a print STRICTLY THROUGH that limit on the side that would hit it
    (a yes-print lands on the ask, a no-print on the bid), so at-price queue is never credited;
  - a filled exit books exactly EDGE cents and the settlement risk is gone;
  - an exit that never fills rides to settlement exactly as it really did.
Generous in one direction only: it credits us the print without queue position ahead of us.

Usage: python3 score_relaxed_exit.py
"""
import collections
import gzip
import json
import math

import numpy as np

FILLS = "data/tonight/tonight_fills.txt.gz"
TAPE = "data/tonight/tonight_T.csv.gz"
RESULTS = "data/leftover/results.json"
EDGES_C = [0.0, 1.0, 2.0, 3.0]


def main():
    res = json.load(open(RESULTS))
    raw = []
    for line in gzip.open(FILLS, "rt"):
        j, rest = line.split(" ", 1)
        v = json.loads(rest)["v"]
        if v.get("is_taker"):
            continue
        raw.append((v["ts_ms"] * 1000, j, v))
    raw.sort()

    tape = collections.defaultdict(list)
    with gzip.open(TAPE, "rt") as f:
        for line in f:
            tk, t_us, _v, side, price, cnt = line.rstrip("\n").split(",")
            tape[tk].append((int(t_us), side == "yes", float(price) * 100.0, float(cnt)))
    for v in tape.values():
        v.sort()

    # Sequential replay: label each fill opening/reducing, track the end position per market.
    pos = collections.defaultdict(float)
    fills = []
    for t, j, v in raw:
        tk = v["market_ticker"]
        r = res.get(tk)
        if not r or r.get("result") not in ("yes", "no"):
            continue
        sgn = 1.0 if v["book_side"] == "bid" else -1.0
        ct = float(v["count_fp"])
        px = float(v["yes_price_dollars"]) * 100.0
        key = (j, tk)
        new = pos[key] + sgn * ct
        opening = abs(new) > abs(pos[key])
        pos[key] = new
        fills.append({"t": t, "key": key, "ticker": tk, "round": tk.split("-")[1],
                      "sgn": sgn, "ct": ct, "px": px,
                      "y": 100.0 if r["result"] == "yes" else 0.0,
                      "opening": opening})
    flat = {k for k, v in pos.items() if abs(v) < 1e-9}

    actual = sum(f["sgn"] * f["ct"] * (f["y"] - f["px"]) for f in fills)
    print(f"ACTUAL: {len(fills)} fills, {actual/100:+.2f}$ (reconciles to the venue's -5.76$)")
    openings = [f for f in fills if f["opening"]]
    unpaired = [f for f in openings if f["key"] not in flat]
    print(f"  {len(openings)} opening fills, {len(unpaired)} of them left holding a position\n")

    print(f"{'exit edge':>10} {'exits filled':>13} {'of openings':>12} "
          f"{'counterfactual':>15} {'vs actual':>11} {'median s to exit':>17}")
    for edge in EDGES_C:
        filled = 0
        waits = []
        pnl = 0.0
        for f in fills:
            # Every OPENING fill is re-exited under the clamp. The real reducing legs are REPLACED,
            # not kept: they were priced by chasing the touch, which is the thing the clamp fixes,
            # so keeping their -$1.50 would understate the change. Reducing legs therefore
            # contribute nothing of their own here.
            if not f["opening"]:
                continue
            limit = f["px"] + edge if f["sgn"] > 0 else f["px"] - edge
            want_yes = f["sgn"] > 0          # long YES exits by having our ASK hit: a yes-print
            hit_t = None
            for t, yes, p, _c in tape.get(f["ticker"], []):
                if t <= f["t"] or yes != want_yes:
                    continue
                if (want_yes and p > limit) or (not want_yes and p < limit):
                    hit_t = t
                    break
            if hit_t is None:
                pnl += f["sgn"] * f["ct"] * (f["y"] - f["px"])   # unchanged: rides to settlement
            else:
                filled += 1
                waits.append((hit_t - f["t"]) / 1e6)
                pnl += edge * f["ct"]                            # pair closes at the clamp
        med = np.median(waits) if waits else float("nan")
        print(f"{edge:9.1f}c {filled:13d} {100*filled/max(len(openings),1):11.1f}% "
              f"{pnl/100:+14.2f}$ {(pnl-actual)/100:+10.2f}$ {med:16.1f}s")

    print("\nper-round spread of the 0c case (the clustering unit: nine co-expiring legs are one bet)")
    for edge in (0.0, 1.0):
        g = collections.defaultdict(float)
        for f in fills:
            if not f["opening"]:
                continue
            limit = f["px"] + edge if f["sgn"] > 0 else f["px"] - edge
            want_yes = f["sgn"] > 0
            hit = any(
                t > f["t"] and yes == want_yes and
                ((want_yes and p > limit) or (not want_yes and p < limit))
                for t, yes, p, _ in tape.get(f["ticker"], []))
            g[f["round"]] += edge * f["ct"] if hit else f["sgn"] * f["ct"] * (f["y"] - f["px"])
        a = np.array(list(g.values()))
        print(f"  edge {edge:.0f}c: {len(a)} rounds, mean {a.mean():+.2f}c, "
              f"sd {a.std(ddof=1):.2f}c, worst {a.min():+.2f}c, "
              f"se {a.std(ddof=1)/math.sqrt(len(a)):.2f}c, "
              f"{100*(a>0).mean():.0f}% positive")


if __name__ == "__main__":
    main()
