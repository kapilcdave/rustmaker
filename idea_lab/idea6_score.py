import json, math, numpy as np, sys
sys.path.insert(0,'/Users/kapil/proj/kalshi-mm15/idea_lab')
from common import day_cluster_se
L=json.load(open('idea6_ladder.json'))
O='/Users/kapil/proj/kalshi-scalp/data/oos_20261006'
M={int(m['close_ts']):m for m in json.load(open(f'{O}/candles_BTC.json'))['markets'] if m.get('result') in ('yes','no')}
rows={k:[] for k in (2,5,8,11)}
for Hs,v in L.items():
    H=int(Hs)
    if H not in M: continue
    m=M[H]; bars15={int(b['ts']):b for b in m['bars'] if not b.get('post_close')}; y=1.0 if m['result']=='yes' else 0.0
    K=v['K15']; lad=[]
    for s in v['ladder']:
        bb={b['ts']:b for b in s['bars'] if b['b'] is not None and b['a'] is not None}
        lad.append((s['strike'],bb))
    for k in rows:
        t=H-900+60*k; b15=bars15.get(t)
        if not b15 or b15['a'] is None or b15['b'] is None: continue
        pts=[(st,(bb[t]['a']+bb[t]['b'])/2,bb[t]['a']-bb[t]['b']) for st,bb in lad if t in bb]
        lo=[p for p in pts if p[0]<=K]; hi=[p for p in pts if p[0]>K]
        if not lo or not hi: continue
        a=max(lo,key=lambda p:p[0]); b=min(hi,key=lambda p:p[0])
        if max(a[2],b[2])>0.06: continue   # skip junk hourly quotes
        w=(K-a[0])/(b[0]-a[0]); lp=a[1]*(1-w)+b[1]*w
        rows[k].append((H//86400,y,(b15['a']+b15['b'])/2,lp,b15['a'],b15['b']))
out={}
for k,r in rows.items():
    if len(r)<30: continue
    A=np.array(r); d=A[:,0]; y=A[:,1]; mid=A[:,2]; lp=A[:,3]
    x=lp-mid; res=y-mid; sl=float((x*res).sum()/(x*x).sum()); e=res-sl*x
    u=np.unique(d); se=math.sqrt(sum(((x[d==dd]*e[d==dd]).sum())**2 for dd in u))/(x*x).sum()
    br=lambda p: round(float(np.mean((p-y)**2)),5)
    out[k]=dict(n=len(r),slope=round(sl,3),se=round(se,3),brier_15m=br(mid),brier_ladder=br(lp),brier_blend=br((mid+lp)/2),mean_abs_gap=round(float(np.abs(x).mean()),4))
    print(k,out[k])
json.dump(out,open('idea6_score.json','w'),indent=1)
