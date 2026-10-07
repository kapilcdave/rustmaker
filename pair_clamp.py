"""How often does a round trip lock a LOSS, and what if the exit quote were held at break-even?

FIFO lots per market (partial fills split correctly). A closing fill priced through the matched
lots' entry is, under `clamp`, assumed not to happen: the exit quote would have rested at
break-even instead, and the position rides on. Real fills only; fills the clamped quote would
have picked up later at break-even are NOT added (conservative for the clamp).

Usage: python3 pair_clamp.py <fills.gz> [...]   (results from data/leftover/results.json)
"""
import sys
from collections import defaultdict, deque

import numpy as np

sys.path.insert(0, ".")
from leftover_portfolio import load  # noqa: E402


def run(rows, clamp):
    lots = defaultdict(deque)  # ticker -> deque of [sign, price, ct]
    cash = defaultdict(float)
    pos = defaultdict(float)
    y = {}
    pairs = []  # locked edge per contract-cent of matched size: (edge_c, ct)
    for f in rows:
        t, s, px, ct = f["ticker"], f["sgn"], f["px"], f["ct"]
        y[t] = f["y"]
        q = lots[t]
        if q and q[0][0] != s:
            # Closing: the edge against the lots it would match (FIFO), before deciding.
            need, edge_ct, k = ct, 0.0, 0
            while need > 1e-9 and k < len(q):
                m = min(need, q[k][2])
                edge_ct += m * ((px - q[k][1]) if q[k][0] > 0 else (q[k][1] - px))
                need -= m
                k += 1
            matched = ct - need
            if clamp and edge_ct < -1e-9:
                continue
            need = ct
            while need > 1e-9 and q:
                m = min(need, q[0][2])
                q[0][2] -= m
                need -= m
                if q[0][2] <= 1e-9:
                    q.popleft()
            if matched > 0:
                pairs.append((edge_ct / matched, matched))
            if need > 1e-9:
                q.append([s, px, need])
        else:
            q.append([s, px, ct])
        pos[t] += s * ct
        cash[t] -= s * ct * px
    pnl = np.array([cash[t] + pos[t] * y[t] for t in y])
    return pnl, pairs


def main():
    rows = []
    for p in sys.argv[1:]:
        rows += load(p, "data/leftover/results.json")
    rows.sort(key=lambda r: r["ts"])
    b, pb = run(rows, False)
    c, _ = run(rows, True)
    e = np.array([x for x, _ in pb])
    w = np.array([m for _, m in pb])
    print(f"{len(rows)} fills, {len(b)} markets, {len(e)} closing fills")
    print(f"pairs that locked a LOSS {(e < 0).mean():.1%} (avg {e[e < 0].mean():+.2f}c), "
          f"profit {(e > 0).mean():.1%} (avg {e[e > 0].mean():+.2f}c), flat {(e == 0).mean():.1%}")
    print(f"pair P&L ${(e * w).sum() / 100:+.2f} = losers ${(e * w)[e < 0].sum() / 100:+.2f} "
          f"+ winners ${(e * w)[e > 0].sum() / 100:+.2f}")
    se = lambda x: x.std(ddof=1) / np.sqrt(len(x))
    print(f"as traded          ${b.sum() / 100:+7.2f}  {b.mean():+.2f}±{se(b):.2f} c/mkt")
    print(f"exit clamp at b/e  ${c.sum() / 100:+7.2f}  {c.mean():+.2f}±{se(c):.2f} c/mkt  "
          f"paired {np.mean(c - b):+.2f}±{se(c - b):.2f}")


if __name__ == "__main__":
    main()
