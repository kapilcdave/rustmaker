"""Settle each fill and compare c/ct across order-history strata: WHERE did the loss come from?

Run `extract_fill_features.py <live_*.jsonl.gz>` on the box first (the journal is ~180 MB gzipped
and must stream), copy its output to data/fillfeat_<date>.jsonl, then `fetch_results.py` for the
tickers, then this.

Result on the 2026-10-09 run (1,144 fills, 1,085 ct, -1.843 c/ct = -$18.14, reconciles exactly):

    front of queue (level_before == 0)   n=1099   -1.378 c/ct
    behind in the queue  (> 0)           n=  39  -13.136 c/ct   t -2.05
    filled within 1s of posting          n= 837   -2.043 c/ct   (73% of all fills)
    rested > 10s before filling          n=  29  +14.828 c/ct   <- the ONLY positive bucket

⚠ OBSERVATIONAL, not randomised, and one run. The engine chose amend vs cancel+create for reasons
(partial fill, no order id, price off the grid, too few tokens), so `amends>=1` is a selected
population and this cannot price an amend-only RUN. Conditioning on time-to-fill conditions on an
outcome. Every split here is a lead to preregister, never a verdict
(`in-sample-controls-cannot-detect-selection`). What it CAN do is kill a branch cheaply, which is
what this corpus prefers to spend on one.
"""
import json, math, sys
from collections import defaultdict

res = json.load(open("data/leftover/results.json"))
try:
    for tk, r in json.load(open("data/settle_cache.json")).items():
        if tk not in res and r in ("yes", "no"):
            res[tk] = {"result": r}
except FileNotFoundError:
    pass

rows, unsettled = [], 0
for line in open("data/fillfeat_20261009.jsonl"):
    f = json.loads(line)
    r = res.get(f["ticker"])
    if not r or r.get("result") not in ("yes", "no"):
        unsettled += 1
        continue
    sgn = 1.0 if f["side"] == "bid" else -1.0
    y = 100.0 if r["result"] == "yes" else 0.0
    f["pnl_c"] = sgn * f["ct"] * (y - f["px_c"]) - f["fee_c"]
    rows.append(f)
print(f"settled {len(rows)} of {len(rows)+unsettled} fills  (dropped {unsettled} unsettled)\n")

def stat(label, sub):
    if not sub:
        print(f"  {label:34} n=0"); return
    ct = sum(r["ct"] for r in sub)
    per = [r["pnl_c"] / r["ct"] for r in sub]
    m = sum(per) / len(per)
    sd = math.sqrt(sum((x - m) ** 2 for x in per) / (len(per) - 1)) if len(per) > 1 else float("nan")
    se = sd / math.sqrt(len(per)) if len(per) > 1 else float("nan")
    tot = sum(r["pnl_c"] for r in sub)
    print(f"  {label:34} n={len(sub):>5} ct={ct:>6.0f} {m:>+7.3f} c/ct  se {se:>5.3f}"
          f"  t {m/se if se else float('nan'):>+6.2f}  total {tot/100:>+7.2f}$")

print("ALL")
stat("every settled fill", rows)
print("\nTHE AMEND QUESTION  (did the filled order get amended before it filled?)")
stat("amends == 0  (cancel+create path)", [r for r in rows if r["amends"] == 0])
stat("amends >= 1  (kept its id)", [r for r in rows if r["amends"] >= 1])
print("\nTHE UNDERCUT QUESTION  (was our quote undercut before it filled?)")
stat("undercuts == 0", [r for r in rows if r["undercuts"] == 0])
stat("undercuts >= 1", [r for r in rows if r["undercuts"] >= 1])
print("\nTHE QUEUE QUESTION  (depth ahead of us when we landed)")
stat("level_before == 0  (front)", [r for r in rows if r["level_before"] == 0])
stat("level_before >  0  (behind)", [r for r in rows if (r["level_before"] or 0) > 0])
stat("level_before unknown", [r for r in rows if r["level_before"] is None])
print("\nRESTING AGE AT FILL")
for lo, hi in ((0, 1000), (1000, 10000), (10000, 60000), (60000, 10**9)):
    stat(f"age {lo/1000:.0f}-{hi/1000:.0f}s" if hi < 10**9 else f"age >{lo/1000:.0f}s",
         [r for r in rows if r["age_ms"] is not None and lo <= r["age_ms"] < hi])

print("\nWHERE THE MONEY ACTUALLY IS  (front-of-queue x time to fill)")
for lvl, lname in ((0, "front"), (1, "behind")):
    for lo, hi in ((0, 1000), (1000, 10000), (10000, 10**9)):
        sub = [r for r in rows
               if ((r["level_before"] or 0) == 0 if lvl == 0 else (r["level_before"] or 0) > 0)
               and r["age_ms"] is not None and lo <= r["age_ms"] < hi]
        tag = f"{lname}, age {lo/1000:.0f}-{hi/1000:.0f}s" if hi < 10**9 else f"{lname}, age >{lo/1000:.0f}s"
        stat(tag, sub)

tot = sum(r["pnl_c"] for r in rows)
print(f"\nshare of the -{abs(tot)/100:.2f}$ by bucket:")
for name, pred in (
    ("front + filled within 1s", lambda r: (r["level_before"] or 0) == 0 and (r["age_ms"] or 0) < 1000),
    ("front + filled after 1s", lambda r: (r["level_before"] or 0) == 0 and (r["age_ms"] or 0) >= 1000),
    ("behind in the queue", lambda r: (r["level_before"] or 0) > 0 or r["level_before"] is None),
):
    s = sum(r["pnl_c"] for r in rows if pred(r))
    n = sum(1 for r in rows if pred(r))
    print(f"  {name:28} n={n:>5} ({n/len(rows)*100:>4.1f}% of fills)  {s/100:>+7.2f}$  "
          f"({s/tot*100:>5.1f}% of the loss)")
