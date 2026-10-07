# ETH 15-minute book-residual live findings — 2026-09-27

## Decision

Stop live testing. Do not increase clip size. The real paired trades made money,
but the unpaired residuals lost more than the spread capture earned. The Ohio VM
and Rust latency are operational advantages, not evidence of a profitable edge.

The completed corrected ETH shadow reached the same decision: all fourteen arms
lost money over the full valid panel, and losses increased materially with clip.

After both runs, exchange shard 2 had no ETH position and no resting orders.

## Live results

Run A was a 30-minute smoke test over two finalized markets. Run B was the
pre-registered 120-minute validation over eight finalized markets. Both used one
contract clips and positions, the two-contract/15-second venue group, 120-second
opening cutoffs, and $1 marked-loss caps.

| Run | Markets | Contracts | Paired contracts | Paired P&L | Residual P&L | Total P&L |
|---|---:|---:|---:|---:|---:|---:|
| A | 2 | 28.00 | 13.00 | +$0.1500 | +$0.0300 | +$0.1800 |
| B | 8 | 84.63 | 39.35 | +$1.2018 | -$2.0676 | -$0.8658 |
| Combined | 10 | 112.63 | 52.35 | +$1.3518 | -$2.0376 | -$0.6858 |

The paired component earned about 2.58 cents per completed pair. About 93% of
contract legs were paired, but the remaining 7.93 residual contracts lost about
25.7 cents each. Combined live P&L was approximately -$0.274/hour over 2.5 hours.
This sample is small, but it directly rejects scaling the current rule.

All reported live fills were passive and recorded zero fees.

## Strategy

The book-residual strategy joins the existing best bid and ask; it does not move
one tick ahead. A quote remains eligible only when its price is favorable versus
the receipt-clock midpoint observed at least one second earlier. Momentum and
thin-touch checks pull quotes that look likely to be adversely selected. After a
fill, the opposite-side quote is allowed only when the two acquired outcomes cost
at most $1.00, so the engine cannot deliberately lock a guaranteed negative pair.

Profit therefore has two separate sources:

1. Completed YES/NO pairs earn `1 - yes_cost - no_cost` at settlement.
2. Any unmatched side rides to binary settlement and can win or lose most of its
   purchase cost.

The second source dominated the live variance and erased the paired edge in B.

## Difference from penny jumping

Penny jumping improves the best price by one tick to gain queue priority. It pays
away part of the spread, causes more cancel/repost churn, and depends more directly
on reacting before other makers. Book-residual instead joins the touch and waits
for displayed queue ahead to clear. Its filter attempts to avoid stale/toxic touch
quotes rather than win every queue race.

Run A's 28 fills had a median 92.9 displayed contracts ahead at join, yet median
join-to-fill time was 867 ms and the 90th percentile was 5.17 seconds. That is far
slower than the VM's sub-10-ms processing path. Faster code helps cancel stale
quotes, but it was not the binding source of fills or profitability in this test.

## Why scaling looked plausible in shadow

The earlier BTC screen found roughly +0.138 cents at a five-second midpoint mark
and large public taker quantities at the original fill events. The ETH shadow also
simulated roughly one thousand contracts per complete 15-minute window at a
25-contract clip. Those facts suggested capacity to test, not realized profit.

The shadow orders are invisible: they do not add queue, change competitor behavior,
or consume liquidity. More importantly, the first complete ETH shadow window's
25-contract book arm showed +$16.68 total only because a 25-contract residual won
+$18.75; its paired component was -$2.07. Across the first two complete windows,
both 25-contract book queue assumptions still had negative paired P&L and positive
totals driven entirely by residual settlement. That is directional lottery exposure,
not scalable market-making evidence.

## Corrected residual shadow B

The original spot-adjusted shadow arms placed no orders because their one-second
variance sampler required every carried Coinbase tick to be at most two seconds
old. On the recorded ETH feed this produced 0% model availability. A bounded
20-second carry-forward produced 100% availability on the same observed interval;
gaps longer than 20 seconds still fail closed. The fix has 23 passing Rust tests,
was deployed only as `kalshi-mm15-residual-v3`, and remained shadow-only.

The corrected three-hour B run scored all 11 expected complete windows. One partial
market at startup was excluded prospectively. All 36 audit chunks were archived;
the run recorded zero venue gaps, zero dropped tape rows, zero ledger errors, and
no missing complete windows. It submitted no real orders.

| Arm | Queue model | Simulated contracts | Paired P&L | Residual P&L | Total P&L |
|---|---|---:|---:|---:|---:|
| Base 1 | Pro-rata | 855.55 | -$0.6509 | -$0.9904 | -$1.6413 |
| Residual 1 | Pro-rata | 591.95 | -$0.1279 | -$1.2904 | -$1.4183 |
| Base 1 | Trade-count | 684.30 | -$0.5594 | -$0.8000 | -$1.3594 |
| Residual 1 | Trade-count | 487.56 | -$0.4468 | -$1.2760 | -$1.7228 |
| Book 25 | Pro-rata | 8,713.60 | -$6.3003 | -$19.1952 | -$25.4955 |
| Residual 25 | Pro-rata | 8,012.20 | -$11.4968 | -$29.0712 | -$40.5680 |
| Book 25 | Trade-count | 7,828.98 | +$0.6952 | -$17.9801 | -$17.2849 |
| Residual 25 | Trade-count | 7,443.35 | -$7.3495 | -$18.0837 | -$25.4332 |
| Residual 100 | Pro-rata | 16,521.65 | -$30.2107 | -$61.5914 | -$91.8021 |
| Residual 100 | Trade-count | 14,888.87 | -$8.6956 | -$68.5054 | -$77.2010 |

The residual filter did not improve the 25-contract book arm under either queue
model: it worsened total simulated P&L by $15.0725 under pro-rata and $8.1483 under
trade-count. The one-contract residual arms also lost under both models. The
100-contract arms had the largest losses, directly rejecting the proposed scale
case. These are shadow fills rather than realizable income, but they are sufficient
to reject another live trial of this rule.

## Original shadow A

The frozen A run ultimately produced 6 valid windows out of 15 expected; 9 were
excluded for late discovery. Its residual arms posted zero because of the stale
spot-sampler defect. Every 25-contract arm had negative paired P&L. The only
positive 25-contract total, `book25_tc` at +$12.9015, consisted of -$2.0447 paired
P&L and +$14.9462 residual P&L, again showing directional settlement exposure
rather than repeatable spread capture. The broader archived-window diagnostic is
saved as `data/capacity-eth-20260927-A/diagnostic_all_archived_windows.json`.

## Final research decision

Do not deploy the spot-adjusted residual rule to live trading. Do not increase live
size. No further live run is justified unless a materially different rule first
shows positive paired and total settlement economics across complete out-of-sample
windows, with residual risk bounded independently of favorable binary outcomes.
