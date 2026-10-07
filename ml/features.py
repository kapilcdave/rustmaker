"""Decision rows for the GLiNER gate: one row per moment the penny-jump quoter had a choice.

Two kinds of row, both read ONLY from state available at the decision instant minus LAG_US:

  entry  a simulated improved-quote fill (penny.py's fill model: we create a new level one tick
         inside the touch, so there is no queue; the next taker on that side hits us). Label is
         BUY_YES / BUY_NO when that fill made money net of fees, HOLD when it did not.
  exit   a grid point while one LOT is still open. Crossing out now pays the taker fee; carrying
         pays settlement. When crossing out is the better of the two the label names which kind of
         exit it was -- TAKE_PROFIT if the lot is up on the entry, STOP_LOSS if it is down -- so the
         thresholds are never invented, they are read off realized P&L. HOLD otherwise.
         A pure time stop is not a label: the deterministic layer in serve.py owns it.

The text field is the serialized snapshot the model sees; the numeric columns are kept so a
logistic / gradient-boosting arm can be trained on the SAME rows (the reproduction arm).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

LAG_US = 11_000        # measured decision -> book time (feed 6.2 ms + create 4.9 ms)
MAKER_FEE_C = 0.0      # 15M crypto maker fills, matching penny.py; override per series if needed
EXIT_GRID_US = 5_000_000      # lot-level exit rows: one per lot per 5 s, so row count stays sane
MAX_LOT_AGE_US = 300_000_000  # stop asking about a lot after 5 min; serve.py's time stop matches
POS_CAP = 10           # contracts per market; a fill that would breach the cap was never available
GAMMA_AS = 1.5e-4      # Avellaneda-Stoikov risk aversion, 1/cents. A SCALE CHOICE, not a fit:
                       # the A-S spread term is gamma-free on this lattice (every gamma the 1c
                       # lattice admits is O(1e-2) against k ~ 109, so ln(1+g/k) -> g/k), and the
                       # inventory skew is linear in gamma, so a linear model rescales it freely.
                       # It is NOT free for GLiNER, which reads the number as TEXT: gamma=0.1 put
                       # "reservation 21435.42c" in the sentence on a 0-100 binary. Calibrated so
                       # the skew is ~1 c at the 10-contract cap with median sigma (1.1 c/sqrt-s)
                       # and ttc (400 s): 1/(10 * 1.1^2 * 400).
K_AS = 108.62          # arrival decay per DOLLAR, age-standardised on 1.66M Kalshi 15M orders.
                       # MEASURED BUT NOT IDENTIFIED: the per-age-bin range is k = +121 to -219
                       # (the hazard ratio inverts past ~8 s of resting), so 1/k below is a
                       # reference distance, not a prescription. Order age is set by our own
                       # requote cadence, which makes k downstream of a policy choice.
AS_HALF_C = 100.0 / K_AS       # 0.921 c -- the A-S optimal half spread at the standardised k
LABELS = ["BUY_YES", "BUY_NO", "HOLD", "TAKE_PROFIT", "STOP_LOSS"]
EXIT_ACTIONS = ("TAKE_PROFIT", "STOP_LOSS")
TASK = "action"


def taker_fee_c(px_c: float) -> float:
    """Kalshi taker fee for one contract, cents, rounded up (same form as latency_arb.py)."""
    p = max(min(px_c / 100.0, 1.0), 0.0)
    return math.ceil(0.07 * p * (1 - p) * 100 - 1e-9)


def tick_fp(price_fp):
    # 1e-4 dollar units: 10 = 0.1 c below 10 c and above 90 c (sub-cent ladder), else 100 = 1 c
    return np.where((price_fp < 1000) | (price_fp > 9000), 10, 100)


def _at(idx, arr):
    return arr[np.clip(idx, 0, len(arr) - 1)]


MARKS = {"settle": "settle_c", "mk60s": "mk60s_c", "mk5s": "mk5s_c"}


def _context(vt_us, bi, bvt, mid, I, Q, tv, cpv, cpc, csg, w_us=60_000_000):
    """Trailing tape context at each decision instant, from state strictly before vt - LAG only.

    VWAP is the contract's own traded price, which the book snapshot does not contain: a 2-tick
    book sitting 3 c away from where size actually printed is a different situation from the same
    book at the VWAP, and nothing in the original 14 features could tell them apart.
    """
    out = {}
    n = len(bvt)
    t0 = vt_us - LAG_US
    i60 = np.clip(np.searchsorted(bvt, t0 - w_us, side="right") - 1, 0, n - 1)
    i10 = np.clip(np.searchsorted(bvt, t0 - 10_000_000, side="right") - 1, 0, n - 1)
    sp60 = np.maximum(bvt[bi] - bvt[i60], 1)
    sp10 = np.maximum(bvt[bi] - bvt[i10], 1)
    # time-weighted, not per-update: book updates arrive in bursts, so an unweighted mean of rows
    # is a mean over EVENTS and overweights whatever was busy
    out["ma10_c"] = np.where(bvt[bi] > bvt[i10], (I[bi] - I[i10]) / sp10, mid[bi])
    out["ma60_c"] = np.where(bvt[bi] > bvt[i60], (I[bi] - I[i60]) / sp60, mid[bi])
    out["sigma_c"] = np.sqrt(np.maximum(Q[bi] - Q[i60], 0.0) / (sp60 / 1e6))
    k = np.searchsorted(tv, t0, side="right")
    k0 = np.searchsorted(tv, t0 - w_us, side="right")
    v60 = cpc[k] - cpc[k0]
    out["vwap60_c"] = np.where(v60 > 0, (cpv[k] - cpv[k0]) / np.maximum(v60, 1), mid[bi])
    out["vwap_c"] = np.where(cpc[k] > 0, cpv[k] / np.maximum(cpc[k], 1), mid[bi])
    out["tvol60"] = v60.astype(float)
    # signed taker flow: +1 = every print in the window lifted an ask
    out["flow60"] = np.where(v60 > 0, (csg[k] - csg[k0]) / np.maximum(v60, 1), 0.0)
    return out


def _derive(f: pd.DataFrame) -> pd.DataFrame:
    """Row-level features built from the context columns. Signed by s wherever a direction exists,
    so one coefficient covers both sides instead of the model having to learn the sign twice."""
    f["mid_ma10_c"] = f.mid_c - f.ma10_c
    f["mid_ma60_c"] = f.mid_c - f.ma60_c
    f["vwap_dev_c"] = f.mid_c - f.vwap60_c           # book above where size actually traded
    f["px_vwap_c"] = f.s * (f.px_c - f.vwap60_c)     # our quote's edge over the traded price
    f["flow_adv60"] = f.s * f.flow60                 # >0: the window's takers hit OUR side
    f["as_skew_c"] = -f.pos_before * GAMMA_AS * f.sigma_c ** 2 * f.ttc_s
    f["as_resv_c"] = f.mid_c + f.as_skew_c           # A-S reservation price
    f["as_edge_c"] = f.s * (f.px_c - f.as_resv_c)    # distance of our quote from it, signed
    f["as_room_c"] = f.s * (f.px_c - f.mid_c) - AS_HALF_C   # half spread minus the A-S optimum
    return f


def market_rows(bb: pd.DataFrame, tt: pd.DataFrame, close_us: int, result_yes: float,
                ticker: str, min_room_ticks: int = 2, mark: str = "mk5s") -> pd.DataFrame | None:
    """All decision rows for one market. bb/tt are that market's book and print frames."""
    bb = bb.sort_values("vt")
    bb = bb[(bb.bid > 0) & (bb.ask > 0)]
    if len(bb) < 20 or len(tt) == 0 or not np.isfinite(result_yes):
        return None
    bvt = bb.vt.to_numpy()
    bid, ask = bb.bid.to_numpy(), bb.ask.to_numpy()
    bsz, asz = bb.bidsz.to_numpy(), bb.asksz.to_numpy()
    mid = (bid + ask) / 200.0                      # cents

    # cumulative arrays for the trailing context, built once per market. I integrates mid over
    # TIME so a moving average is time-weighted; Q accumulates squared mid changes for sigma.
    dt = np.diff(bvt, prepend=bvt[0])
    I = np.concatenate([[0.0], np.cumsum(mid[:-1] * np.diff(bvt))])
    Q = np.concatenate([[0.0], np.cumsum(np.diff(mid) ** 2)])
    ts = tt.sort_values("vt")
    tv = ts.vt.to_numpy()
    tc = ts["count"].to_numpy().astype(float)
    tpx = ts.price.to_numpy().astype(float)
    tsg = np.where(ts.side.to_numpy() == "yes", 1.0, -1.0)
    cpv = np.concatenate([[0.0], np.cumsum(tpx * tc)])     # price x size, for VWAP
    cpc = np.concatenate([[0.0], np.cumsum(tc)])           # size
    csg = np.concatenate([[0.0], np.cumsum(tsg * tc)])     # signed size, for taker flow
    ctx = lambda v, bi: _context(v, bi, bvt, mid, I, Q, tv, cpv, cpc, csg)  # noqa: E731

    entries = []
    for side, g in tt.sort_values("vt").groupby("side"):
        v = g.vt.to_numpy()
        i = np.searchsorted(bvt, v - LAG_US, side="right") - 1   # touch our quote was built on
        j = np.searchsorted(bvt, v, side="right") - 1            # touch just before the print
        live = (i >= 0) & (i == j)                               # quote survived to the print
        i = np.clip(i, 0, len(bvt) - 1)
        b, a = bid[i], ask[i]
        tk = tick_fp(np.where(side == "yes", a, b))
        room = (a - b) >= min_room_ticks * tk
        m = live & room
        if not m.any():
            continue
        ii = i[m]
        vv = v[m]
        i1 = np.searchsorted(bvt, vv - LAG_US - 1_000_000, side="right") - 1
        i5 = np.searchsorted(bvt, vv - LAG_US - 5_000_000, side="right") - 1
        mom1 = mid[ii] - _at(i1, mid)
        mom5 = mid[ii] - _at(i5, mid)
        k5 = np.searchsorted(bvt, vv + 5_000_000, side="right") - 1
        k60 = np.searchsorted(bvt, vv + 60_000_000, side="right") - 1
        s = 1.0 if side == "yes" else -1.0          # yes-taker lifts our improved ask: we sold yes
        px_c = (np.where(side == "yes", a[m] - tk[m], b[m] + tk[m])) / 100.0
        entries.append(pd.DataFrame({
            "vt": vv, "kind": "entry", "s": s, "px_c": px_c,
            "bid_c": b[m] / 100.0, "ask_c": a[m] / 100.0, "mid_c": mid[ii],
            "spread_ticks": ((a - b) / tk)[m],
            "bidsz": bsz[ii], "asksz": asz[ii],
            "imb": bsz[ii] / np.maximum(bsz[ii] + asz[ii], 1),
            "mom1s_c": mom1, "mom5s_c": mom5,
            "ttc_s": (close_us - vv) / 1e6,
            **ctx(vv, ii),
            "mk5s_c": s * (px_c - _at(k5, mid)),
            "mk60s_c": s * (px_c - _at(k60, mid)),
            "pos_before": 0.0,
        }))
    if not entries:
        return None
    e = pd.concat(entries, ignore_index=True).sort_values("vt").reset_index(drop=True)
    # Three marks for the same fill. The LABEL and the scored metric must be the same one, or the
    # model is trained to optimize something the arm table does not measure; `mark` picks it once and
    # build_dataset records it in the manifest so the scorer cannot disagree.
    e["settle_c"] = e.s * (e.px_c - 100.0 * result_yes) - MAKER_FEE_C
    e["mk5s_c"] = e.mk5s_c - MAKER_FEE_C
    e["mk60s_c"] = e.mk60s_c - MAKER_FEE_C
    e["net_c"] = e[MARKS[mark]]
    e["label"] = np.where(e.net_c <= 0, "HOLD", np.where(e.s < 0, "BUY_YES", "BUY_NO"))

    # inventory path the quoter would have carried, under a position cap: a fill that would have
    # breached the cap was not a decision we could have taken, so its row is dropped.
    pos, before, taken = 0.0, [], []
    for s_ in e.s.to_numpy():
        d_ = -s_
        if abs(pos + d_) > POS_CAP:
            before.append(np.nan); taken.append(False); continue
        before.append(pos); taken.append(True); pos += d_
    e["pos_before"] = before
    e = e[np.array(taken)].reset_index(drop=True)
    if not len(e):
        return None
    # One lot per accepted fill, each with its own exit decisions. A lot-level row is what lets the
    # label distinguish taking a profit from stopping a loss: both are "cross out now", and only the
    # lot's own entry price says which one it is.
    # globally unique: a bare counter restarts in every market, and anything that groups by lot
    # across markets (the JSONL subsample) then silently pools unrelated lots
    e["lot_id"] = [f"{ticker}#{k}" for k in range(len(e))]
    exits = []
    for lot in e.itertuples():
        grid = np.arange(lot.vt + EXIT_GRID_US,
                         min(lot.vt + MAX_LOT_AGE_US, close_us - 120_000_000), EXIT_GRID_US)
        if not len(grid):
            continue
        lot_pos = -lot.s                      # +1 long yes, -1 short yes
        ii = np.clip(np.searchsorted(bvt, grid - LAG_US, side="right") - 1, 0, len(bvt) - 1)
        i1 = np.searchsorted(bvt, grid - LAG_US - 1_000_000, side="right") - 1
        i5 = np.searchsorted(bvt, grid - LAG_US - 5_000_000, side="right") - 1
        xpx = (bid[ii] if lot_pos > 0 else ask[ii]) / 100.0    # crossing out costs the touch
        fee = np.array([taker_fee_c(x) for x in xpx])
        unreal = lot_pos * (xpx - lot.px_c)                    # mark to the exitable side, no fee
        close_pnl = unreal - fee
        carry_pnl = lot_pos * (100.0 * result_yes - lot.px_c)
        gain = close_pnl - carry_pnl                           # already per contract: a lot is 1 ct
        tk = tick_fp(bid[ii] if lot_pos > 0 else ask[ii])
        exits.append(pd.DataFrame({
            "vt": grid, "kind": "exit", "s": lot.s, "px_c": xpx,
            "bid_c": bid[ii] / 100.0, "ask_c": ask[ii] / 100.0, "mid_c": mid[ii],
            "spread_ticks": (ask[ii] - bid[ii]) / tk,
            "bidsz": bsz[ii], "asksz": asz[ii],
            "imb": bsz[ii] / np.maximum(bsz[ii] + asz[ii], 1),
            "mom1s_c": mid[ii] - _at(i1, mid), "mom5s_c": mid[ii] - _at(i5, mid),
            "ttc_s": (close_us - grid) / 1e6,
            **ctx(grid, ii),
            "mk5s_c": np.nan, "mk60s_c": np.nan, "settle_c": np.nan,
            "pos_before": float(lot_pos), "lot_id": lot.lot_id,
            "entry_px_c": lot.px_c, "unreal_c": unreal, "age_s": (grid - lot.vt) / 1e6,
            "net_c": gain,
            "label": np.where(gain > 0,
                              np.where(unreal > 0, "TAKE_PROFIT", "STOP_LOSS"), "HOLD"),
        }))
    e["entry_px_c"] = np.nan
    e["unreal_c"] = np.nan
    e["age_s"] = np.nan
    d = pd.concat([e] + exits, ignore_index=True).sort_values("vt").reset_index(drop=True)
    d = _derive(d)
    d["ticker"] = ticker
    d["series"] = ticker.split("-")[0]
    d["result_yes"] = result_yes
    return d[d.vt < close_us - 120_000_000]        # the last 2 min are a different game


def candidate_clause(kind, pos, px_c, entry_px_c=None, unreal_c=None, age_s=None,
                     improved_side=None) -> str:
    """The one place the candidate is put into words. serve.text_of calls this too."""
    if kind == "entry":
        return f"candidate {improved_side} at {px_c:.2f}c"
    return (f"candidate flatten {'long-yes' if pos > 0 else 'short-yes'} "
            f"entry {entry_px_c:.2f}c unrealized {unreal_c:+.2f}c age {age_s:.0f}s "
            f"exitable at {px_c:.2f}c")


def serialize(r) -> str:
    """The snapshot the model reads. Compact, fixed field order, no label leakage."""
    cl = candidate_clause(
        r.kind, r.pos_before, r.px_c, getattr(r, "entry_px_c", None),
        getattr(r, "unreal_c", None), getattr(r, "age_s", None),
        improved_side=("improved-ask" if r.s > 0 else "improved-bid"),
    )
    return (
        f"series {r.series} | ttc {r.ttc_s:.0f}s | mid {r.mid_c:.2f}c | "
        f"bid {r.bid_c:.2f}c x{int(r.bidsz)} | ask {r.ask_c:.2f}c x{int(r.asksz)} | "
        f"spread {r.spread_ticks:.0f} ticks | imbalance {r.imb:.2f} | "
        f"mom1s {r.mom1s_c:+.2f}c | mom5s {r.mom5s_c:+.2f}c | "
        f"vwap60 {r.vwap60_c:.2f}c | vwap-dev {r.vwap_dev_c:+.2f}c | "
        f"ma10 {r.ma10_c:.2f}c | ma60 {r.ma60_c:.2f}c | "
        f"sigma {r.sigma_c:.3f}c/sqrt-s | takervol60 {r.tvol60:.0f} | flow60 {r.flow60:+.2f} | "
        f"reservation {r.as_resv_c:.2f}c | quote-vs-reservation {r.as_edge_c:+.2f}c | "
        f"position {r.pos_before:+.0f} | {cl}"
    )


# the reproduction arm sees exactly these; unreal/age are NaN on entry rows, filled with 0 there
BASE_NUMERIC = ["mid_c", "bid_c", "ask_c", "bidsz", "asksz", "spread_ticks", "imb",
                "mom1s_c", "mom5s_c", "ttc_s", "pos_before", "px_c", "unreal_c", "age_s"]
# v2: the contract's own traded price (VWAP), time-weighted mid averages, realized vol, signed
# taker flow, and the A-S reservation-price terms. Everything signed by s where a direction exists.
CONTEXT_NUMERIC = ["vwap_c", "vwap60_c", "vwap_dev_c", "px_vwap_c", "ma10_c", "ma60_c",
                   "mid_ma10_c", "mid_ma60_c", "sigma_c", "tvol60", "flow60", "flow_adv60",
                   "as_skew_c", "as_resv_c", "as_edge_c", "as_room_c"]
NUMERIC = BASE_NUMERIC + CONTEXT_NUMERIC
