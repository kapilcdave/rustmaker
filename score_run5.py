"""Venue-ledger score of a live penny run: per-market P&L from /portfolio/settlements.

`revenue` (cents) pays only the NET position; YES+NO pairs are netted to $1 each before
settlement, so that $1 x min(yes, no) has to be added back.
"""
import json, statistics as st, collections, math, sys
d = json.load(open(sys.argv[1]))
ftick = set(f['ticker'] for f in d['fills'])
rows = []
for s in d['settlements']:
    if s['ticker'] not in ftick: continue
    y, n = float(s['yes_count_fp']), float(s['no_count_fp'])
    yc, nc = float(s['yes_total_cost_dollars']), float(s['no_total_cost_dollars'])
    fee = float(s['fee_cost']); m = min(y, n)
    tot = s['revenue'] / 100 + m - yc - nc - fee
    ay = yc / y if y else 0; an = nc / n if n else 0
    pair = m * (1 - ay - an)
    rows.append(dict(t=s['ticker'], ser=s['ticker'].split('-')[0], tot=tot, pair=pair, left=tot - pair, fee=fee,
                     time=s['settled_time'], ct=y + n, npairs=m, lcnt=abs(y - n)))
def summ(xs):
    n = len(xs); m = sum(xs) / n; se = st.stdev(xs) / math.sqrt(n) if n > 1 else float('nan'); return n, m * 100, se * 100
T = [r['tot'] for r in rows]; n, m, se = summ(T)
print(f"markets {n} (fill tickers {len(ftick)})  TOTAL ${sum(T):+.2f}  {m:+.2f} ± {se:.2f} c/mkt  lower95 {m-1.96*se:+.2f}")
print(f"pairs ${sum(r['pair'] for r in rows):+.2f} over {sum(r['npairs'] for r in rows):.0f} pairs ({100*sum(r['pair'] for r in rows)/max(1,sum(r['npairs'] for r in rows)):+.2f} c/pair)"
      f"  leftovers ${sum(r['left'] for r in rows):+.2f} over {sum(r['lcnt'] for r in rows):.0f} ct  fees ${sum(r['fee'] for r in rows):.2f}  contracts {sum(r['ct'] for r in rows):.0f}")
rows.sort(key=lambda r: r['time']); h = len(rows) // 2
for name, part in [('H1', rows[:h]), ('H2', rows[h:])]:
    n, m, se = summ([r['tot'] for r in part]); print(f"{name} {part[0]['time'][11:16]}-{part[-1]['time'][11:16]}Z  ${sum(r['tot'] for r in part):+.2f}  {m:+.2f} ± {se:.2f}")
g = collections.defaultdict(list)
for r in rows: g[r['ser']].append(r)
for k, v in sorted(g.items(), key=lambda kv: -sum(r['tot'] for r in kv[1])):
    n, m, se = summ([r['tot'] for r in v])
    print(f"  {k:12s} n={n:3d} ${sum(r['tot'] for r in v):+6.2f} ({m:+.1f} ± {se:.1f})  pairs {sum(r['pair'] for r in v):+.2f} left {sum(r['left'] for r in v):+.2f}")
srt = sorted(rows, key=lambda r: r['tot'])
print('worst 5', [(r['t'], round(r['tot'], 2)) for r in srt[:5]])
print('best 5', [(r['t'], round(r['tot'], 2)) for r in srt[-5:]])
print(f"drop best 5: ${sum(r['tot'] for r in srt[:-5]):+.2f}")
hr = collections.defaultdict(float)
for r in rows: hr[r['time'][11:13]] += r['tot']
print('by settle hour UTC', {k: round(v, 2) for k, v in sorted(hr.items())})
