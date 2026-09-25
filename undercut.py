import json, bisect, sys, collections
from array import array
path = sys.argv[1]
mids = collections.defaultdict(lambda: (array('q'), array('d')))
new, under, fills = {}, {}, []
with open(path) as f:
    for line in f:
        k = line[6:16]
        if k.startswith('B"'):
            r = json.loads(line); tk, vt, b, _, a, _ = r["v"]
            t, m = mids[tk]; t.append(vt); m.append((b + a) / 200.0)
        elif k.startswith('new"'):
            r = json.loads(line); v = r["v"]; new[v["coid"]] = (v["body"]["ticker"], v["body"]["side"], float(v["body"]["price"]) * 100)
        elif k.startswith('undercut"'):
            r = json.loads(line); v = r["v"]; under.setdefault(v["coid"], v["vt"])
        elif k.startswith('fill"'):
            r = json.loads(line); v = r["v"]; fills.append((v.get("client_order_id"), v["ts_ms"] * 1000, float(v["count_fp"])))
def mid_at(tk, t):
    ts, ms = mids[tk]; i = bisect.bisect_right(ts, t) - 1
    return ms[i] if i >= 0 else None
agg = collections.defaultdict(lambda: [0, 0.0, 0.0, 0.0])
for coid, tf, ct in fills:
    if coid not in new: continue
    tk, side, px = new[coid]; s = 1.0 if side == "ask" else -1.0
    m5, m60 = mid_at(tk, tf + 5_000_000), mid_at(tk, tf + 60_000_000)
    if m5 is None or m60 is None: continue
    key = "undercut BEFORE fill" if coid in under and under[coid] <= tf else "not undercut before fill"
    a = agg[key]; a[0] += 1; a[1] += ct; a[2] += s * (px - m5) * ct; a[3] += s * (px - m60) * ct
tot_orders = len(new); und = len(under)
print(f"orders posted {tot_orders}, of which undercut while resting {und} ({und/max(tot_orders,1):.1%})")
for k, (n, ct, c5, c60) in agg.items():
    print(f"  {k:26s} fills={n:5d} contracts={ct:7.1f}  mk5s={c5/ct:+.3f} c/ct  mk60s={c60/ct:+.3f} c/ct")
