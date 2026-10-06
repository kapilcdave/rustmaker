"""Per-series taker test of the spot-vs-strike gap, time-split (H1 fit threshold, H2 score), basis-corrected K."""
import sys, json, math
import numpy as np
sys.path.insert(0,'/Users/kapil/proj/kalshi-mm15/idea_lab')
from common import *
out={}
for s in SERIES:
    cb=load_cb(s); ms=load_markets(s)
    bas=[math.log(m['K']/((cb[m['t0']-60][0]+cb[m['t0']-60][1])/2)) for m in ms if cb.get(m['t0']-60)]
    b0=float(np.mean(bas)) if bas else 0.0
    t_split=np.median([m['t0'] for m in ms])
    res={}
    for corr in (False,True):
        for k in (2,3,5,8):
            for th in (0.05,0.10):
                P={0:[],1:[]};Dd={0:[],1:[]}
                for m in ms:
                    sg=vol_min(cb,m['t0']); bar=m['bars'].get(m['t0']+60*k); c=cb.get(m['t0']+60*(k-1))
                    if sg is None or sg<2e-5 or not bar or not c: continue
                    K=m['K']*(math.exp(-b0) if corr else 1.0)
                    z=math.log(c[1]/K)/(sg*math.sqrt(max(15-k,.5))); f=float(norm_cdf(z)); a,b=bar['a'],bar['b']
                    h=0 if m['t0']<t_split else 1
                    if f-a>th: P[h].append(m['y']-a-fee(a)); Dd[h].append(m['t0']//86400)
                    elif b-f>th: P[h].append((1-m['y'])-(1-b)-fee(1-b)); Dd[h].append(m['t0']//86400)
                for h in (0,1):
                    if len(P[h])>=30:
                        mu,se=day_cluster_se(P[h],Dd[h]); res[f'corr{int(corr)}_k{k}_th{th}_H{h+1}']=(len(P[h]),round(mu*100,2),round(se*100,2))
    out[s]=dict(basis_bps=round(b0*1e4,2),res=res)
    pos=[v for kk,v in res.items() if kk.startswith('corr1') and kk.endswith('H2')]
    print(s,'basis',round(b0*1e4,2),'| corr1 H2 cells:',pos)
json.dump(out,open('idea1b_series_split.json','w'),indent=1)
