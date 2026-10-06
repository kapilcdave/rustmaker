"""Score PREREG_altspot_tilt_20261006 on the post-2026-09-14 window. One trade per market (first k that triggers)."""
import sys, json, math, os
import numpy as np
sys.path.insert(0,'/Users/kapil/proj/kalshi-mm15/idea_lab')
from common import norm_cdf, fee, day_cluster_se
O='/Users/kapil/proj/kalshi-scalp/data/oos_20261006'
START=1789357500  # 2026-09-14T03:45:00Z, preregistered inclusive start
END=1791244800    # 2026-10-06T00:00:00Z, exclusive (through Oct 5 UTC)
BASIS_BPS={'BTC':0.71,'ETH':0.64,'SOL':1.13,'XRP':1.23,'DOGE':1.07,'BNB':2.44,'HYPE':1.77,'NEAR':4.67,'ZEC':1.87}  # frozen from in-sample table
def num(x): return float(str(x).replace(',',''))
def run(s, th=0.10, ks=(2,3,5,8)):
    p=f'{O}/candles_{s}.json'
    if not os.path.exists(p): return None
    cb={int(b[0]):(b[3],b[4]) for b in json.load(open(f'{O}/cb_{s}-USD.json'))['bars']}
    ms=json.load(open(p))['markets']; P=[];D=[];N=0
    for m in ms:
        if m.get('result') not in ('yes','no') or m.get('floor_strike') in (None,'None') : continue
        t0=int(m['open_ts'])
        if not START <= t0 < END: continue
        K=num(m['floor_strike'])*math.exp(-BASIS_BPS[s]*1e-4); y=1.0 if m['result']=='yes' else 0.0
        cs=[cb[t0-60*i][1] for i in range(120,0,-1) if (t0-60*i) in cb]
        if len(cs)<40: continue
        sg=float(np.std(np.diff(np.log(cs))))
        if sg<2e-5: continue
        bars={int(b['ts']):b for b in m['bars'] if not b.get('post_close')}
        N+=1
        for k in ks:
            bar=bars.get(t0+60*k); c=cb.get(t0+60*(k-1))
            if not bar or not c or bar['a'] is None or bar['b'] is None: continue
            f=float(norm_cdf(math.log(c[1]/K)/(sg*math.sqrt(max(15-k,.5))))); a,b=bar['a'],bar['b']
            if f-a>th: P.append(y-a-fee(a)); D.append(t0//86400); break
            if b-f>th: P.append((1-y)-(1-b)-fee(1-b)); D.append(t0//86400); break
    return N,P,D
def pooled(names):
    P=[];D=[];per={}
    for s in names:
        r=run(s)
        if r is None: continue
        N,p,d=r; per[s]=(N,len(p),round(100*float(np.mean(p)),2) if p else None); P+=p; D+=d
    if len(P)>=30:
        mu,se=day_cluster_se(P,D); return dict(n=len(P),net_c=round(mu*100,3),se_c=round(se*100,3),lo95=round((mu-1.96*se)*100,3),per=per)
    return dict(n=len(P),per=per)
res={'primary':pooled(['NEAR','ZEC','HYPE']),'controls':pooled(['BTC','ETH','SOL','XRP']),'BNB':pooled(['BNB']),'DOGE':pooled(['DOGE'])}
print(json.dumps(res,indent=1)); json.dump(res,open('oos_score.json','w'),indent=1)
