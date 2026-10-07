"""Taker latency arb on a shadow tape: after a Coinbase move, is the stale Kalshi quote still up
when OUR taker order would land, and does taking it pay after Kalshi's taker fee?

Clock: every time here is OUR receipt clock (recv_us), so no venue clock agreement is needed.
A Coinbase tick received at t; our order reaches the Kalshi book at t + CREATE_US. We see a Kalshi
book change FEED_US after it happens. So the stale quote was still there when we landed iff the
first change we SEE is later than t + CREATE_US + FEED_US.
Value: Kalshi mid H seconds after the event, minus the price paid, minus the taker fee.
Usage: python3 latency_arb.py tape.csv.gz [bps]
"""
import math, sys
import numpy as np
import pandas as pd

CREATE_US, FEED_US = 5_440, 6_000  # measured 2026-09-23 (order->book) / feed one-way p50
TH = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0
df = pd.read_csv(sys.argv[1], usecols=range(8), header=0, dtype={"a": str, "b": str}, low_memory=False)
x = df[df.kind == "X"].copy(); x["mid"] = pd.to_numeric(x.a)
b = df[df.kind == "B"].copy()
for c in "abcd":
    b[c] = pd.to_numeric(b[c])
b = b[(b.a > 0) & (b.c > 0)]


def fee_c(p):  # Kalshi taker fee on one contract, cents, rounded up
    return math.ceil(7 * p * (1 - p) * 100 - 1e-9) / 100 * 100 / 100 if False else math.ceil(0.07 * p * (1 - p) * 100 - 1e-9)


rows = []
for series, sx in x.groupby("ticker"):
    sx = sx.sort_values("recv_us"); t = sx.recv_us.to_numpy(); m = sx.mid.to_numpy()
    bb = b[b.ticker.str.startswith(series + "-")].sort_values("recv_us")
    if bb.empty:
        continue
    bt = bb.recv_us.to_numpy(); tick = bb.ticker.to_numpy()
    bid, ask = bb.a.to_numpy(), bb.c.to_numpy(); bsz, asz = bb.b.to_numpy(), bb.d.to_numpy()
    last_ev = -10**18
    for k in range(len(t)):
        j0 = np.searchsorted(t, t[k] - 1_000_000, side="right") - 1
        if j0 < 0 or t[k] - last_ev < 2_000_000:
            continue
        r = (m[k] / m[j0] - 1) * 1e4
        if abs(r) < TH:
            continue
        last_ev = t[k]
        i = np.searchsorted(bt, t[k], side="right") - 1
        if i < 0:
            continue
        tk = tick[i]
        up = r > 0
        px0, sz0 = (ask[i], asz[i]) if up else (bid[i], bsz[i])
        # First book change on the side we would take, in the SAME market.
        j = i + 1
        while j < len(bt) and (tick[j] != tk or (ask[j] if up else bid[j]) == px0):
            j += 1
        if j >= len(bt):
            continue
        lead = bt[j] - t[k]
        # Kalshi mid 10 s and 60 s later in that market.
        def mid_at(tt):
            q = np.searchsorted(bt, tt, side="right") - 1
            while q >= 0 and tick[q] != tk:
                q -= 1
            return (bid[q] + ask[q]) / 200 if q >= 0 else np.nan
        p = px0 / 10_000  # dollars
        val10, val60 = mid_at(t[k] + 10_000_000), mid_at(t[k] + 60_000_000)
        pay = p * 100
        gain10 = (val10 - pay) if up else (pay - val10)
        gain60 = (val60 - pay) if up else (pay - val60)
        f = fee_c(p)
        rows.append(dict(series=series, r=r, lead_ms=lead / 1000, px_c=pay, size=sz0 / 100, fee_c=f,
                         g10=gain10 - f, g60=gain60 - f, win=lead > CREATE_US + FEED_US))
e = pd.DataFrame(rows)
print(f"spot moves >= {TH} bps / 1 s: {len(e)} events over {e.series.nunique()} series")
print("time until the threatened Kalshi quote changes (ms, our clock): "
      + "  ".join(f"p{q}={np.percentile(e.lead_ms, q):.1f}" for q in (10, 25, 50, 75, 90)))
w = e[e.win]
print(f"quote still up when our order lands (lead > {(CREATE_US + FEED_US) / 1000:.1f} ms): {len(w)} of {len(e)} ({100 * len(w) / len(e):.0f}%)")
for name, g in [("ALL events", e), ("WON the race", w)]:
    if len(g):
        print(f"  {name:13s} n={len(g):5d}  take 1 ct after fee: +10s {g.g10.mean():+.2f} ± {g.g10.std() / np.sqrt(len(g)):.2f} c   "
              f"+60s {g.g60.mean():+.2f} ± {g.g60.std() / np.sqrt(len(g)):.2f} c   fee {g.fee_c.mean():.2f} c   size at px {g['size'].median():.0f} ct")
print("\nWON the race, by series (c/ct after fee, +60 s):")
print(w.groupby("series").agg(n=("g60", "size"), g60=("g60", "mean"), g10=("g10", "mean"), size=("size", "median")).round(2).to_string())
