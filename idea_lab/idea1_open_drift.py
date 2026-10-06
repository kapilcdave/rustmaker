"""IDEA 1: is the 15M book efficient to (spot - strike) in the first minutes after open?
Fair = Phi(ln(S_k/K) / (sigma_min*sqrt(rem))). Tests mid vs fair at end of minute k."""
import sys, json, math
import numpy as np
sys.path.insert(0, '/Users/kapil/proj/kalshi-mm15/idea_lab')
from common import *
KS = [1,2,3,5,8,11]
rows = {k: [] for k in KS}
for s in SERIES:
    cb = load_cb(s)
    for m in load_markets(s):
        sg = vol_min(cb, m['t0'])
        if sg is None: continue
        for k in KS:
            bar = m['bars'].get(m['t0']+60*k)
            c = cb.get(m['t0']+60*(k-1))
            if not bar or not c or bar['a'] is None or bar['b'] is None: continue
            S = c[1]; rem = max(15-k, 0.5)
            z = math.log(S/m['K'])/(sg*math.sqrt(rem))
            rows[k].append((s, m['t0']//86400, m['y'], bar['b'], bar['a'], z, m['t0']))
out = {}
for k in KS:
    r = rows[k]
    if not r: continue
    ser = np.array([x[0] for x in r]); day = np.array([x[1] for x in r])
    y = np.array([x[2] for x in r]); b = np.array([x[3] for x in r]); a = np.array([x[4] for x in r])
    z = np.array([x[5] for x in r]); mid = (a+b)/2; fair = norm_cdf(z)
    br = lambda p: float(np.mean((p-y)**2))
    # slope of (y-mid) on (fair-mid), day-clustered
    x = fair-mid; r_ = y-mid
    slope = float((x*r_).sum()/(x*x).sum())
    res = r_ - slope*x
    u = np.unique(day); sc = np.array([(x[day==d]*res[day==d]).sum() for d in u])
    se = math.sqrt((sc**2).sum())/(x*x).sum()
    # taker rule: buy yes at ask if fair-ask>th ; buy no at 1-bid if bid-fair>th
    res_t = {}
    for th in (0.02, 0.05, 0.10):
        pnl = []; dd = []
        for i in range(len(y)):
            if fair[i]-a[i] > th: pnl.append(y[i]-a[i]-fee(a[i])); dd.append(day[i])
            elif b[i]-fair[i] > th: pnl.append((1-y[i])-(1-b[i])-fee(1-b[i])); dd.append(day[i])
        if len(pnl) > 30:
            mu, se2 = day_cluster_se(pnl, dd)
            res_t[th] = dict(n=len(pnl), net=round(mu*100,3), se=round(se2*100,3))
    out[k] = dict(n=len(y), brier_mid=round(br(mid),5), brier_fair=round(br(fair),5),
                  brier_blend=round(br(0.5*mid+0.5*fair),5), slope=round(slope,4), slope_se=round(se,4),
                  taker=res_t)
    print(k, json.dumps(out[k]))
json.dump(out, open('/Users/kapil/proj/kalshi-mm15/idea_lab/idea1_open_drift.json','w'), indent=1)
