#!/usr/bin/env python3
"""Can the 15M penny seat be FUNDED to significance? The calculation nobody has run.

Every post-mortem in this corpus asks "is the edge real". This asks the other question, which is
the one that has actually been killing the live program: given the seat's own measured noise, how
long does a run survive its own loss cap, and how many markets can it therefore accumulate before
something shuts it off? `kalshi-15m-maker-losses-were-caps-and-collateral` says the fix is "more
markets at a frozen config" and that no run in 25 ever completed a week -- 7 died on loss caps.
That is a first-passage problem with measurable inputs, so it is answerable in closed form rather
than by running another week and seeing.

Unit of analysis is the ROUND (one 15-minute expiry, all series), because co-expiring markets share
one price path and the corpus already clusters on it. Nothing here places an order or needs a
credential: it reads the journal fill rows this tree already holds plus the PUBLIC settlement cache.

    .venv/bin/python ruin.py
"""
from __future__ import annotations

import gzip
import json
from collections import defaultdict

import numpy as np

HIST = "data/leftover/band_fills.gz"        # 45 journals, 2026-09-24 -> 10-05
TODAY = "data/tonight/tonight_fills.txt.gz"  # the 2026-10-07 armed run
RESULTS = "data/leftover/results.json"
EXTRA_RESULTS = "data/settle_cache.json"

ROUNDS_PER_HOUR = 4.0        # a 15M expiry every 15 minutes
TARGET_T = 2.0               # the corpus's own bar for a real effect


def load_fills(path, res):
    """Per-market settled P&L from journal fill rows. Maker rows only; takers are not this seat."""
    rows, unsettled = [], 0
    pos = defaultdict(float)
    raw = []
    with gzip.open(path, "rt") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            j, rest = line.split(" ", 1)
            v = json.loads(rest)["v"]
            if v.get("is_taker"):
                continue
            raw.append((v["ts_ms"], j, v))
    raw.sort(key=lambda x: (x[0], x[1]))     # never let the tie-break reach the dict
    for ts, j, v in raw:
        tk = v["market_ticker"]
        r = res.get(tk)
        if not r or r.get("result") not in ("yes", "no"):
            unsettled += 1
            continue
        sgn = 1.0 if v["book_side"] == "bid" else -1.0
        ct = float(v["count_fp"])
        px = float(v["yes_price_dollars"]) * 100.0
        y = 100.0 if r["result"] == "yes" else 0.0
        pos[(j, tk)] += sgn * ct
        rows.append({"run": j, "ticker": tk, "round": tk.split("-")[1], "ts": ts,
                     "pnl_c": sgn * ct * (y - px), "ct": ct})
    return rows, unsettled


def round_series(rows):
    """Per-(run, round) P&L in cents, in time order. This is the i.i.d.-ish block."""
    g = defaultdict(lambda: {"pnl": 0.0, "ct": 0.0, "mkts": set(), "ts": None})
    for r in rows:
        k = (r["run"], r["round"])
        d = g[k]
        d["pnl"] += r["pnl_c"]
        d["ct"] += r["ct"]
        d["mkts"].add(r["ticker"])
        d["ts"] = r["ts"] if d["ts"] is None else min(d["ts"], r["ts"])
    out = sorted(g.items(), key=lambda kv: kv[1]["ts"])
    return (np.array([v["pnl"] for _, v in out]),
            np.array([len(v["mkts"]) for _, v in out]),
            np.array([v["ct"] for _, v in out]))


def survive(pnl, cap_c, bankroll_c, floor_c, n_rounds, n_sims, rng):
    """P(run the whole horizon without tripping the cumulative cap or the collateral floor).

    Mirrors the engine's own rule, which is what actually fired today:
      `cumulative loss cap hit: equity {e} vs baseline {b} (cap {cap})`
    i.e. a drawdown measured from the SESSION BASELINE, not a per-round stop. The collateral floor
    is the second absorbing barrier: below it the supervisor refuses to start a run.
    """
    draws = rng.choice(pnl, size=(n_sims, n_rounds), replace=True)
    equity = bankroll_c + np.cumsum(draws, axis=1)
    dd = bankroll_c - equity                       # drawdown from baseline, cents
    tripped = (dd >= cap_c) | (equity <= floor_c)
    first = np.where(tripped.any(axis=1), tripped.argmax(axis=1) + 1, n_rounds)
    return float((~tripped.any(axis=1)).mean()), first


def main() -> None:
    res = json.load(open(RESULTS))
    try:
        extra = json.load(open(EXTRA_RESULTS))
        for tk, r in extra.items():
            if tk not in res and r in ("yes", "no"):
                res[tk] = {"result": r}
    except FileNotFoundError:
        pass

    all_rows = []
    for path, label in ((HIST, "history 09-24..10-05"), (TODAY, "armed run 10-07")):
        try:
            rows, uns = load_fills(path, res)
        except FileNotFoundError:
            print(f"  (missing {path}, skipped)")
            continue
        p, m, c = round_series(rows)
        print(f"{label:24s} fills={len(rows):6d} rounds={len(p):4d} markets={m.sum():5d} "
              f"ct={c.sum():7.0f} total={p.sum() / 100:+8.2f}$  (dropped {uns} unsettled)")
        all_rows += rows

    pnl, mkts, cts = round_series(all_rows)
    n = len(pnl)
    mean, sd = pnl.mean(), pnl.std(ddof=1)
    se = sd / np.sqrt(n)
    mk_per_round = mkts.mean()
    print("\n" + "=" * 92)
    print("THE SEAT'S OWN ROUND DISTRIBUTION  (the input every cap should have been sized off)")
    print("=" * 92)
    print(f"  rounds {n}   markets {mkts.sum()}   contracts {cts.sum():.0f}   "
          f"total {pnl.sum() / 100:+.2f}$")
    print(f"  per round:  mean {mean:+7.2f}c   sd {sd:7.2f}c   se {se:6.2f}c   "
          f"t = {mean / se:+5.2f}")
    print(f"  quantiles:  p5 {np.quantile(pnl, .05):+7.1f}c   p25 "
          f"{np.quantile(pnl, .25):+7.1f}c   p50 {np.quantile(pnl, .50):+7.1f}c   "
          f"p75 {np.quantile(pnl, .75):+7.1f}c   p95 {np.quantile(pnl, .95):+7.1f}c")
    print(f"  worst round {pnl.min():+.1f}c   best round {pnl.max():+.1f}c   "
          f"markets/round {mk_per_round:.1f}")

    # ---- realised duty: the factor that turns "$/h at 100%" into income ---------------------
    ts = np.array(sorted(r["ts"] for r in all_rows), dtype=float)
    span_h = (ts[-1] - ts[0]) / 1000.0 / 3600.0
    possible = span_h * ROUNDS_PER_HOUR
    duty = n / possible if possible > 0 else float("nan")
    print("\n" + "=" * 92)
    print("REALISED DUTY  -- every '$/day at 100% duty' in this corpus must be multiplied by this")
    print("=" * 92)
    print(f"  calendar span {span_h:,.0f} h ({span_h / 24:.1f} days)   rounds possible "
          f"{possible:,.0f}   rounds actually quoted {n}")
    print(f"  duty = {duty:.1%}  -- the seat is idle {1 - duty:.0%} of the clock "
          f"(supervisor waits, cap stops, ctrl-c, crashes)")

    # ---- what the edge needs, in rounds and in wall-clock -----------------------------------
    print("\n" + "=" * 92)
    print("PRICE THE PRIZE  -- rounds needed for the measured edge to reach t = 2")
    print("=" * 92)
    if mean > 0:
        need = (TARGET_T * sd / mean) ** 2
        print(f"  at the measured mean {mean:+.2f}c and sd {sd:.2f}c:  "
              f"{need:,.0f} rounds = {need * mk_per_round:,.0f} markets")
        print(f"  at {ROUNDS_PER_HOUR:.0f} rounds/h that is {need / ROUNDS_PER_HOUR:,.0f} h "
              f"= {need / ROUNDS_PER_HOUR / 24:,.1f} days at 100% duty")
        print(f"  income if the edge is real: {mean * ROUNDS_PER_HOUR / 100:+.2f}$/h "
              f"= {mean * ROUNDS_PER_HOUR * 24 / 100:+.2f}$/day at 100% duty")
        print(f"  AT THE REALISED {duty:.0%} DUTY: "
              f"{mean * ROUNDS_PER_HOUR * 24 * duty / 100:+.2f}$/day, and t=2 arrives in "
              f"{need / ROUNDS_PER_HOUR / 24 / duty:,.0f} calendar days")
    else:
        print(f"  mean is {mean:+.2f}c -- NEGATIVE pooled, so there is no t=2 to reach and no "
              f"cap or bankroll makes it pay. Significance is not the binding question.")

    # ---- survival against the two absorbing barriers ----------------------------------------
    rng = np.random.default_rng(0)
    week = int(round(7 * 24 * ROUNDS_PER_HOUR))
    day = int(round(24 * ROUNDS_PER_HOUR))
    print("\n" + "=" * 92)
    print(f"SURVIVAL  -- P(reach the horizon without tripping the cap), {10_000} sims, "
          f"floor 900c")
    print("=" * 92)
    print(f"  a day is {day} rounds, a week is {week} rounds at 100% duty\n")
    hdr = f"  {'bankroll':>9} {'cap':>7} | {'P(1 day)':>9} {'P(1 week)':>10} " \
          f"{'E[rounds]':>10} {'E[markets]':>11}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for bank in (1052, 1628, 2500, 5000, 10000):
        for cap in (400, 800, 1600, 3200):
            if cap > bank - 900:
                continue
            p1, f1 = survive(pnl, cap, bank, 900.0, day, 10_000, rng)
            p7, f7 = survive(pnl, cap, bank, 900.0, week, 10_000, rng)
            print(f"  {bank / 100:8.2f}$ {cap / 100:6.2f}$ | {p1:9.1%} {p7:10.1%} "
                  f"{f7.mean():10.0f} {f7.mean() * mk_per_round:11,.0f}")

    print("\n" + "=" * 92)
    print("WHAT TODAY ACTUALLY WAS")
    print("=" * 92)
    p1, f1 = survive(pnl, 400.0, 1628.0, 900.0, day, 10_000, rng)
    p7, _ = survive(pnl, 400.0, 1628.0, 900.0, week, 10_000, rng)
    print(f"  config run 2026-10-07: bankroll 16.28$, cumulative cap 4.00$, floor 9.00$")
    print(f"  P(surviving one full day) = {p1:.1%}   P(a week) = {p7:.1%}   "
          f"E[rounds before a stop] = {f1.mean():.0f} of {day}")
    print(f"  -> the cap is {400 / sd:.2f} round-sd. It fires on noise, exactly as the corpus "
          f"said a 1$ cap did.")


if __name__ == "__main__":
    main()
