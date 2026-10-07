"""Venue-ledger fill P&L to settlement, by time left to the market's close.

Each fill's value to settlement is (result - price) x count for a bid, (price - result) x count
for an ask. Market P&L is exactly the sum of its fills' values, so removing the fills in a time
bucket changes P&L by exactly that bucket's value (before any change in who we then trade with).
Usage: python3 ttc_buckets.py venue.json [run_boundaries_unix ...]
"""
import collections, datetime as dt, json, math, sys

d = json.load(open(sys.argv[1]))
res = {s['ticker']: 1.0 if s['market_result'] == 'yes' else 0.0 for s in d['settlements'] if s['market_result'] in ('yes', 'no')}
MON = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']


def close_ts(t):  # 26SEP250445 = 2026-09-25 04:45 US Eastern (EDT)
    seg = t.split('-')[1]
    return dt.datetime(2000 + int(seg[:2]), MON.index(seg[2:5]) + 1, int(seg[5:7]), int(seg[7:9]), int(seg[9:11]),
                       tzinfo=dt.timezone(dt.timedelta(hours=-4))).timestamp()


def bucket(s):
    return '>7m' if s > 420 else '5-7m' if s > 300 else '4-5m' if s > 240 else '3-4m' if s > 180 else '2-3m' if s > 120 else '<2m'


cuts = [int(x) for x in sys.argv[2:]]
B = collections.defaultdict(lambda: collections.defaultdict(list))
for f in d['fills']:
    t = f['ticker']
    if t not in res:
        continue
    p, c, y = float(f['yes_price_dollars']), float(f['count_fp']), res[t]
    v = (y - p) * c if f['book_side'] == 'bid' else (p - y) * c
    run = sum(f['ts'] >= x for x in cuts)
    B[bucket(close_ts(t) - f['ts'])][run].append((v, c, t))
order = ['>7m', '5-7m', '4-5m', '3-4m', '2-3m', '<2m']
runs = sorted({r for b in B.values() for r in b})
print(f"{'left':6s} {'fills':>6s} {'P&L $':>8s} {'c/ct':>7s} {'±se':>5s} | per run segment $")
for k in order:
    xs = [x for r in B[k].values() for x in x_ for x_ in [r]] if False else [x for r in B[k].values() for x in r]
    if not xs:
        continue
    v = sum(a for a, _, _ in xs); c = sum(b for _, b, _ in xs)
    # SE clustered by market: fills in one market share one outcome
    bym = collections.defaultdict(float)
    for a, _, t in xs:
        bym[t] += a
    vals = list(bym.values()); n = len(vals); mu = sum(vals) / n
    se = (sum((x - mu) ** 2 for x in vals) / max(1, n - 1)) ** 0.5 * math.sqrt(n)  # SE of the bucket total
    seg = '  '.join(f"{sum(a for a, _, _ in B[k].get(r, [])):+6.2f}" for r in runs)
    print(f"{k:6s} {len(xs):6d} {v:+8.2f} {100 * v / c:+7.2f} {se:5.2f} | {seg}")
