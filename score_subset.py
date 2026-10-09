#!/usr/bin/env python3
"""Score a NAMED series subset at a given bankroll: round distribution, rope, survival.

`scale_down.py` answered the structural question (collateral vs cap) by SAMPLING subsets, so its
table is medians over subsets and deliberately says nothing about any particular ticker. This
scores the specific subset you are about to arm.

⚠ The subset must come from a PRIOR, never from this output. At least a quarter of all 511 subsets
have a non-positive mean, so picking the best row here and arming it is exactly the selection error
`in-sample-controls-cannot-detect-selection` names. The pair this corpus arms --- ZEC+NEAR --- was
chosen by `zec-near-are-the-widest-15m-books` (penny-jump income needs spread room) and
`eth-15m-gated-maker-closed-same-as-btc`, both of which predate any subset P&L. Its numbers below
are therefore IN-SAMPLE for the pair and are a sizing input, not evidence of an edge.

    .venv/bin/python score_subset.py 4093 KXZEC15M,KXNEAR15M [cap_c]
"""
from __future__ import annotations

import sys

import numpy as np

from ruin import EXTRA_RESULTS, HIST, RESULTS, TODAY, load_fills, round_series, survive

import json

ROUNDS_PER_HOUR = 4
COLLAT_PER_SERIES = 100   # 100c x series x clip, clip 1
MIN_HEAD_C = 250          # the supervisor's loop guard
HOURS_PER_DAY = 24


def main() -> None:
    bankroll = int(sys.argv[1]) if len(sys.argv) > 1 else 4093
    want = set((sys.argv[2] if len(sys.argv) > 2 else "KXZEC15M,KXNEAR15M").split(","))
    cap_override = int(sys.argv[3]) if len(sys.argv) > 3 else None

    # The two caches have DIFFERENT shapes: RESULTS maps ticker -> {"result": ...} while
    # settle_cache maps ticker -> "yes"/"no". A blind dict.update() puts bare strings in and
    # load_fills then does r.get() on a str. Same wrapping as ruin.main().
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
            r, _ = load_fills(path, res)
            rows += r
        except FileNotFoundError:
            print(f"  (missing {path})")
    if not rows:
        sys.exit("no settled fills loaded")

    full_pnl, _, _ = round_series(rows)
    sub = [r for r in rows if r["ticker"].split("-")[0] in want]
    if not sub:
        sys.exit(f"no fills for {sorted(want)}")
    pnl, mkts, cts = round_series(sub)

    k = len(want)
    collat = COLLAT_PER_SERIES * k
    cap = cap_override if cap_override is not None else bankroll - collat
    head = bankroll - collat
    mean, sd = pnl.mean(), pnl.std(ddof=1)
    se = sd / np.sqrt(len(pnl))
    t = mean / se if se else float("nan")

    print(f"subset {','.join(sorted(want))}   k={k}   bankroll {bankroll}c")
    print(f"  rounds {len(pnl)}  markets {int(mkts.sum())}  contracts {int(cts.sum())}"
          f"  total {pnl.sum() / 100:+.2f}$")
    print(f"  per round: mean {mean:+.2f}c  sd {sd:.2f}c  se {se:.2f}c  t {t:+.2f}"
          f"  markets/round {mkts.mean():.1f}")
    print(f"  for comparison, the full 9-series seat: mean {full_pnl.mean():+.2f}c"
          f"  sd {full_pnl.std(ddof=1):.2f}c  over {len(full_pnl)} rounds")
    print()
    print(f"  collateral floor {collat}c   headroom {head}c"
          f"   {'OK' if head >= MIN_HEAD_C else 'UNDER the 250c guard -- would refuse to start'}")
    print(f"  cap {cap}c = {cap / sd:.1f} round-sd   (rope)")

    rng = np.random.default_rng(20261009)
    print()
    print(f"  {'horizon':>10} {'rounds':>7} {'P(survive)':>11} {'E[rounds]':>10}")
    for label, hours in (("1 day", 24), ("1 week", 24 * 7), ("67 days", 24 * 67)):
        n = int(ROUNDS_PER_HOUR * hours)
        p, first = survive(pnl, cap, bankroll, collat, n, 10000, rng)
        print(f"  {label:>10} {n:>7} {p * 100:>10.1f}% {first.mean():>10.0f}")

    # Rounds needed for t=2 at this subset's own mean/sd, and what that is in calendar time at the
    # realised duty. The duty is the fraction of wall-clock rounds the seat actually quoted.
    if mean > 0:
        n_t2 = (2 * sd / mean) ** 2
        print()
        print(f"  rounds to t=2 at this mean/sd: {n_t2:,.0f}")
        print(f"  = {n_t2 / ROUNDS_PER_HOUR:,.0f} quoting hours"
              f"  = {n_t2 / ROUNDS_PER_HOUR / HOURS_PER_DAY:,.0f} days at 100% duty")
    else:
        print("\n  mean is non-positive: t=2 is not reachable and this subset is not a seat")


if __name__ == "__main__":
    main()
