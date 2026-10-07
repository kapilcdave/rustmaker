"""Score completed archived capacity-shadow windows from the cash/inventory ledger.

No orders. No P&L-based stopping. Partial, missing and gap-tainted windows are
reported separately, never silently promoted to a full-run income estimate.
"""
import argparse
import collections
import gzip
import json
import math
import statistics
import urllib.request
from pathlib import Path


ARMS=[f'{s}_{q}' for q in ['pr','tc'] for s in ['base1','base25','book25','res1','res10','res25','res100']]


def cash_settlement(cash_c,position_fp,result):
    if result not in ('yes','no'):raise ValueError('Unsettled outcome')
    return cash_c/100 + position_fp/100*(result=='yes')


def main():
    p=argparse.ArgumentParser();p.add_argument('root');p.add_argument('--offline',action='store_true');a=p.parse_args()
    root=Path(a.root);cachepath=root/'results.json'
    cache=json.loads(cachepath.read_text()) if cachepath.exists() else {}
    finals={};fills=collections.defaultdict(list);starts={};errors=[]
    for path in sorted(root.glob('run_*/audit/*.gz')):
        run=path.parents[1].name
        with gzip.open(path,'rt') as f:
            for line in f:
                d=json.loads(line);kind=d.get('kind')
                if kind=='start':starts[run]=d['recv_us']
                elif kind=='fill':
                    if d['count_fp']>d['trade_count_fp'] or d['count_fp']<=0:errors.append('invalid fill size')
                    fills[(run,d['ticker'],d['arm'])].append(d)
                elif kind=='final':finals[(run,d['ticker'],d['arm'])]=d
    valid={};excluded=collections.Counter()
    for key,d in finals.items():
        run,tk,arm=key;opening=d['close_ms']*1000-900_000_000
        if d['invalid']:excluded['gap_tainted']+=1;continue
        if starts.get(run,10**30)>opening:excluded['partial_start']+=1;continue
        if not d['first_snapshot_us'] or d['first_snapshot_us']>opening+20_000_000:
            excluded['late_discovery']+=1;continue
        valid[key]=d
    tickers={k[1] for k in valid}
    if not a.offline:
        for tk in sorted(tickers):
            if cache.get(tk) in ('yes','no'):continue
            try:
                with urllib.request.urlopen('https://external-api.kalshi.com/trade-api/v2/markets/'+tk,timeout=15) as r:
                    cache[tk]=json.load(r)['market'].get('result','')
            except Exception as e:print('Result unavailable',tk,str(e))
        cachepath.write_text(json.dumps(cache,indent=2)+'\n')
    # Require the full arm panel for paired comparisons, including zero-fill arms.
    panels=collections.defaultdict(set)
    for run,tk,arm in valid:panels[(run,tk)].add(arm)
    complete={k for k,v in panels.items() if v==set(ARMS) and cache.get(k[1]) in ('yes','no')}
    rows=[]
    for (run,tk,arm),d in valid.items():
        if (run,tk) not in complete:continue
        pos=0;cash=0.0;ct=0.0;peak=0;paired=0.0;pairs=0.0;inventory=collections.deque()
        for f in fills[(run,tk,arm)]:
            sign=1 if f['side']==0 else -1
            qty=f['count_fp']/100;price=f['price_fp']/10000
            pos+=sign*f['count_fp'];cash-=sign*qty*price;ct+=qty;peak=max(peak,abs(pos))
            left=qty
            while left>1e-9 and inventory and inventory[0][0]!=sign:
                oldsign,oldpx,oldqty=inventory[0];q=min(left,oldqty)
                paired+=q*(price-oldpx)*oldsign;pairs+=q;left-=q
                if oldqty-q<1e-9:inventory.popleft()
                else:inventory[0]=(oldsign,oldpx,oldqty-q)
            if left>1e-9:inventory.append((sign,price,left))
        if pos!=d['position_fp'] or abs(cash-d['cash_c']/100)>1e-5:
            errors.append(f'ledger mismatch {run} {tk} {arm}')
        cap=int(''.join(c for c in arm.split('_')[0] if c.isdigit()))
        if peak>cap*100:errors.append(f'position cap exceeded {tk} {arm}')
        pnl=cash_settlement(d['cash_c'],d['position_fp'],cache[tk])
        rows.append(dict(ticker=tk,arm=arm,close_ms=d['close_ms'],pnl=pnl,contracts=ct,
                         paired_pnl=paired,pairs=pairs,residual_pnl=pnl-paired,max_position=peak/100))
    report={'scored_windows':len(complete),'excluded_arm_windows':dict(excluded),
            'unsettled_or_incomplete_panels':len(panels)-len(complete),'errors':errors,
            'fee_assumption':'Passive maker fees zero, matching observed ETH live fills; no taker exits',
            'status':'INVALID' if errors else 'COLLECTING_NOT_A_VERDICT','arms':{}}
    for arm in ARMS:
        r=sorted((r for r in rows if r['arm']==arm),key=lambda x:x['close_ms']);n=len(r)
        if not n:continue
        hours=collections.defaultdict(float)
        for x in r:hours[x['close_ms']//3_600_000]+=x['pnl']
        # Hourly totals provide a serial-dependence diagnostic; final inference also
        # requires the full scheduled-window coverage report below.
        se=statistics.stdev(hours.values())/math.sqrt(len(hours)) if len(hours)>1 else None
        report['arms'][arm]={'windows':n,'contracts':sum(x['contracts'] for x in r),
            'pnl':sum(x['pnl'] for x in r),'valid_window_dollars_per_hour':sum(x['pnl'] for x in r)/(n*.25),
            'hour_block_mean_se':se,'paired_pnl':sum(x['paired_pnl'] for x in r),
            'residual_pnl':sum(x['residual_pnl'] for x in r),'max_position':max(x['max_position'] for x in r),
            'H1_pnl':sum(x['pnl'] for x in r[:n//2]),'H2_pnl':sum(x['pnl'] for x in r[n//2:])}
    statepath=root/'supervisor.json'
    if statepath.exists():
        state=json.loads(statepath.read_text());report['supervisor_status']=state['status']
        import time
        first_close=(math.floor(state['start_unix']/900)+2)*900
        last_close=math.floor(min(time.time()-30,state['deadline_unix'])/900)*900
        expected=max(0,int((last_close-first_close)/900)+1)
        report['expected_complete_scheduled_windows']=expected
        report['missing_or_excluded_windows']=max(0,expected-len(complete))
        report['full_run_income_claim_allowed']=False
    (root/'score.json').write_text(json.dumps(report,indent=2)+'\n')
    (root/'market_scores.json').write_text(json.dumps(rows,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
