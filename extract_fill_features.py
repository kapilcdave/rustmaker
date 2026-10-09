#!/usr/bin/env python3
"""One streaming pass over a live journal -> one record per fill, with the order's own history.

Run on the box: the 8 h journal is ~180 MB gzipped / 10.4M rows, of which 8.6M are the `B` touch
tape and 1.45M the `T` trade tape. Neither is ever stored; only per-coid state (~37k orders) is.

    python3 -I extract_fill_features.py data/live_penny/live_*.jsonl.gz > fillfeat.jsonl

Emits, per fill: the amend/undercut history of the filled order, the venue's own `level_before`
(depth ahead of us when our order joined its level), time from post to fill, and the PLACEMENT --
where our price sat relative to the touch when we posted.

Placement is the point of this script. `B` rows are `[ticker, vt, yes_bid, bid_size, yes_ask,
ask_size]`, i.e. TOUCH ONLY -- there is no depth away from the touch anywhere in the journal, so a
general "is someone already at my price" question is not answerable. But the only two cases a
penny-jump seat can be in are answerable exactly:

    improved  -- we posted INSIDE the touch, so we created the level and are alone in it
    joined    -- we posted AT the touch, so we are behind whatever size was already resting
    behind    -- we posted worse than the touch (should not happen for this strategy)

`level_before` from the venue is an INDEPENDENT measurement of the same thing, so the two can be
cross-tabulated: improved should be level 0, joined should be level > 0. Disagreement means the
touch moved between our decision and the venue accepting the order, which is itself the quantity
of interest.

⚠ Our own fill prints on the public `T` tape, and if that print is received before the user-fills
row then any "time since last print" is ~0 by construction and measures us, not the market
(`a-zero-lag-flow-feature-reads-its-own-sweep`). `last_print` carries the print's side/price/size
so that confound stays testable; on the 2026-10-09 run 59.5% of sub-millisecond gaps were our own
fill.
"""
from __future__ import annotations

import gzip
import json
import sys
from collections import defaultdict

TICK_C = 1.0  # cent grid; the tapered deci-cent wings are finer but these fills are cent-regime


def classify(side, px_c, touch):
    """Where our price sat relative to the touch when the order went on. None if no book yet."""
    if touch is None:
        return None
    bid, _bsz, ask, _asz = touch
    if side == "bid":
        if bid is None:
            return "no_bid"
        if px_c > bid + 1e-9:
            return "improved"
        if abs(px_c - bid) < 1e-9:
            return "joined"
        return "behind"
    if ask is None:
        return "no_ask"
    if px_c < ask - 1e-9:
        return "improved"
    if abs(px_c - ask) < 1e-9:
        return "joined"
    return "behind"


def main(path):
    st = defaultdict(lambda: {"amends": 0, "undercuts": 0, "level": None, "t0": None,
                              "px_c": None, "side": None, "place": None, "touch": None,
                              "place_amend": None})
    touch = {}                      # ticker -> (bid, bid_sz, ask, ask_sz), from the B tape
    last_T, prev_T = {}, {}
    prints = defaultdict(list)
    out = []

    with gzip.open(path, "rt") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            k, v = r.get("k"), r.get("v")

            if k == "B":            # 8.6M rows: update the touch, store nothing
                # ⚠ UNITS. The B tape carries price in 1/100 CENT (2000/2400 = 20.00c/24.00c,
                # verified against the same instant's `new` row mid of 22.0), while a fill's
                # `yes_price_dollars` is dollars and `amend` carries 1/100 cent. Comparing the
                # tape to a fill price without this divide makes every bid look "behind" and
                # every ask "improved" -- i.e. it silently relabels placement as SIDE.
                touch[v[0]] = (v[2] / 100.0, v[3], v[4] / 100.0, v[5])
                continue
            if k == "T":
                tk = v[0]
                if tk in last_T:
                    prev_T[tk] = last_T[tk]
                last_T[tk] = (r["t"], v[2], v[3], v[4])
                p = prints[tk]
                p.append(r["t"])
                if len(p) > 256:
                    del p[:-128]
                continue

            if k == "new":
                b = v.get("body") or {}
                c = b.get("client_order_id")
                if not c:
                    continue
                s = st[c]
                s["t0"] = r["t"]
                s["side"] = b.get("side")
                s["px_c"] = float(b["price"]) * 100.0 if b.get("price") else None
                s["touch"] = touch.get(b.get("ticker"))
                s["place"] = classify(s["side"], s["px_c"], s["touch"])
            elif k == "amend":
                c = v.get("coid")
                if not c:
                    continue
                s = st[c]
                s["amends"] += 1
                # An amend moves the price, so the placement question is asked again at the new one.
                if v.get("price") is not None:
                    s["px_c"] = float(v["price"]) / 100.0
                    s["place_amend"] = classify(s["side"], s["px_c"], s["touch"])
            elif k == "undercut":
                if v.get("coid"):
                    st[v["coid"]]["undercuts"] += 1
            elif k == "own_delta":
                c = v.get("coid")
                if c and float(v.get("delta") or 0) > 0:
                    st[c]["level"] = v.get("level_before")
            elif k == "fill":
                c = v.get("client_order_id")
                s = st.get(c, {})
                tk = v["market_ticker"]
                out.append({
                    "coid": c, "ticker": tk, "side": v["book_side"],
                    "ct": float(v["count_fp"]), "px_c": float(v["yes_price_dollars"]) * 100.0,
                    "ts_ms": v["ts_ms"], "taker": bool(v.get("is_taker")),
                    "fee_c": float(v.get("fee_cost") or 0) * 100.0,
                    "amends": s.get("amends", 0), "undercuts": s.get("undercuts", 0),
                    "level_before": s.get("level"),
                    "age_ms": (r["t"] - s["t0"]) / 1000.0 if s.get("t0") else None,
                    "place": s.get("place_amend") or s.get("place"),
                    "place_at_post": s.get("place"),
                    "post_px_c": s.get("px_c"),
                    "touch_at_post": s.get("touch"),
                    "since_print_ms": (r["t"] - last_T[tk][0]) / 1000.0 if tk in last_T else None,
                    "last_print": list(last_T.get(tk, (None,) * 4))[1:],
                    "since_prev_print_ms": (r["t"] - prev_T[tk][0]) / 1000.0 if tk in prev_T else None,
                    "prints_1s": sum(1 for x in prints[tk] if r["t"] - x <= 1_000_000),
                })

    for o in out:
        print(json.dumps(o, separators=(",", ":")))
    print(f"# fills {len(out)}  orders tracked {len(st)}", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1])
