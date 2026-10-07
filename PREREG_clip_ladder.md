# Pre-registration: penny4 clip-capacity ladder

Frozen before the first `--clip-ladder` capture. This is a shadow experiment and places no orders.

## Question

Does the penny4 price-improvement seat retain positive per-contract economics and increase total
income when its order clip rises from 1 to 3 and 10 contracts? This tests capacity, not a new
fair-value signal.

## Arms

- `touch_c1`: gated touch-join control, 1 contract, absolute position at most 1.
- `p4_c1`: penny4, 1-contract clip, absolute position at most 1.
- `p4_c3`: identical penny4 rule, 3-contract clip, absolute position at most 3.
- `p4_c10`: identical penny4 rule, 10-contract clip, absolute position at most 10.
- Penny4 means one tick inside when the other participants' spread is at least 4 ticks, with the
  existing momentum/thinness gates, measured create/cancel latency, pro-rata cancellation credit,
  and no posts in the final 120 seconds.
- Each arm has its own 300-token/s budget because only one arm would run live.
- A through-price replay fill is capped by the historical taker order's observed quantity. Larger
  clips are not assumed to fill merely because the original print traded at a worse price.

## Window And Metrics

- Fresh 24-hour capture across all nine crypto series, with BTC reported separately and pooled
  results secondary. Do not select a series after seeing this window.
- Score only settled markets with `shadow_pnl.py`.
- Primary capacity metrics: settlement cents per contract, dollars per hour, pair share, and total
  contracts for `p4_c1`, `p4_c3`, and `p4_c10`.
- Validity control: `touch_c1` must have negative settlement cents per market, matching prior real
  and shadow touch-join evidence. Otherwise the replay is void.
- Scale passes only if the larger arm has positive settlement cents per contract in both time
  halves and increases total dollars over `p4_c1`. Report market-clustered uncertainty; do not use
  fill count as the independent sample size.

## Known Upward Bias

The shadow cannot model takers reducing order size because our larger quote is visible, or rivals
joining and improving around it. A shadow pass licenses only a randomized capped live clip test; it
does not establish deployable P&L or a $10,000/month run rate.

## Run

```bash
cargo run --release -- shadow --clip-ladder --minutes 1440 --out data/shadow_clip_ladder
python3 score_clip_ladder.py data/shadow_clip_ladder/shadow_XXXX.csv.gz
```
