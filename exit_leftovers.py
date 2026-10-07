"""What would it cost to EXIT leftovers as a taker instead of holding them to settlement?

On the live journals' real fills and real touch (B rows = the book as the engine saw it, own
orders included), per market:
  hold      as traded: whatever is open at the end settles.
  cut       the position open when the engine stops opening (close - 450 s) is sold at the bid
            (long YES) / bought at the ask (short) at that moment, plus the taker fee
            ceil(7 p (1 - p)) c; fills after the cut still happen (they are all reducing).
  instant   every fill is exited as a taker 1 s after it: sell at the bid / buy at the ask then,
            minus the fee. The "never hold anything" extreme.
Markets need a settled result in data/leftover/results.json.

Usage: python3 exit_leftovers.py <live_*.jsonl.gz> [...]   -- run on the LAPTOP; a journal's B rows
do not fit next to a live engine on the 945 MB box.
"""
import bisect
import datetime as dt
import gzip
import json
import math
import sys
from collections import defaultdict

import numpy as np

CUT_S = 450


def fee_c(px):
    return math.ceil(7 * (px / 100) * (1 - px / 100) - 1e-9)


def close_us(tk):
    # Ticker time is US Eastern (EDT, UTC-4 through Nov 1).
    x = dt.datetime.strptime(tk.split("-")[1], "%y%b%d%H%M") + dt.timedelta(hours=4)
    return int(x.replace(tzinfo=dt.timezone.utc).timestamp() * 1e6)


def read(path):
    fills, touch = [], defaultdict(lambda: ([], [], []))
    with gzip.open(path, "rt") as fh:
        try:
            for line in fh:
                if line.startswith('{"k":"fill"'):
                    v = json.loads(line)["v"]
                    fills.append((v["ts_ms"] * 1000, v["market_ticker"], 1 if v["book_side"] == "bid" else -1,
                                  float(v["yes_price_dollars"]) * 100, float(v["count_fp"])))
                elif line.startswith('{"k":"B"'):
                    t, vt, b, _, a, _ = json.loads(line)["v"]
                    ts, bs, as_ = touch[t]
                    ts.append(vt); bs.append(b / 100.0); as_.append(a / 100.0)
        except (EOFError, json.JSONDecodeError):
            pass  # a journal still being written
    return fills, touch


def at(touch, tk, t_us):
    ts, bs, as_ = touch.get(tk, ([], [], []))
    i = bisect.bisect_right(ts, t_us) - 1
    return (bs[i], as_[i]) if i >= 0 else (None, None)


def main():
    res = json.load(open("data/leftover/results.json"))
    out = defaultdict(list)
    n_open_cut = 0
    for path in sys.argv[1:]:
        fills, touch = read(path)
        by = defaultdict(list)
        for f in sorted(fills):
            by[f[1]].append(f)
        for tk, fs in by.items():
            r = res.get(tk, {}).get("result")
            if r not in ("yes", "no"):
                continue
            y = 100.0 if r == "yes" else 0.0
            cut = close_us(tk) - CUT_S * 1_000_000
            pos = cash = 0.0
            pos_c = cash_c = 0.0
            inst = 0.0
            done_cut = False
            for vt, _, s, px, ct in fs:
                if vt > cut and not done_cut:
                    done_cut = True
                    pos_c, cash_c = pos, cash
                    if pos:
                        n_open_cut += 1
                        b, a = at(touch, tk, cut)
                        xp = b if pos > 0 else a
                        if xp is None:
                            xp = y
                        cash_c += pos * xp - abs(pos) * fee_c(xp)
                        pos_c = 0.0
                pos += s * ct
                cash -= s * ct * px
                if done_cut:
                    pos_c += s * ct
                    cash_c -= s * ct * px
                b, a = at(touch, tk, vt + 1_000_000)
                xp = (b if s > 0 else a)
                xp = y if xp is None else xp
                inst += ct * ((xp - px) if s > 0 else (px - xp)) - ct * fee_c(xp)
            if not done_cut:
                pos_c, cash_c = pos, cash
                if pos:
                    n_open_cut += 1
                    b, a = at(touch, tk, cut)
                    xp = b if pos > 0 else a
                    xp = y if xp is None else xp
                    cash_c += pos * xp - abs(pos) * fee_c(xp)
                    pos_c = 0.0
            out["hold"].append((close_us(tk), cash + pos * y))
            out["cut"].append((close_us(tk), cash_c + pos_c * y))
            out["instant"].append((close_us(tk), inst))
    print(f"{len(out['hold'])} settled markets, {n_open_cut} held a position at the cut\n")
    for k in ("hold", "cut", "instant"):
        x = np.array([v for _, v in out[k]])
        rnd = defaultdict(float)
        for c, v in out[k]:
            rnd[c] += v
        r = np.array([rnd[c] for c in sorted(rnd)])
        cum = np.cumsum(r)
        dd = np.max(np.maximum.accumulate(np.r_[0, cum])[1:] - cum)
        print(f"{k:<8} ${x.sum() / 100:+7.2f}  {x.mean():+6.2f}±{x.std(ddof=1) / np.sqrt(len(x)):.2f} c/mkt"
              f"  round sd {r.std(ddof=1):5.1f}c  worst round {r.min():+6.0f}c  maxDD ${dd / 100:.2f}")


if __name__ == "__main__":
    main()
