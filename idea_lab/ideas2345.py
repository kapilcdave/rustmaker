import sys, json, math, glob, datetime as dt
import numpy as np
sys.path.insert(0,'/Users/kapil/proj/kalshi-mm15/idea_lab')
from common import *
R = {}
cbs = {s: load_cb(s) for s in SERIES}
mk = {s: load_markets(s) for s in SERIES}
def phi(z): return math.exp(-z*z/2)/math.sqrt(2*math.pi)

# ---------- IDEA 4: value of tilting toward fair (maker signal), bucketed gap, k=2..11
rows=[]
for s in SERIES:
    cb=cbs[s]
    for m in mk[s]:
        sg=vol_min(cb,m['t0'])
        if sg is None: continue
        for k in (2,3,5,8):
            bar=m['bars'].get(m['t0']+60*k); c=cb.get(m['t0']+60*(k-1))
            if not bar or not c: continue
            rem=max(15-k,0.5); z=math.log(c[1]/m['K'])/(sg*math.sqrt(rem))
            mid=(bar['a']+bar['b'])/2
            rows.append((s,m['t0']//86400,k,m['y'],mid,float(norm_cdf(z)),bar['a']-bar['b']))
A=np.array([(r[1],r[2],r[3],r[4],r[5],r[6]) for r in rows]); ser=np.array([r[0] for r in rows])
gap=A[:,4]-A[:,3]; res=A[:,2]-A[:,3]
b4={}
for lo,hi in [(-1,-.15),(-.15,-.08),(-.08,-.03),(-.03,.03),(.03,.08),(.08,.15),(.15,1)]:
    sel=(gap>lo)&(gap<=hi)
    if sel.sum()>50:
        mu,se=day_cluster_se(res[sel],A[sel,0]); b4[f'{lo}:{hi}']=dict(n=int(sel.sum()),mean_gap=round(float(gap[sel].mean()),4),y_minus_mid=round(mu,4),se=round(se,4))
R['idea4_tilt_buckets']=b4
# per-series slope
ps={}
for s in SERIES:
    sel=ser==s
    x=gap[sel]; r_=res[sel]; ps[s]=dict(n=int(sel.sum()),slope=round(float((x*r_).sum()/(x*x).sum()),3))
R['idea4_slope_by_series']=ps

# ---------- IDEA 2: BTC lead. alts' next-minute mid change vs BTC-orthogonal return
btc=cbs['BTC']
out2={}
for s in [x for x in SERIES if x!='BTC']:
    cb=cbs[s]; X=[];Y=[];D=[];G=[]
    # fit beta own~btc on all minutes lazily per market using same minute
    for m in mk[s]:
        sg=vol_min(cb,m['t0'])
        if sg is None: continue
        for k in range(2,12):
            b0=m['bars'].get(m['t0']+60*k); b1=m['bars'].get(m['t0']+60*(k+1))
            c=cb.get(m['t0']+60*(k-1)); cp=cb.get(m['t0']+60*(k-2))
            bt=btc.get(m['t0']+60*(k-1))
            if not(b0 and b1 and c and cp and bt): continue
            rown=math.log(c[1]/c[0]); rbtc=math.log(bt[1]/bt[0])
            rem=max(15-k,0.5); z=math.log(c[1]/m['K'])/(sg*math.sqrt(rem))
            if sg<2e-5 or abs(z)>6: continue
            sens=phi(z)/(sg*math.sqrt(rem))
            mid0=(b0['a']+b0['b'])/2; mid1=(b1['a']+b1['b'])/2
            X.append((rown*sens, rbtc*sens, float(norm_cdf(z))-mid0)); Y.append(mid1-mid0); D.append(m['t0']//86400)
    X=np.array(X);Y=np.array(Y);D=np.array(D)
    ok=np.isfinite(X).all(1)&np.isfinite(Y); lim=np.percentile(np.abs(X[ok]),99.5,axis=0); ok&=(np.abs(X)<=lim).all(1); X,Y,D=X[ok],Y[ok],D[ok]
    # orthogonalise btc to own
    beta=(X[:,1]*X[:,0]).sum()/(X[:,0]**2).sum(); eb=X[:,1]-beta*X[:,0]
    M=np.column_stack([np.ones(len(Y)),X[:,0],eb,X[:,2]])
    coef,*_=np.linalg.lstsq(M,Y,rcond=None); r_=Y-M@coef
    # day-clustered SE (sandwich)
    XtXi=np.linalg.inv(M.T@M); u=np.unique(D); meat=np.zeros((4,4))
    for d in u:
        g=(M[D==d]*r_[D==d][:,None]).sum(0); meat+=np.outer(g,g)
    se=np.sqrt(np.diag(XtXi@meat@XtXi))
    out2[s]=dict(n=len(Y),own=round(coef[1],3),own_se=round(se[1],3),btc_orth=round(coef[2],3),btc_se=round(se[2],3),gap=round(coef[3],3),gap_se=round(se[3],3))
R['idea2_btc_lead']=out2

# ---------- IDEA 3: BTC/Coinbase vs BRTI-average basis. proxy K from CB prev-minute avg
bs={}
for s in SERIES:
    cb=cbs[s]; e=[];e2=[]
    for m in mk[s]:
        c=cb.get(m['t0']-60)
        if not c: continue
        prox=(c[0]+c[1])/2; e.append(math.log(m['K']/prox)*1e4)
        # settlement: CB last-minute avg vs A1
        c2=cb.get(m['t0']+840)
        if c2: e2.append(math.log(m['A1']/((c2[0]+c2[1])/2))*1e4)
    bs[s]=dict(n=len(e),strike_basis_bps_mean=round(float(np.mean(e)),3),sd=round(float(np.std(e)),3),settle_sd=round(float(np.std(e2)),3) if e2 else None)
R['idea3_basis_bps']=bs

# ---------- IDEA 5: hours/weekday. candles: spread, volume, slope by UTC hour; live ledger by hour
hr={}
for s in SERIES:
    for m in mk[s]:
        h=(m['t0']//3600)%24; b=m['bars'].get(m['t0']+60*5)
        if b: hr.setdefault(h,[]).append((b['a']-b['b'],b.get('v') or 0))
R['idea5_spread_by_utc_hour']={int(h):dict(n=len(v),spread=round(float(np.mean([x[0] for x in v])),4),vol=round(float(np.mean([x[1] for x in v])),0)) for h,v in sorted(hr.items())}
wd={}
for s in SERIES:
    for m in mk[s]:
        w=dt.datetime.utcfromtimestamp(m['t0']).weekday(); b=m['bars'].get(m['t0']+60*5)
        if b: wd.setdefault(w,[]).append(b['a']-b['b'])
R['idea5_spread_by_weekday']={int(w):round(float(np.mean(v)),4) for w,v in sorted(wd.items())}
pn={}; seen=set()
for f in sorted(glob.glob('/Users/kapil/proj/kalshi-mm15/data/box/live_penny/*settlements*.json'))+['/Users/kapil/proj/kalshi-mm15/data/box/live_penny/all_venue.json']:
    d=json.load(open(f))
    for st in d['settlements']:
        tk=st['ticker']
        if tk in seen: continue
        seen.add(tk)
        y=float(st['yes_count_fp']);n=float(st['no_count_fp'])
        pnl=st['revenue']/100+min(y,n)-float(st['yes_total_cost_dollars'])-float(st['no_total_cost_dollars'])-float(st['fee_cost'])
        t=dt.datetime.strptime(st['settled_time'][:19],'%Y-%m-%dT%H:%M:%S')
        pn.setdefault(t.hour,[]).append(pnl)
R['idea5_live_pnl_by_utc_hour']={int(h):dict(n=len(v),sum=round(sum(v),2),mean_c=round(100*float(np.mean(v)),2)) for h,v in sorted(pn.items())}
R['idea5_live_total']=dict(n=len(seen),sum=round(sum(sum(v) for v in pn.values()),2))
json.dump(R,open('ideas2345.json','w'),indent=1)
print(json.dumps(R,indent=1))
