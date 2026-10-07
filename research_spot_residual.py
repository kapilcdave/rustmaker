"""Offline 15-minute crypto maker diagnostic; never places orders.

Frozen screen: a 1s anchor, trailing 60s realized spot volatility, logistic
probability adjustment (scale 1.6), fair-edge thresholds 0, 0.5, 1 cent.
This is a signal screen, NOT a counterfactual execution backtest. Filtering
existing simulated fills ignores the queue/inventory effects of cancellation.
Public prints belong to incumbent makers, not necessarily to us.
"""
import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


def close_us(ticker):
    dt = datetime.strptime(ticker.split('-')[1], '%y%b%d%H%M')
    return int(dt.replace(tzinfo=ZoneInfo('America/New_York')).timestamp() * 1e6)


def feature_rows(df, delay_us, series):
    spot = df[(df.kind == 'X') & (df.ticker == series)].copy()
    spot['px'] = pd.to_numeric(spot.a)
    spot = spot.sort_values('recv_us').drop_duplicates('recv_us', keep='last')
    if spot.empty:
        raise ValueError(f'Tape has no {series} spot observations')
    xt = spot.recv_us.to_numpy(dtype=np.int64)
    xp = spot.px.to_numpy(dtype=float)
    # Regular-grid returns avoid overweighting a busy spot message stream.
    grid = np.arange(xt[0] + 1_000_000, xt[-1], 1_000_000, dtype=np.int64)
    xi = np.searchsorted(xt, grid, side='right') - 1
    logp = np.log(xp[xi])
    rv = pd.Series(np.r_[np.nan, np.diff(logp)] ** 2).rolling(60, min_periods=60).mean().to_numpy()
    fresh_grid = grid - xt[xi] <= 2_000_000
    # Volatility is invalid if any sample in its estimation window is stale.
    vol_ok = pd.Series(fresh_grid.astype(int)).rolling(61, min_periods=61).min().to_numpy() == 1
    out = []
    for ticker, m in df[df.ticker.str.startswith(series + '-')].groupby('ticker'):
        b = m[m.kind == 'B'].sort_values('recv_us').copy()
        if len(b) < 2:
            continue
        for c in 'abcd':
            b[c] = pd.to_numeric(b[c])
        b = b[(b.a > 0) & (b.c > b.a) & (b.c < 10000)]
        if len(b) < 2:
            continue
        bt = b.recv_us.to_numpy(dtype=np.int64)
        bm = (b.a.to_numpy() + b.c.to_numpy()) / 200.0
        ba = b.a.to_numpy() / 100.0
        bc = b.c.to_numpy() / 100.0
        for kind in ['T', 'F']:
            f = m[m.kind == kind].copy()
            if f.empty:
                continue
            # F prices are hypothetical fills produced by the existing queue engine.
            vt = f.venue_ms.to_numpy(dtype=np.int64)
            # Public trades have millisecond timestamps: add 1ms timing buffer.
            decision = vt - delay_us - 1000
            i = np.searchsorted(bt, decision, side='right') - 1
            ia = np.searchsorted(bt, decision - 1_000_000, side='right') - 1
            sx = np.searchsorted(xt, decision, side='right') - 1
            sa = np.searchsorted(xt, decision - 1_000_000, side='right') - 1
            vg = np.searchsorted(grid, decision, side='right') - 1
            valid = (i >= 0) & (ia >= 0) & (sx >= 0) & (sa >= 0) & (vg >= 0)
            i, ia, sx, sa, vg = [np.maximum(v, 0) for v in [i, ia, sx, sa, vg]]
            ttc = (close_us(ticker) - decision) / 1e6
            valid &= (ttc > 120) & (bm[i] >= 15) & (bm[i] <= 85)
            valid &= (decision - xt[sx] <= 2_000_000) & (decision - 1_000_000 - xt[sa] <= 2_000_000)
            valid &= vol_ok[vg] & np.isfinite(rv[vg]) & (rv[vg] > 0)
            anchor = np.clip(bm[ia] / 100, .001, .999)
            denom = np.sqrt(rv[vg] * np.maximum(ttc, 1))
            z = np.log(anchor / (1 - anchor)) + 1.6 * np.log(xp[sx] / xp[sa]) / np.maximum(denom, 1e-12)
            fair = 100 / (1 + np.exp(-np.clip(z, -30, 30)))
            if kind == 'T':
                side = np.where(f.a.to_numpy() == 'yes', 1, -1)
                price = pd.to_numeric(f.b).to_numpy() / 100
                count = pd.to_numeric(f.c).to_numpy()
                strat = np.repeat('public_prints', len(f))
            else:
                side = np.where(f.b.to_numpy() == 'ask', 1, -1)
                price = pd.to_numeric(f.c).to_numpy() / 100
                count = pd.to_numeric(f.d).to_numpy()
                strat = f.a.to_numpy()
            # Gate must use the known touch, never a future execution price.
            quote = np.where(side > 0, bc[i], ba[i])
            edge = side * (quote - fair)
            d = pd.DataFrame(dict(ticker=ticker, vt=vt, recv_us=f.recv_us.to_numpy(), strat=strat, count=count,
                                  edge=edge, spread=bc[i]-ba[i], price=price,
                                  book_edge=side * (quote-100*anchor),
                                  spot_bps=side * np.log(xp[sx]/xp[sa])*10000,
                                  valid=valid))
            for h in [1, 5, 60]:
                # Match future RECEIPT-clock mid; this is a valuation mark, not an exit.
                target = f.recv_us.to_numpy(dtype=np.int64) + h * 1_000_000
                j = np.searchsorted(bt, target, side='right') - 1
                ok = (j >= 0) & (target <= bt[-1])
                j = np.maximum(j, 0)
                d[f'mk{h}'] = np.where(ok, side * (price-bm[j]), np.nan)
            out.append(d[d.valid & (d['count'] > 0)])
    return pd.concat(out, ignore_index=True)


def summarize(d, hours):
    r = dict(rows=len(d), markets=d.ticker.nunique(), contracts=float(d['count'].sum()), hours=hours)
    r['ct_per_hour'] = r['contracts']/hours
    for h in [1, 5, 60]:
        x = d.dropna(subset=[f'mk{h}'])
        w = x['count'].sum()
        if w <= 0:
            continue
        y = (x['count'] * x[f'mk{h}']).sum()/w
        g = x.assign(y=x['count'] * x[f'mk{h}']).groupby('ticker').agg(Y=('y','sum'), W=('count','sum'))
        n = len(g)
        se = np.sqrt(n/(n-1)*((g.Y-y*g.W)**2).sum())/w if n > 1 else np.nan
        r[f'mk{h}_c'] = float(y)
        r[f'mk{h}_se'] = float(se)
    return r


def main():
    p = argparse.ArgumentParser()
    p.add_argument('tape')
    p.add_argument('--out', required=True)
    p.add_argument('--delay-us', type=int, default=5440)
    p.add_argument('--series', default='KXBTC15M')
    a = p.parse_args()
    parts = []
    for chunk in pd.read_csv(a.tape, dtype={'a':str,'b':str}, chunksize=300000, low_memory=False):
        parts.append(chunk[chunk.ticker.str.startswith(a.series) & chunk.kind.isin(['B','T','F','X'])])
    df = pd.concat(parts, ignore_index=True)
    d = feature_rows(df, a.delay_us, a.series)
    # At the original base fill events only, count the historical taker quantity.
    # This is an optimistic size sensitivity, not fills: queue ahead consumes part
    # of each print, and larger inventory changes all subsequent decisions.
    trades = df[df.kind == 'T'].copy()
    trades['observed_taker_ct'] = pd.to_numeric(trades.c)
    trades = trades.groupby(['ticker','recv_us','venue_ms'],as_index=False).observed_taker_ct.sum()
    d = d.merge(trades, left_on=['ticker','recv_us','vt'], right_on=['ticker','recv_us','venue_ms'],how='left')
    markets = sorted(d.ticker.unique(), key=close_us)
    cut = len(markets)//2
    d['half'] = np.where(d.ticker.isin(markets[:cut]), 'H1', 'H2')
    result = []
    for half in ['H1','H2','ALL']:
        ds = d if half == 'ALL' else d[d.half == half]
        included = ds.ticker.unique()
        ev = df[df.ticker.isin(included)]
        hours = (ev.recv_us.max()-ev.recv_us.min())/3.6e9
        for strat in ['public_prints','base','penny4']:
            s = ds[ds.strat == strat]
            for name, mask in [('all',np.ones(len(s),dtype=bool)),
                               ('edge_ge_0',s.edge>=0),('edge_ge_0.5',s.edge>=.5),('edge_ge_1',s.edge>=1),
                               ('edge_lt_0',s.edge<0),('book_only_ge_0',s.book_edge>=0),
                               ('fixed_spot_1bp',s.spot_bps<=1),('fixed_spot_2bp',s.spot_bps<=2)]:
                result.append(dict(half=half,strat=strat,gate=name,**summarize(s[mask],hours)))
    Path(a.out).parent.mkdir(parents=True,exist_ok=True)
    Path(a.out).write_text(json.dumps(result,indent=2)+'\n')
    print(pd.DataFrame(result).round(4).to_string(index=False))
    base = d[(d.strat == 'base') & (d.edge >= 0)].copy()
    print('\nOPTIMISTIC SIZE SENSITIVITY at original retained fill events; NOT capacity or P&L')
    print('Matched taker quantity:',base.observed_taker_ct.notna().sum(),'/',len(base))
    for clip in [1,10,25,100]:
        q = np.minimum(clip,base.observed_taker_ct.fillna(0).to_numpy())
        print('clip',clip,'hypothetical_ct',round(q.sum(),2),'vs_original',round(q.sum()/base['count'].sum(),2))
    print('\nPAIRED MARKET-CLUSTERED differences of weighted mean markouts, cents')
    for h in [5,60]:
        b = d[(d.strat=='base') & d[f'mk{h}'].notna()]
        for control, mask in [('all',np.ones(len(b),dtype=bool)),('book_only',b.book_edge>=0)]:
            groups=[]
            means=[]
            for x in [b[b.edge>=0],b[mask]]:
                g=x.assign(Y=x['count']*x[f'mk{h}']).groupby('ticker').agg(Y=('Y','sum'),W=('count','sum'))
                mean=g.Y.sum()/g.W.sum()
                means.append(mean)
                groups.append((g.Y-mean*g.W)/g.W.sum())
            z=groups[0].subtract(groups[1],fill_value=0)
            se=np.sqrt(len(z)/(len(z)-1)*(z*z).sum())
            print('h',h,'residual minus',control,'difference',round(means[0]-means[1],5),'SE',round(se,5))


if __name__ == '__main__':
    main()
