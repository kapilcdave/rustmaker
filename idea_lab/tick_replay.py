"""Tick-level replay of the spot-gap taker on the 12 h 9-series tape (2026-09-26/27 box tape with spot rows 'X').
Signal at every spot tick (our RECEIVE time); our IOC lands L ms later and fills at the Kalshi top of book as of arrival (venue clock).
L is the whole post-receipt path: decide + sign + order transit/ingest. Ed25519 vs RSA-PSS moves L by about 1 ms; the sweep shows what 1 ms is worth."""
import sys, json, math, numpy as np, pandas as pd
sys.path.insert(0,'/Users/kapil/proj/kalshi-mm15/idea_lab')
from common import norm_cdf, fee
O='/Users/kapil/proj/kalshi-scalp/data/oos_20261006'
TAPE='/Users/kapil/proj/kalshi-mm15/data/box/shadow_spot/tape.csv.gz'
BASIS={'BTC':0.71,'ETH':0.64,'SOL':1.13,'XRP':1.23,'DOGE':1.07,'BNB':2.44,'HYPE':1.77,'NEAR':4.67,'ZEC':1.87}
LS=[0,2,5,8,10,15,25,50,100,250,500,1000,2000,5000,15000,60000]
TH=[0.05,0.10]
df=pd.read_csv(TAPE,dtype={'a':str,'b':str,'c':str,'d':str})
B=df[df.kind=='B'].copy(); X=df[df.kind=='X'].copy()
for c in 'abcd': B[c]=pd.to_numeric(B[c],errors='coerce')
B=B.dropna(subset=['a','c']); B['ven']=B.venue_ms.astype('int64')/1000.0  # B venue stamps are MICROseconds; X are ms
print('B venue-vs-recv lag ms p10/50/90',np.percentile(B.recv_us/1000.0-B.ven,[10,50,90]).round(1))
X['p']=pd.to_numeric(X.a); X['rms']=X.recv_us.astype('int64')/1000.0; X['ven']=X.venue_ms.astype('int64')
lag=(X.rms-X.ven); print('spot feed lag recv-venue ms: p10/50/90', np.percentile(lag,[10,50,90]).round(1))
S=json.load(open(f'{O}/strikes.json'))
meta={}
for ser,rows in S.items():
    for r in rows:
        if r.get('result') in ('yes','no') and r.get('floor_strike') not in (None,'None'): meta[r['ticker']]=r
cbc={}
def sigma(sym,t0):
    if sym not in cbc: cbc[sym]={int(b[0]):b[4] for b in json.load(open(f'{O}/cb_{sym}-USD.json'))['bars']}
    cs=[cbc[sym][t0-60*i] for i in range(120,0,-1) if (t0-60*i) in cbc[sym]]
    if len(cs)<40: return None
    sg=float(np.std(np.diff(np.log(cs)))); return sg if sg>2e-5 else None
import datetime as dt
def ts(s): return int(dt.datetime.strptime(s,'%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=dt.timezone.utc).timestamp())
Xg={k:v for k,v in X.groupby('ticker')}; Bg={k:v.sort_values('ven') for k,v in B.groupby('ticker')}
res={(L,th):[] for L in LS for th in TH}
per={}
nm=0
for tk,bk in Bg.items():
    if tk not in meta: continue
    ser=tk.split('-')[0]; sym=ser[2:-3]
    if ser not in Xg: continue
    m=meta[tk]; t0=ts(m['open_time']); t1=ts(m['close_time']); y=1.0 if m['result']=='yes' else 0.0
    sg=sigma(sym,t0)
    if sg is None: continue
    K=float(str(m['floor_strike']).replace(',',''))*math.exp(-BASIS[sym]*1e-4)
    xs=Xg[ser]; xs=xs[(xs.rms>=(t0+60)*1000)&(xs.rms<=(t1-30)*1000)]
    if len(xs)<5 or len(bk)<10: continue
    nm+=1
    rt=xs.rms.values; p=xs.p.values
    rem=np.maximum((t1*1000-rt)/60000.0,0.5)
    fair=norm_cdf(np.log(p/K)/(sg*np.sqrt(rem)))
    bt=bk.ven.values; bb=bk.a.values/10000.0; ba=bk.c.values/10000.0; bs=bk.b.values; asz=bk.d.values
    for L in LS:
        idx=np.searchsorted(bt,rt+L,side='right')-1
        ok=idx>=0
        idx=np.where(ok,idx,0)
        ask=ba[idx]; bid=bb[idx]
        for th in TH:
            gy=ok&(fair-ask>th)&(ask<0.99)&(asz[idx]>0)
            gn=ok&(bid-fair>th)&(bid>0.01)&(bs[idx]>0)
            hit=np.where(gy|gn)[0]
            if len(hit)==0: continue
            i=hit[0]
            if gy[i]: pnl=y-ask[i]-fee(ask[i])
            else: pnl=(1-y)-(1-bid[i])-fee(1-bid[i])
            res[(L,th)].append((sym,pnl))
print('markets scored',nm)
GR={'primary':['NEAR','ZEC','HYPE'],'BNB':['BNB'],'DOGE':['DOGE'],'controls':['BTC','ETH','SOL','XRP']}
out={}
for th in TH:
    print('--- th',th)
    for L in LS:
        row={}
        for g,names in GR.items():
            v=[x[1] for x in res[(L,th)] if x[0] in names]
            row[g]=(len(v),round(100*float(np.mean(v)),2) if v else None, round(100*float(np.std(v)/math.sqrt(len(v))),2) if len(v)>1 else None)
        out[f'{th}_{L}']=row; print(L,row)
json.dump(out,open('tick_replay.json','w'),indent=1)
