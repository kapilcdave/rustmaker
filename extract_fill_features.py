"""One streaming pass over a live journal -> one record per fill with its order history.

Rows are time-ordered, so the counters snapshotted at the fill are strictly BEFORE it.
Only per-coid counters are held (~37k), never the 8.6M book rows.
"""
import gzip, json, sys
from collections import defaultdict

st = defaultdict(lambda: {"amends": 0, "undercuts": 0, "level": None, "px0": None,
                          "t0": None, "namend_px": 0})
out = []
with gzip.open(sys.argv[1], "rt") as f:
    for line in f:
        try: r = json.loads(line)
        except Exception: continue
        k = r.get("k"); v = r.get("v")
        if k in ("B", "T"):      # book/trade tape: never stored
            continue
        if k == "new":
            c = (v.get("body") or {}).get("client_order_id")
            if c:
                s = st[c]; s["t0"] = r["t"]
                s["px0"] = (v.get("body") or {}).get("yes_price")
        elif k == "amend":
            if v.get("coid"): st[v["coid"]]["amends"] += 1
        elif k == "undercut":
            if v.get("coid"): st[v["coid"]]["undercuts"] += 1
        elif k == "own_delta":
            c = v.get("coid")
            if c and float(v.get("delta") or 0) > 0:   # our order APPEARING at a level
                st[c]["level"] = v.get("level_before")
        elif k == "fill":
            c = v.get("client_order_id"); s = st.get(c, {})
            out.append({
                "coid": c, "ticker": v["market_ticker"], "side": v["book_side"],
                "ct": float(v["count_fp"]), "px_c": float(v["yes_price_dollars"]) * 100.0,
                "ts_ms": v["ts_ms"], "taker": bool(v.get("is_taker")),
                "fee_c": float(v.get("fee_cost") or 0) * 100.0,
                "amends": s.get("amends", 0), "undercuts": s.get("undercuts", 0),
                "level_before": s.get("level"),
                "age_ms": (r["t"] - s["t0"]) / 1000.0 if s.get("t0") else None,
            })
for o in out:
    print(json.dumps(o, separators=(",", ":")))
print(f"# fills {len(out)}  orders tracked {len(st)}", file=sys.stderr)
