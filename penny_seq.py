"""Sequential penny-jump simulation with the constraints the vectorised scorer ignores:
one 1-ct quote per side, |position| <= 1, a fill removes that side's quote until a repost lands
LAG later, one fill per taker ORDER (same side + same venue ms = one order), no fills in the last
120 s. Features/gate as in penny.py. Reports settlement c per MARKET, clustered SE, H1/H2.
Usage: python3 penny_seq.py tape.csv.gz [min_room_ticks]
"""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, ".")
from tox import close_utc, load, results  # noqa: E402
from penny import LAG_US, tick_fp  # noqa: E402

MIN_ROOM = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0


def sim_market(bb, tt, close_us):
    bb = bb.sort_values("vt")
    bb = bb[(bb.bid > 0) & (bb.ask > 0)]
    if len(bb) < 20 or len(tt) == 0:
        return []
    bvt, bid, ask = bb.vt.to_numpy(), bb.bid.to_numpy(), bb.ask.to_numpy()
    bs_all, as_all = bb.bidsz.to_numpy(), bb.asksz.to_numpy()
    mid = (bid + ask) / 200.0
    tt = tt.sort_values(["vt", "recv_us"])
    pos, ready = 0, {"yes": -1, "no": -1}
    last = (None, None)
    fills = []
    for v, side in zip(tt.vt.to_numpy(), tt.side.to_numpy()):
        if (side, v) == last:
            continue  # a fragment of a taker order we already processed
        last = (side, v)
        if v > close_us - 120_000_000 or v < ready[side]:
            continue
        i = np.searchsorted(bvt, v - LAG_US, side="right") - 1
        j = np.searchsorted(bvt, v, side="right") - 1
        if i < 0 or i != j:
            continue
        b, a = bid[i], ask[i]
        tk = int(tick_fp(a if side == "yes" else b))
        if (a - b) < MIN_ROOM * tk:
            continue
        i1 = max(np.searchsorted(bvt, v - LAG_US - 1_000_000, side="right") - 1, 0)
        mom = mid[i] - mid[i1]
        tot = max(bs_all[i] + as_all[i], 1)
        if side == "yes":  # our improved ask sells 1
            if pos - 1 < -1 or mom > 0.25 or bs_all[i] / tot > 0.9213:
                continue
            px, s = a - tk, 1.0
            pos -= 1
        else:              # our improved bid buys 1
            if pos + 1 > 1 or -mom > 0.25 or as_all[i] / tot > 0.9213:
                continue
            px, s = b + tk, -1.0
            pos += 1
        ready[side] = v + LAG_US  # our quote on that side is gone until the repost lands
        k60 = min(np.searchsorted(bvt, v + 60_000_000, side="right") - 1, len(bvt) - 1)
        fills.append((v, s, px / 100.0, s * (px / 100.0 - mid[k60])))
    return fills


b, t = load(sys.argv[1])
res = results(sorted(t.ticker.unique()))
rows = []
for tk, tt in t.groupby("ticker"):
    y = {"yes": 1.0, "no": 0.0}.get(res.get(tk, ""))
    if y is None:
        continue
    for v, s, P, mk in sim_market(b[b.ticker == tk], tt, close_utc(tk)):
        rows.append({"ticker": tk, "vt": v, "settle": s * (P - 100 * y), "mk60s": mk})
d = pd.DataFrame(rows)
d["half"] = np.where(d.vt < d.vt.median(), "H1", "H2")
all_mkts = sorted(t.ticker.unique())
print(f"min room {MIN_ROOM} ticks: {len(d):,} fills over {d.ticker.nunique()} markets "
      f"({len(d)/max(d.ticker.nunique(),1):.1f} fills/market)")
print(f"  per contract: settle {d.settle.mean():+.3f} c, mk60s {d.mk60s.mean():+.3f} c")
for h in ["H1", "H2", None]:
    x = d if h is None else d[d.half == h]
    pm = x.groupby("ticker").settle.sum()
    print(f"  {h or 'ALL'}: {pm.mean():+.1f} ± {pm.std(ddof=1)/np.sqrt(len(pm)):.1f} c/market over {len(pm)} markets"
          f"  (${pm.sum()/100:+.2f} total)")
