"""Replay every live penny run's real fills under leftover/variance rules.

Rules (a dropped fill is assumed not to happen: our quote would not have rested; later fills are
re-classified against the recomputed positions; fills the source run never got are NOT added):
  base         the fills as they happened
  wing L       drop a fill that opens/adds to a market position whose loss-if-wrong exceeds L cents
               (buy YES at p risks p; sell YES at p risks 100 - p)
  net N        per 15-min ROUND (all series closing together), drop a fill that pushes the summed
               YES-position across series beyond |N| contracts; crypto series move together, so the
               sum is the round's directional bet. Fills that shrink |sum| are always kept.
  net N + wing L  both

P&L per market = cash + position x settlement (fees are 0.0064 $ total across all runs; ignored).

Usage: python3 leftover_portfolio.py data/leftover/penny_fills.gz data/leftover/results.json
  (fills: `<journal> <fill json>` lines grepped from data/live_penny/live_*.jsonl.gz on the box)
"""
import gzip
import json
import sys
from collections import defaultdict

import numpy as np


def load(fills_path, results_path):
    res = json.load(open(results_path))
    rows = []
    for line in gzip.open(fills_path, "rt"):
        journal, j = line.split(" ", 1)
        v = json.loads(j)["v"]
        r = res.get(v["market_ticker"], {})
        if r.get("result") not in ("yes", "no"):
            continue  # unsettled (the run in progress)
        rows.append({"run": journal.split("_")[1].split(".")[0], "ticker": v["market_ticker"],
                     "round": r["close_time"], "ts": v["ts_ms"],
                     "sgn": 1.0 if v["book_side"] == "bid" else -1.0,
                     "px": float(v["yes_price_dollars"]) * 100, "ct": float(v["count_fp"]),
                     "y": 100.0 if r["result"] == "yes" else 0.0})
    rows.sort(key=lambda x: x["ts"])
    return rows


def replay(rows, wing=None, net=None):
    pos = defaultdict(float)
    cash = defaultdict(float)
    rnd_net = defaultdict(float)
    meta = {}
    kept = 0
    for f in rows:
        t, s, ct, px = f["ticker"], f["sgn"], f["ct"], f["px"]
        meta[t] = (f["run"], f["round"], f["y"])
        new = pos[t] + s * ct
        opens = abs(new) > abs(pos[t])
        if wing is not None and opens:
            risk = px if s > 0 else 100 - px
            if risk > wing:
                continue
        if net is not None:
            nn = rnd_net[f["round"]] + s * ct
            if abs(nn) > net and abs(nn) > abs(rnd_net[f["round"]]):
                continue
            rnd_net[f["round"]] = nn
        pos[t] = new
        cash[t] -= s * ct * px
        kept += 1
    mk = {t: cash[t] + pos[t] * meta[t][2] for t in meta}
    return mk, meta, kept


def summarise(name, mk, meta, kept, n_fills):
    t = sorted(mk)
    pnl = np.array([mk[x] for x in t])
    n = len(pnl)
    by_round = defaultdict(float)
    for x in t:
        by_round[meta[x][1]] += mk[x]
    r = np.array([by_round[k] for k in sorted(by_round)])
    cum = np.cumsum(r)
    dd = float(np.max(np.maximum.accumulate(np.concatenate([[0], cum]))[1:] - cum))
    half = n // 2
    order = sorted(t, key=lambda x: meta[x][1])
    h1 = np.mean([mk[x] for x in order[:half]])
    h2 = np.mean([mk[x] for x in order[half:]])
    print(f"{name:<18} ${pnl.sum()/100:+7.2f}  {pnl.mean():+5.2f}±{pnl.std(ddof=1)/np.sqrt(n):.2f} c/mkt"
          f"  H1 {h1:+5.2f} H2 {h2:+5.2f}  round sd {r.std(ddof=1):5.1f}c  worst {r.min():+6.1f}c"
          f"  p5 {np.percentile(r, 5):+6.1f}c  maxDD ${dd/100:5.2f}  fills {kept/n_fills:5.1%}")


def main():
    rows = load(sys.argv[1], sys.argv[2])
    n = len(rows)
    print(f"{n} settled fills, {len({r['ticker'] for r in rows})} markets, "
          f"{len({r['round'] for r in rows})} rounds, {len({r['run'] for r in rows})} journals\n")
    for name, kw in [("base", {}),
                     ("wing 85", {"wing": 85}), ("wing 75", {"wing": 75}), ("wing 60", {"wing": 60}),
                     ("net 1", {"net": 1}), ("net 2", {"net": 2}), ("net 3", {"net": 3}),
                     ("net 4", {"net": 4}),
                     ("net 2 + wing 85", {"net": 2, "wing": 85}),
                     ("net 3 + wing 85", {"net": 3, "wing": 85})]:
        summarise(name, *replay(rows, **kw), n)
    print("\nper journal, base vs net 2:")
    runs = sorted({r["run"] for r in rows})
    for run in runs:
        sub = [r for r in rows if r["run"] == run]
        b = sum(replay(sub)[0].values()) / 100
        c = sum(replay(sub, net=2)[0].values()) / 100
        print(f"  {run}  base ${b:+6.2f}  net2 ${c:+6.2f}  ({len(sub)} fills)")


if __name__ == "__main__":
    main()
