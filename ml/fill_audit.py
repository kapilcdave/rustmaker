"""Audit the improved-quote fill model against the price the print actually traded at.

`features.market_rows` accepts a fill whenever the quote was `live` and the book had `room`:

    m = live & room

It never compares the PRINT PRICE to our own quote price. The implicit argument is that an
improved quote is the best offer, so any taker on that side must hit it. That argument needs the
book snapshot to be true at the print instant: a yes-print at price P means the ask WAS P
(taker_side is confirmed on this venue -- yes-prints hit the ask), so if P is worse for us than
the quote we are supposed to have posted, the real touch had already moved and our order was
behind the market, not at the front of it.

So each assumed fill gets one check, per side:

    yes-print (we sold yes at px_c):  real fill requires P >= px_c
    no-print  (we bought yes at px_c): real fill requires P <= px_c

and the pass rate is broken down BY QUOTED SPREAD, because a width gradient in the pass rate is a
mechanical explanation for the width gradient in the P&L.

    python3 ml/fill_audit.py data/box/tape_1790136632445.csv.gz data/box/shadow_spot/tape.csv.gz
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ml"))
from features import LAG_US, tick_fp  # noqa: E402
from tox import close_utc, load  # noqa: E402

rows = []
for path in sys.argv[1:]:
    b, t = load(path)
    for tk_ in sorted(set(b.ticker.unique()) & set(t.ticker.unique())):
        bb = b[b.ticker == tk_].sort_values("vt")
        bb = bb[(bb.bid > 0) & (bb.ask > 0)]
        tt = t[t.ticker == tk_]
        if len(bb) < 20 or len(tt) == 0:
            continue
        close_us = close_utc(tk_)
        bvt = bb.vt.to_numpy()
        bid, ask = bb.bid.to_numpy(), bb.ask.to_numpy()
        for side, g in tt.sort_values("vt").groupby("side"):
            v = g.vt.to_numpy()
            pr = g.price.to_numpy() * 100.0          # cents -> 1e-4 dollars, same units as bid/ask
            i = np.searchsorted(bvt, v - LAG_US, side="right") - 1
            j = np.searchsorted(bvt, v, side="right") - 1
            live = (i >= 0) & (i == j)
            i = np.clip(i, 0, len(bvt) - 1)
            a_, b_ = ask[i], bid[i]
            tkk = tick_fp(np.where(side == "yes", a_, b_))
            room = (a_ - b_) >= 2 * tkk
            m = live & room & (v < close_us - 120_000_000)
            if not m.any():
                continue
            px = np.where(side == "yes", a_ - tkk, b_ + tkk)[m]
            P = pr[m]
            # does the print price reach the quote we are assumed to have posted?
            reach = P >= px if side == "yes" else P <= px
            rows.append(pd.DataFrame({
                "ticker": tk_, "side": side,
                "spread_ticks": ((a_ - b_) / tkk)[m],
                "px_c": px / 100.0, "print_c": P / 100.0,
                "gap_c": np.where(side == "yes", (P - px), (px - P)) / 100.0,
                "reach": reach,
            }))
d = pd.concat(rows, ignore_index=True)
print(f"{len(d):,} assumed improved-quote fills over {d.ticker.nunique()} markets\n")
print(f"print price reaches our quote: {d.reach.mean():.4f}  "
      f"({int(d.reach.sum()):,} of {len(d):,})")
print(f"by side: {d.groupby('side').reach.mean().round(4).to_dict()}\n")
d["bucket"] = pd.cut(d.spread_ticks, [1, 2, 3, 4, 5, 7, 10, 20, 10000],
                     labels=["2", "3", "4", "5", "6-7", "8-10", "11-20", "21+"])
g = d.groupby("bucket", observed=True)
print("pass rate by quoted spread -- a gradient here mechanically explains the P&L gradient")
print(pd.DataFrame({"fills": g.size(), "reach_rate": g.reach.mean().round(4),
                    "median_gap_c": g.gap_c.median().round(2),
                    "mean_gap_c": g.gap_c.mean().round(2)}).to_string())
