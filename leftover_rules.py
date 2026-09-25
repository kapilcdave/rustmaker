"""Replay a fill stream under two leftover-reducing rules, market by market.

Rule A (open cutoff X s): no fill that OPENS or ADDS to a position within X s of close; fills that
reduce |position| are always kept (the pairing side keeps quoting).
Rule B (loss cap L c): no opening fill whose loss-if-wrong exceeds L: buying YES at p risks p,
selling YES at p risks 100 - p.
A dropped fill is assumed not to happen (our quote would not have rested); later fills are
re-classified against the recomputed position. Approximation: fills we would have gained from
quotes the source run never posted are not added (same for every rule).

Usage: python3 leftover_rules.py shadow <shadow_tape.csv.gz> | live <journal.jsonl.gz> [...]
"""
import gzip
import json
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from tox import results  # noqa: E402


def close_us(t):
    x = datetime.strptime(t.split("-")[1], "%y%b%d%H%M").replace(tzinfo=timezone.utc) + timedelta(hours=4)
    return int(x.timestamp() * 1e6)


def shadow_fills(path, strat="penny5"):
    df = pd.read_csv(path, dtype={"a": str, "b": str}, low_memory=False)
    f = df[(df.kind == "F") & (df.a == strat)]
    return pd.DataFrame({"ticker": f.ticker, "buy": f.b == "bid", "px": pd.to_numeric(f.c) / 100.0,
                         "ct": pd.to_numeric(f.d), "vt": f.venue_ms.astype("int64")})


def live_fills(paths):
    rows = []
    for p in paths:
        with gzip.open(p, "rt") as fh:
            lines = iter(fh)
            while True:
                try:
                    line = next(lines)
                except (StopIteration, EOFError):
                    break  # a journal still being written has no gzip end marker yet
                if not line.startswith('{"k":"fill"'):
                    continue
                try:
                    v = json.loads(line)["v"]
                except json.JSONDecodeError:
                    break  # torn last line of an in-progress journal
                rows.append({"ticker": v["market_ticker"], "buy": v["book_side"] == "bid",
                             "px": float(v["yes_price_dollars"]) * 100, "ct": float(v["count_fp"]),
                             "vt": v["ts_ms"] * 1000})
    return pd.DataFrame(rows)


def replay(g, y, cutoff_s, loss_cap_c):
    pos = cash = paired = 0.0
    close = close_us(g.ticker.iloc[0])
    for buy, px, ct, vt in zip(g.buy, g.px, g.ct, g.vt):
        d = ct if buy else -ct
        opening = abs(pos + d) > abs(pos) + 1e-9
        if opening:
            if vt > close - cutoff_s * 1e6:
                continue
            risk = px if buy else 100 - px
            if risk > loss_cap_c:
                continue
        cash += -px * ct if buy else px * ct
        pos += d
    return cash + pos * 100 * y, abs(pos)


def score(fills, res, cutoff_s, loss_cap_c):
    out = []
    for t, g in fills.sort_values("vt").groupby("ticker"):
        y = {"yes": 1.0, "no": 0.0}.get(res.get(t))
        if y is None:
            continue
        pnl, left = replay(g, y, cutoff_s, loss_cap_c)
        out.append((pnl, left))
    v = np.array([p for p, _ in out])
    return v.mean(), v.std(ddof=1) / np.sqrt(len(v)), len(v), sum(l for _, l in out)


def main():
    mode, paths = sys.argv[1], sys.argv[2:]
    fills = shadow_fills(paths[0]) if mode == "shadow" else live_fills(paths)
    res = results(sorted(fills.ticker.unique()))
    print(f"{mode}: {len(fills):,} fills over {fills.ticker.nunique()} markets")
    print(f"{'open cutoff':>12} {'loss cap':>9} {'c/market':>9} {'± se':>6} {'mkts':>5} {'leftover ct':>12}")
    for cutoff in [120, 300, 450]:
        for cap in [100, 85, 70, 50]:
            m, se, n, left = score(fills, res, cutoff, cap)
            tag = "  <- current live rule" if (cutoff, cap) == (120, 100) else ""
            print(f"{cutoff:>10} s {cap:>7} c {m:>+9.2f} {se:>6.2f} {n:>5} {left:>12.1f}{tag}")


if __name__ == "__main__":
    main()
