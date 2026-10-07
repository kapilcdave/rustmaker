#!/usr/bin/env python3
"""Paired cut-vs-hold on the same markets, which is the decision statistic exit_leftovers.py
does not print: the two arms are the SAME markets, so their separate SEs overstate the
uncertainty of the DIFFERENCE. Reuses exit_leftovers' parsing so the fee, the Eastern ticker
clock and the count_fp handling cannot drift between the two scripts.

Also splits per journal, because the 09-29 pooled study (347 mkts) found hold > cut on P&L while
the 10-05 run alone found cut > hold: insurance costs in good runs and pays in bad ones, so the
pooled mean is the only honest summary, and a per-run split shows the dispersion behind it.

Usage: python3 exit_leftovers_paired.py <live_*.jsonl.gz> [...]
"""
import json, sys
from collections import defaultdict
from pathlib import Path

import numpy as np

from exit_leftovers import CUT_S, at, close_us, fee_c, read


def one(path, res):
    """Per market: (hold, cut) cents. Mirrors exit_leftovers.main's inner loop exactly."""
    fills, touch = read(path)
    by = defaultdict(list)
    for f in sorted(fills):
        by[f[1]].append(f)
    rows = []
    for tk, fs in by.items():
        r = res.get(tk, {}).get("result")
        if r not in ("yes", "no"):
            continue
        y = 100.0 if r == "yes" else 0.0
        cut = close_us(tk) - CUT_S * 1_000_000
        pos = cash = pos_c = cash_c = 0.0
        done = False
        for vt, _, s, px, ct in fs:
            if vt > cut and not done:
                done = True
                pos_c, cash_c = pos, cash
                if pos:
                    b, a = at(touch, tk, cut)
                    xp = b if pos > 0 else a
                    xp = y if xp is None else xp
                    cash_c += pos * xp - abs(pos) * fee_c(xp)
                    pos_c = 0.0
            pos += s * ct
            cash -= s * ct * px
            if done:
                pos_c += s * ct
                cash_c -= s * ct * px
        if not done:
            pos_c, cash_c = pos, cash
            if pos:
                b, a = at(touch, tk, cut)
                xp = b if pos > 0 else a
                xp = y if xp is None else xp
                cash_c += pos * xp - abs(pos) * fee_c(xp)
                pos_c = 0.0
        rows.append((close_us(tk), tk, cash + pos * y, cash_c + pos_c * y))
    return rows


def stats(label, rows):
    if not rows:
        return
    h = np.array([r[2] for r in rows]); c = np.array([r[3] for r in rows]); d = c - h
    se = d.std(ddof=1) / np.sqrt(len(d)) if len(d) > 1 else float("nan")
    print(f"{label:<34} n={len(rows):4d}  hold ${h.sum()/100:+7.2f}  cut ${c.sum()/100:+7.2f}"
          f"  cut-hold {d.mean():+6.2f}±{se:5.2f} c/mkt  lo95 {d.mean()-1.96*se:+6.2f}")


def main(paths):
    res = json.load(open("data/leftover/results.json"))
    allrows = []
    for p in paths:
        rows = one(p, res)
        allrows += rows
        stats(Path(p).name.replace(".jsonl.gz", ""), rows)
    print()
    stats("POOLED", allrows)
    if not allrows:
        return
    # Round = markets closing together; this is the unit drawdown is actually felt in.
    for name, idx in (("hold", 2), ("cut", 3)):
        rnd = defaultdict(float)
        for r in allrows:
            rnd[r[0]] += r[idx]
        a = np.array([rnd[k] for k in sorted(rnd)])
        cum = np.cumsum(a)
        dd = np.max(np.maximum.accumulate(np.r_[0, cum])[1:] - cum)
        print(f"{name:<6} rounds={len(a):4d}  round sd {a.std(ddof=1):5.1f}c  worst {a.min():+6.0f}c  maxDD ${dd/100:.2f}")
    h = np.array([r[2] for r in allrows]); c = np.array([r[3] for r in allrows])
    print(f"\nminus best 10 markets: hold ${np.sort(h)[:-10].sum()/100:+.2f}  cut ${np.sort(c)[:-10].sum()/100:+.2f}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
