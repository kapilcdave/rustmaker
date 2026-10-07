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

HS=[10,25,50,100,250,500,1000,2000,5000,15000,60000]
LS2=[0,2,5,8,10,15,25,50,100,250,1000]
TH2=0.05
close={h:{} for h in HS}   # group -> list of (fraction of gap closed by mid at t+h)
mark={L:{} for L in LS2}   # entry at arrival L, exit mid at +60s, gross c/ct
def add(d,g,v): d.setdefault(g,[]).append(v)
grp=lambda sym:'primary' if sym in('NEAR','ZEC','HYPE') else ('BNB' if sym=='BNB' else ('DOGE' if sym=='DOGE' else 'controls'))
nm=0
for tk,bk in Bg.items():
    if tk not in meta: continue
    ser=tk.split('-')[0]; sym=ser[2:-3]
    if ser not in Xg: continue
    m=meta[tk]; t0=ts(m['open_time']); t1=ts(m['close_time']); sg=sigma(sym,t0)
    if sg is None: continue
    K=float(str(m['floor_strike']).replace(',',''))*math.exp(-BASIS[sym]*1e-4)
    xs=Xg[ser]; xs=xs[(xs.rms>=(t0+60)*1000)&(xs.rms<=(t1-90)*1000)]
    if len(xs)<5 or len(bk)<10: continue
    nm+=1; g=grp(sym)
    rt=xs.rms.values; p=xs.p.values
    rem=np.maximum((t1*1000-rt)/60000.0,0.5); fair=norm_cdf(np.log(p/K)/(sg*np.sqrt(rem)))
    bt=bk.ven.values; bb=bk.a.values/1e4; ba=bk.c.values/1e4; mid=(bb+ba)/2
    def at(t): 
        i=np.searchsorted(bt,t,side='right')-1; i=np.clip(i,0,len(bt)-1); return i
    i0=at(rt); gap0=fair-mid[i0]
    trig=np.where((np.abs(gap0)>TH2)&(ba[i0]<0.99)&(bb[i0]>0.01))[0]
    # one trigger per 1-second bucket
    seen=set(); keep=[]
    for j in trig:
        b=int(rt[j]//1000)
        if b in seen: continue
        seen.add(b); keep.append(j)
    keep=np.array(keep,dtype=int)
    if keep.size==0: continue
    sgn=np.sign(gap0[keep])
    for h in HS:
        ih=at(rt[keep]+h); dm=(mid[ih]-mid[i0[keep]])*sgn
        add(close[h],g,(np.abs(gap0[keep]).sum(), dm.sum(), keep.size))
    ih=at(rt[keep]+60000)
    for L in LS2:
        ia=at(rt[keep]+L)
        yes=sgn>0
        gain=np.where(yes, mid[ih]-ba[ia], bb[ia]-mid[ih])
        add(mark[L],g,(gain.sum(),keep.size))
print('markets',nm)
out={'closed':{},'markout60':{}}
print('fraction of gap0 closed by book mid after h ms (sum dm / sum |gap0|):')
for h in HS:
    r={g:(round(sum(x[1] for x in v)/sum(x[0] for x in v),3),sum(x[2] for x in v)) for g,v in close[h].items()}
    out['closed'][h]=r; print(h,r)
print('gross markout (mid at +60s minus entry quote at arrival L), c/ct, before fee and exit spread:')
for L in LS2:
    r={g:(round(100*sum(x[0] for x in v)/sum(x[1] for x in v),3),sum(x[1] for x in v)) for g,v in mark[L].items()}
    out['markout60'][L]=r; print(L,r)
json.dump(out,open('tick_catchup.json','w'),indent=1)
