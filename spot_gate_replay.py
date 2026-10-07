"""Would an OKX-triggered pull have avoided worse fills than the Coinbase pull alone?

For every live fill (journal) inside a spotprobe window: the 1 s spot return on coinbase_ticker
and on okx as seen on the box's receipt clock LAT_MS before the fill (the decision had to be made
then), signed so that + = the move ran INTO our fill (we bought and spot fell, or sold and it
rose). Groups: coinbase >= 2 bps adverse (the live pull; fills here slipped through anyway),
okx-only >= X bps adverse, neither. Outcome: Kalshi mid 5 s and 60 s after the fill minus the
fill price, from the maker's side (c/ct), from the journal's own B rows. All series use BTC
spot (the crypto 15M books co-move).

Usage: python3 spot_gate_replay.py <spotprobe.csv.gz> <live_*.jsonl.gz>
"""
import bisect
import gzip
import json
import sys
from collections import defaultdict

import numpy as np

LAT_MS = 12


def main():
    spot = defaultdict(lambda: ([], []))
    with gzip.open(sys.argv[1], "rt") as fh:
        next(fh)
        for line in fh:
            if line.startswith("#"):
                continue
            f, recv, _, px = line.split(",")
            if f in ("coinbase_ticker", "okx"):
                spot[f][0].append(int(recv)); spot[f][1].append(float(px))
    lo, hi = max(spot[f][0][0] for f in spot), min(spot[f][0][-1] for f in spot)
    fills, book = [], defaultdict(lambda: ([], []))
    with gzip.open(sys.argv[2], "rt") as fh:
        try:
            for line in fh:
                if line.startswith('{"k":"fill"'):
                    v = json.loads(line)["v"]
                    fills.append((v["ts_ms"] * 1000, v["market_ticker"], 1 if v["book_side"] == "bid" else -1,
                                  float(v["yes_price_dollars"]) * 100))
                elif line.startswith('{"k":"B"'):
                    t, vt, b, _, a, _ = json.loads(line)["v"]
                    book[t][0].append(vt); book[t][1].append((b + a) / 200.0)
        except (EOFError, json.JSONDecodeError):
            pass

    def px_at(f, t):
        ts, ps = spot[f]
        i = bisect.bisect_right(ts, t) - 1
        return ps[i] if i >= 0 else None

    def mid_at(tk, t):
        ts, ms = book[tk]
        i = bisect.bisect_right(ts, t) - 1
        return ms[i] if i >= 0 else None

    rows = []
    for t, tk, s, px in fills:
        if not (lo + 2e6 < t < hi):
            continue
        d = t - LAT_MS * 1000
        adv = {}
        for f in ("coinbase_ticker", "okx"):
            a, b = px_at(f, d), px_at(f, d - 1_000_000)
            adv[f] = -s * (a / b - 1) * 1e4 if a and b else np.nan
        m5, m60 = mid_at(tk, t + 5_000_000), mid_at(tk, t + 60_000_000)
        if m5 is None or m60 is None:
            continue
        rows.append((adv["coinbase_ticker"], adv["okx"], s * (m5 - px), s * (m60 - px)))
    r = np.array(rows)
    print(f"{len(r)} fills in the window\n")
    for x in (1.0, 2.0):
        groups = {
            f"coinbase >= 2 bps adverse": r[:, 0] >= 2,
            f"okx >= {x:g} bps, coinbase not": (r[:, 1] >= x) & ~(r[:, 0] >= 2),
            "neither": ~(r[:, 0] >= 2) & ~(r[:, 1] >= x),
        }
        print(f"okx threshold {x:g} bps")
        for g, m in groups.items():
            if m.sum() == 0:
                print(f"  {g:<32} n=0"); continue
            se = lambda v: v.std(ddof=1) / np.sqrt(len(v)) if len(v) > 1 else float("nan")
            print(f"  {g:<32} n={m.sum():4d}  mk5s {r[m, 2].mean():+6.2f}±{se(r[m, 2]):.2f}  mk60s {r[m, 3].mean():+6.2f}±{se(r[m, 3]):.2f} c/ct")


if __name__ == "__main__":
    main()
