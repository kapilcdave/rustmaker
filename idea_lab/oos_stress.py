import sys, json, math, os
import numpy as np
sys.path.insert(0,'/Users/kapil/proj/kalshi-mm15/idea_lab')
from common import norm_cdf, fee, day_cluster_se
from oos_score import BASIS_BPS, num, O
def run(s, mode, th=0.10, ks=(2,3,5,8)):
    cb={int(b[0]):(b[3],b[4]) for b in json.load(open(f'{O}/cb_{s}-USD.json'))['bars']}
    P=[];D=[];ent=[]
    for m in json.load(open(f'{O}/candles_{s}.json'))['markets']:
        if m.get('result') not in ('yes','no') or m.get('floor_strike') in (None,'None'): continue
        t0=int(m['open_ts']); K=num(m['floor_strike'])*math.exp(-BASIS_BPS[s]*1e-4); y=1.0 if m['result']=='yes' else 0.0
        cs=[cb[t0-60*i][1] for i in range(120,0,-1) if (t0-60*i) in cb]
        if len(cs)<40: continue
        sg=float(np.std(np.diff(np.log(cs))))
        if sg<2e-5: continue
        bars={int(b['ts']):b for b in m['bars'] if not b.get('post_close')}
        for k in ks:
            bar=bars.get(t0+60*k); c=cb.get(t0+60*(k-1))
            if not bar or not c or bar['a'] is None or bar['b'] is None: continue
            f=float(norm_cdf(math.log(c[1]/K)/(sg*math.sqrt(max(15-k,.5))))); a,b=bar['a'],bar['b']
            side=None
            if f-a>th: side='Y'
            elif b-f>th: side='N'
            if not side: continue
            nb=bars.get(t0+60*(k+1))
            if mode=='close': pa,pb=a,b
            elif mode=='worst': pa,pb=(bar.get('ah') or a),(bar.get('bl') if bar.get('bl') is not None else b)
            elif mode=='next':
                if not nb or nb['a'] is None or nb['b'] is None: break
                pa,pb=nb['a'],nb['b']
            if side=='Y': P.append(y-pa-fee(pa)); ent.append((pa,pa-b))
            else: P.append((1-y)-(1-pb)-fee(1-pb)); ent.append((1-pb,a-pb))
            D.append(t0//86400); break
    return P,D,ent
res={}
for name,names in [('primary',['NEAR','ZEC','HYPE']),('BNB',['BNB']),('controls',['BTC','ETH','SOL','XRP'])]:
    for mode in ('close','worst','next'):
        P=[];D=[];E=[]
        for s in names:
            p,d,e=run(s,mode); P+=p;D+=d;E+=e
        mu,se=day_cluster_se(P,D)
        r=dict(n=len(P),net_c=round(mu*100,2),lo95=round((mu-1.96*se)*100,2))
        if mode=='close':
            D_=np.array(D);P_=np.array(P);u=np.unique(D_)
            tot=np.array([P_[D_==d].sum() for d in u]);r['days']=len(u);r['top5_days_share']=round(float(np.sort(tot)[-5:].sum()/tot.sum()),2);r['pos_day_frac']=round(float((tot>0).mean()),2)
            r['avg_entry_px']=round(float(np.mean([e[0] for e in E])),3);r['avg_spread']=round(float(np.mean([e[1] for e in E])),3)
            h=np.array(D)<np.median(D);r['H1']=round(100*float(P_[h].mean()),2);r['H2']=round(100*float(P_[~h].mean()),2)
        res[f'{name}_{mode}']=r; print(name,mode,r)
json.dump(res,open('oos_stress.json','w'),indent=1)
