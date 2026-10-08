#!/usr/bin/env python3
"""At $10.52, is there a VIABLE scaled-down penny seat, or only a crippled one?

`ruin.py` sized the cap for the 9-series seat and concluded the cap was destroying the sample.
This asks the question that a small bankroll actually poses: collateral is 100c x series x clip, so
**every series you drop converts 100c of locked collateral into drawdown budget**. Since
co-expiring markets share one price path, dropping series also shrinks the round's own sd. Those
two effects push in opposite directions on survival, and the corpus has never measured which wins.

For each subset size k, subsets are SAMPLED, not chosen by their P&L: picking the historically best
series and then reporting its survival is the selection error `in-sample-controls-cannot-detect-
selection` names. What is reported per k is the median over subsets, so the answer is about the
STRUCTURE (collateral vs cap) rather than about which ticker got lucky.

    .venv/bin/python scale_down.py [bankroll_cents]
"""
from __future__ import annotations

import json
import sys
from itertools import combinations

import numpy as np

from ruin import (EXTRA_RESULTS, HIST, RESULTS, TODAY, load_fills, round_series,
                  survive)

ROUNDS_PER_HOUR = 4
MIN_HEAD_C = 250        # the supervisor's loop guard: 3 round-sd at the 9-series sd
COLLAT_PER_SERIES = 100  # 100c x series x clip, clip 1
N_SIMS = 4000
RNG = np.random.default_rng(0)

# ⚑ The journal history spans 20 series -- commodities (COPPER/GOLD/WTI/SILVER/PLATINUM/PALLADIUM/
# NATGAS), sports (NCAAF2H/MLS2HTOTAL) and CRYPTOLEAD -- from configs this seat no longer runs, and
# the commodity 15M family is a CLOSED branch. Mixing them into the subsets would price a seat
# nobody would arm. Restrict to the 9 crypto series penny_supervisor.sh actually quotes.
LIVE = ["KXBNB15M", "KXBTC15M", "KXDOGE15M", "KXETH15M", "KXHYPE15M",
        "KXNEAR15M", "KXSOL15M", "KXXRP15M", "KXZEC15M"]


def main() -> None:
    bankroll = int(sys.argv[1]) if len(sys.argv) > 1 else 1052

    res = json.load(open(RESULTS))
    try:
        for tk, r in json.load(open(EXTRA_RESULTS)).items():
            if tk not in res and r in ("yes", "no"):
                res[tk] = {"result": r}
    except FileNotFoundError:
        pass

    rows = []
    for path in (HIST, TODAY):
        try:
            rows += load_fills(path, res)[0]
        except FileNotFoundError:
            print(f"  (missing {path})")
    for r in rows:
        r["series"] = r["ticker"].split("-")[0]
    present = {r["series"] for r in rows}
    series = [s for s in LIVE if s in present]
    rows = [r for r in rows if r["series"] in series]
    ts = np.array([r["ts"] for r in rows], dtype=float)
    span_h = (ts.max() - ts.min()) / 1000.0 / 3600.0

    print(f"bankroll {bankroll}c   span {span_h:.0f} h   series {len(series)}: "
          + " ".join(s.replace('KX', '').replace('15M', '') for s in series))

    full_pnl, full_mk, _ = round_series(rows)
    print(f"9-series round distribution: mean {full_pnl.mean():+.2f}c  sd {full_pnl.std(ddof=1):.2f}c"
          f"  rounds {len(full_pnl)}  markets/round {full_mk.mean():.1f}\n")

    by_series = {s: [r for r in rows if r["series"] == s] for s in series}

    print("=" * 108)
    print(f"{'k':>2} {'collat':>7} {'cap':>6} {'cap/sd':>7} {'mean':>7} {'sd':>7} "
          f"{'rnd/day':>8} {'P(week)':>8} {'rounds→t2':>10} {'days→t2 [IQR]':>21} "
          f"{'arm':>6} {'nsub':>4}")
    print("=" * 108)

    out = []
    for k in range(1, len(series) + 1):
        subs = list(combinations(series, k))   # ALL of them: no subset sampling noise
        collat = COLLAT_PER_SERIES * k
        cap = bankroll - collat
        head = bankroll - collat
        armable = cap > 0 and head >= MIN_HEAD_C

        recs = []
        for s in subs:
            sub = [r for ss in s for r in by_series[ss]]
            if not sub:
                continue
            pnl, mk, _ = round_series(sub)
            if len(pnl) < 30 or pnl.std(ddof=1) <= 0:
                continue
            mean, sd = pnl.mean(), pnl.std(ddof=1)
            rnd_per_day = len(pnl) / (span_h / 24.0)
            week = max(int(round(rnd_per_day * 7)), 1)
            p_week = (survive(pnl, cap, bankroll, collat, week, N_SIMS, RNG)[0]
                      if armable else 0.0)
            r_t2 = (2.0 * sd / mean) ** 2 if mean > 0 else float("inf")
            recs.append((mean, sd, rnd_per_day, p_week, r_t2,
                         r_t2 / rnd_per_day if rnd_per_day > 0 else float("inf")))
        if not recs:
            continue
        a = np.array(recs, dtype=float)
        med = np.median(a, axis=0)
        sd9 = full_pnl.std(ddof=1)
        q1, q3 = np.percentile(a, 25, axis=0), np.percentile(a, 75, axis=0)
        d_lo, d_hi = q1[5], q3[5]
        print(f"{k:>2} {collat:>7} {cap:>6} {cap / med[1]:>7.1f} {med[0]:>+7.2f} {med[1]:>7.2f} "
              f"{med[2]:>8.1f} {med[3]:>7.1%} {med[4]:>10.0f} "
              f"{med[5]:>6.0f} [{d_lo:>5.0f},{d_hi:>6.0f}] {'yes' if armable else 'NO':>6} "
              f"{len(recs):>4}")
        out.append({"k": k, "collateral_c": collat, "cap_c": cap, "armable": bool(armable),
                    "mean_c": med[0], "sd_c": med[1], "cap_over_sd": cap / med[1],
                    "rounds_per_day": med[2], "p_week": med[3],
                    "rounds_to_t2": med[4], "days_to_t2": med[5],
                    "subsets_evaluated": len(recs)})

    print("\nnotes")
    print(f"  cap = bankroll - collateral; the supervisor also refuses when headroom < {MIN_HEAD_C}c")
    print("  P(week) is first-passage against the ENGINE's rule (drawdown from session baseline)")
    print("  days→t2 assumes the subset's own realised duty continues and the edge is stationary")
    print("  medians are over sampled subsets, so no series was chosen for its P&L")
    json.dump(out, open("data/scale_down.json", "w"), indent=2)
    print("\nwrote data/scale_down.json")


if __name__ == "__main__":
    main()
