# PREREG — BTC 15-minute / hourly-ladder settlement dominance

Frozen 2026-10-06 before computing any paired-book spread or P&L.  The
one-day partial download in `idea_lab/idea6_ladder.json` was inspected only
for schema and bar availability.

## Mechanism

The `:45 -> :00` `KXBTC15M` contract and the `KXBTCD` hourly threshold
ladder use the same 60-second CF Benchmarks BRTI average at the same hour.
Let:

- `A = {S >= K15}` be the 15-minute YES event;
- `B_L = {S > L}`, where `L < K15`, be an hourly-ladder YES event;
- `B_U = {S > U}`, where `U >= K15`, be an hourly-ladder YES event.

Then `A` is a subset of `B_L`, and `B_U` is a subset of `A`.
Consequently:

- buy `B_L YES` plus `A NO`; or
- buy `A YES` plus `B_U NO`

has a settlement payout of at least $1 per paired contract in every state.
The inequality at the lower strike is deliberately strict because the
15-minute contract uses `>=` while the hourly ladder uses `>`.

## Frozen historical screen

Population: all settled BTC 15-minute markets closing exactly on the hour
from 2026-09-14 04:00Z through 2026-10-06 08:00Z for which both contracts
have one-minute candlesticks.

At every common minute-bar close from 13 through 1 minutes before settlement:

1. choose the closest hourly strike strictly below `K15` and the closest
   hourly strike greater than or equal to `K15`;
2. compute both paired taker costs from the two displayed close quotes;
3. add the standard rounded-up taker fee separately to each leg;
4. record an opportunity only if the guaranteed floor exceeds total cost by
   at least 2 cents;
5. take only the first opportunity per hour, choosing the larger edge if both
   directions qualify at the same timestamp.

Report 0c, 0.5c-per-leg, and 1c-per-leg additional latency stress.  A
historical candle close is a **non-atomic execution upper bound**, not a fill:
there is no common event timestamp, queue position, displayed depth, or proof
that both quotes coexisted.

## Integrity gates

Before economic scoring:

1. the two market rules must name the same BRTI 60-second settlement window;
2. their published expiration values must match to the displayed precision;
3. every settled pair must obey the dominance relation implied by its
   strikes, allowing no unexplained violations;
4. at least 100 paired hours and at least 30 distinct UTC days are required.

If an integrity gate fails, the strategy is void rather than negative.

## Promotion gate

This arm advances only to a read-only atomic websocket collector if:

- at least 25 historical opportunities survive the 2c threshold;
- mean guaranteed edge remains positive after 1c **per leg** stress;
- a day-clustered 95% lower bound is above zero after that stress;
- both chronological halves are positive;
- removing the best 10% of days leaves a positive mean; and
- no UTC day supplies more than 20% of gross positive edge.

Even a pass does not authorize live orders.  A subsequent prospective,
atomic, depth-aware capture must independently show paired executable size
and enough time to complete both legs.

## Amendment 1 — same construction on the other hourly crypto ladders

Frozen 2026-10-06 after discovering the series names and reading one example
rule, but before downloading or scoring their paired candles.

Apply the identical construction, thresholds, first-signal rule, integrity
gates, stress tests, and promotion gate separately to:

| 15-minute series | hourly threshold series |
|---|---|
| `KXETH15M` | `KXETHD` |
| `KXSOL15M` | `KXSOLD` |
| `KXXRP15M` | `KXXRPD` |
| `KXDOGE15M` | `KXDOGED` |
| `KXBNB15M` | `KXBNBD` |
| `KXHYPE15M` | `KXHYPED` |
| `KXNEAR15M` | `KXNEARD` |
| `KXZEC15M` | `KXZECD` |

Each asset is its own confirmatory family and must pass alone.  A pooled
multiasset result is descriptive only and cannot rescue a failed asset.  If
an hourly series has no overlapping ladder or uses a different settlement
index/window, that asset is void.  No substitution of a range, daily, weekly,
or one-touch series is allowed after seeing results.

## Amendment 2 — prospective public-REST paired-book capture

Frozen before collecting any paired `KX*15M` / hourly-ladder snapshots.

Through 10:00 PDT, poll BTC, ETH, SOL, XRP, DOGE, BNB and HYPE during their
`:45 -> :00` markets.  Each observation records request start/end timestamps
and the returned top bid, ask and displayed size for the 15-minute contract
and its closest lower/upper hourly thresholds.

The same two paired costs and two-cent post-fee trigger apply.  A candidate is
counted only when:

- all three responses completed within 750 ms from the first request start;
- every response was obtained before the 15-minute close;
- both required legs display at least one contract;
- the rule/index and strike-dominance checks pass.

Take the first qualifying observation per asset-hour.  Report an additional
one cent per leg stress and the minimum paired displayed size.  This remains
non-atomic REST paper evidence: response intervals can overlap without the
books having been simultaneously executable.  It can reject the arm but
cannot by itself promote it to live trading.

## Amendment 3 — hourly-ladder monotonicity control/strategy

Frozen before computing this pair.

For the same closest lower and upper hourly thresholds, also buy lower-strike
YES plus upper-strike NO.  Because `{S > U}` is a subset of `{S > L}`, this
pair also pays at least $1 in every state.  Score it over the same final
13-minute window with the same fees, two-cent trigger, first-signal rule,
stress, clustering, concentration and integrity gates.

This is both a control for the 15-minute cross-instrument arm and a separate
deterministic strategy.  Report and gate it separately; neither pair may
rescue the other.

## Amendment 4 — mechanically satisfy the predeclared 30-day gate

Frozen after noticing that the original 2026-09-14 through 2026-10-06 panel
cannot contain 30 UTC days, and before downloading any additional hourly
quotes.

Append every on-the-hour BTC market from the latest ten complete UTC days in
the already archived pre-gap `kalshi-scalp/data/kxbtc15m_candles.json` panel.
That archive ends on 2026-09-08; the ten-day extension is therefore selected
by timestamp only, not by edge.  Apply every existing rule without change and
report the gap explicitly.  The extension may satisfy sample breadth but may
not be presented as prospective evidence.

## Amendment 5 — direct-orderbook repair of the prospective arm

Frozen after the second prospective hour exposed positive signals in market
summary bid/ask fields, but before reading the direct books for those signals.
The separate exact-CF experiment had already shown that market summary quote
fields can differ from `/markets/{ticker}/orderbook` by several ticks.

Invalidate Amendment 2's market-summary journal for executable-edge claims.
Restart the otherwise unchanged prospective test using depth-one direct
orderbooks for all three legs.  Derive an ask only from the opposite-side best
bid and use that bid's displayed size.  Keep the same 750 ms request-span,
pre-close, one-contract size, two-cent post-fee trigger, first signal per
asset-hour and one-cent-per-leg stress.  The discarded summary signals remain
reported as the reason for the repair, not as evidence.
