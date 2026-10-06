"""IDEA 6 data: for each hour H (close_time) fetch the KXBTCD hourly ladder strikes bracketing the :45-:00 KXBTC15M strike,
1-min candles over the last 15 minutes of the hour. Public endpoints only."""
import sys, json, time, datetime as dt
sys.path.insert(0,'/Users/kapil/proj/kalshi-scalp')
import fetch_15m_candles as F
S=json.load(open('/Users/kapil/proj/kalshi-scalp/data/oos_20261006/strikes.json'))['KXBTC15M']
def ts(s): return F.parse_iso(s)
k15={ts(r['close_time']):float(str(r['floor_strike']).replace(',','')) for r in S if r.get('floor_strike') not in (None,'None')}
lo=ts('2026-09-22T00:00:00Z'); hi=ts('2026-10-05T23:00:00Z')
# list hourly markets
rows=[];cur=''
while True:
    d=F.get(f"/markets?series_ticker=KXBTCD&status=settled&min_close_ts={lo}&max_close_ts={hi}&limit=1000"+(f"&cursor={cur}" if cur else ''))
    if not d or d is F.MISSING: break
    rows+=d['markets']; cur=d.get('cursor') or ''
    if not cur: break
    time.sleep(0.6)
print('hourly markets',len(rows),flush=True)
byH={}
for r in rows: byH.setdefault(ts(r['close_time']),[]).append(r)
out={}
for H,ms in sorted(byH.items()):
    if H not in k15: continue
    K=k15[H]; ms=[m for m in ms if m.get('floor_strike') is not None]; ms.sort(key=lambda m:float(m['floor_strike']))
    below=[m for m in ms if float(m['floor_strike'])<=K][-3:]; above=[m for m in ms if float(m['floor_strike'])>K][:3]
    sel=[]
    for m in below+above:
        d=F.get(f"/series/KXBTCD/markets/{m['ticker']}/candlesticks?start_ts={H-900-60}&end_ts={H+60}&period_interval=1")
        bars=[]
        if d and d is not F.MISSING:
            for c in d.get('candlesticks') or []:
                t=c.get('end_period_ts'); yb=c.get('yes_bid') or {}; ya=c.get('yes_ask') or {}
                bars.append(dict(ts=t,b=F.to_f(yb.get('close_dollars')),a=F.to_f(ya.get('close_dollars'))))
        sel.append(dict(strike=float(m['floor_strike']),result=m.get('result'),bars=bars))
        time.sleep(0.6)
    out[H]=dict(K15=K,ladder=sel)
    if len(out)%25==0:
        print(len(out),'hours',flush=True); json.dump(out,open('idea6_ladder.json','w'))
json.dump(out,open('idea6_ladder.json','w')); print('done',len(out))
