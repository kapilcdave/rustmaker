"""Shared loaders for the 2026-10-06 idea battery (candle-level, offline)."""
import json, math, os, bisect
import numpy as np
D = '/Users/kapil/proj/kalshi-scalp/data'
SERIES = ['BTC','ETH','SOL','XRP','DOGE','BNB','HYPE','NEAR','ZEC']

def norm_cdf(x):
    return 0.5*(1+np.vectorize(math.erf)(np.asarray(x)/math.sqrt(2)))

def load_cb(sym):
    bars = {}
    for f in (f'cb_{sym.lower()}_1m.json', f'cb_{sym.lower()}_1m_full.json'):
        p = os.path.join(D, f)
        if os.path.exists(p):
            for b in json.load(open(p))['bars']:
                bars[int(b[0])] = (b[3], b[4], b[1], b[2])  # open, close, low, high
    return bars

def load_markets(sym):
    d = json.load(open(os.path.join(D, f'kx{sym.lower()}15m_candles.json')))
    out = []
    for m in d['markets']:
        if m.get('result') not in ('yes', 'no') or m.get('floor_strike') in (None,'None','') or m.get('expiration_value') in (None,'None',''): continue
        bars = {int(b['ts']): b for b in m['bars'] if not b.get('post_close')}
        out.append(dict(tk=m['ticker'], t0=int(m['open_ts']), K=float(str(m['floor_strike']).replace(',','')),
                        y=1.0 if m['result']=='yes' else 0.0, bars=bars,
                        A1=float(str(m['expiration_value']).replace(',',''))))
    return out

def vol_min(cb, t0, look=120):
    cs = []
    for i in range(look, 0, -1):
        b = cb.get(t0-60*i)
        if b: cs.append(b[1])
    if len(cs) < 40: return None
    r = np.diff(np.log(cs))
    return float(np.std(r)) or None

def fee(p):  # Kalshi taker fee per contract in dollars
    return math.ceil(0.07*p*(1-p)*100 - 1e-9)/100

def day_cluster_se(vals, days):
    vals = np.asarray(vals, float); days = np.asarray(days)
    u = np.unique(days)
    tot = np.array([vals[days==d].sum() for d in u]); n = vals.size
    m = vals.mean()
    resid = np.array([ (vals[days==d]-m).sum() for d in u])
    return m, math.sqrt((resid**2).sum())/n
