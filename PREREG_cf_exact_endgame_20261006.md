# PREREG — exact CF Benchmarks feed versus Kalshi 15-minute crypto books

Frozen 2026-10-06 before this repository captured any
`cfbenchmarks_value` or `cfbenchmarks_value_5hz` message.

## Question

Kalshi now exposes the settlement index itself through authenticated WebSocket
channels.  The one-hertz channel also publishes the partially accumulated
quarter-hour final-minute average.  The old endgame study reconstructed the
same quantity from Coinbase and concluded that the Kalshi book was sharper than
the proxy.  This is a new instrument: it removes cross-index basis and exposes
the exact values used by the contract.

Does a causal, exact-index estimate of the terminal 60-second average lead the
executable Kalshi book by enough to survive the taker fee and adverse-fill
stress?

## Scope

- Paper/read-only only. No order placement.
- Active assets: ETH, SOL, XRP, DOGE, BNB, HYPE, NEAR and ZEC.
- BTC is excluded from model selection and headline results.
- `cfbenchmarks_value` is collected for every active asset.
- `cfbenchmarks_value_5hz` is additionally collected where available.
- Kalshi `orderbook_delta`, `ticker`, and `trade` messages are collected for
  the matching `KX{ASSET}15M` markets.
- Decisions are restricted to the final 60 seconds before the scheduled close.

## Data and clocks

Every raw WebSocket message is persisted with:

- local receipt timestamp;
- Kalshi `sending_ts_ms`, when present;
- CF `source_ts_ms` and `received_at`;
- WebSocket `sid` and `seq`;
- the current market close timestamp and full top of book.

Sequence gaps invalidate the affected connection interval.  Cross-stream
latencies use local receipt time unless both messages carry a timestamp from the
same clock.  A signal cannot use a book or CF value received after its decision
timestamp.

## Frozen arms

### A0 — executable book

The contemporaneous Kalshi mid, bid and ask. This is the benchmark.

### A1 — exact accumulated-average diffusion

At CF tick `n` in the final minute:

1. Read the exact accumulated average and `window_size=n` from
   `last_60s_windowed_average_15min`.
2. Use the latest exact CF index value for the unwritten ticks.
3. Estimate trailing absolute diffusion scale from causal 5 Hz CF values over
   5 minutes, with 30-second differences. If 5 Hz is unavailable, use the
   one-hertz stream.
4. The terminal mean is
   `(n * accumulated_average + (60-n) * current_index) / 60`.
5. The remaining-average standard deviation uses the integrated Brownian
   formula `sigma * r^(3/2) / (sqrt(3) * 60)`, where `r=60-n`.
6. Convert the distance to the published target into a normal CDF probability.

No parameter is fitted to outcomes in the collection window.

### A2 — conservative exact arm

Same as A1, but multiply sigma by 2 and add a one-cent adverse-fill haircut to
the executable edge. This is the primary economic arm.

### A3 — stale-book reaction

Replay A2 at fixed local-receipt delays of 0, 100, 250, 500 and 1000 ms after
each CF update, using the last book received by that delayed timestamp. This
measures whether any apparent edge is a transient reaction lag rather than a
stable mispricing.

### A4 — close-tick diagnostic

When `window_size=60`, record whether the final outcome-bearing CF message was
received before or after the scheduled market close and whether a non-crossed
executable quote still existed. This arm is diagnostic only and cannot be
reported as a tradable strategy without positive pre-close time on the local
clock.

## Execution and P&L

At most one trade per market per `(arm, delay, threshold)`:

- Buy YES at the displayed YES ask when
  `p_yes - ask - taker_fee(ask) > threshold`.
- Buy NO at the displayed NO ask (`1 - yes_bid`) when
  `(1-p_yes) - no_ask - taker_fee(no_ask) > threshold`.
- If both qualify, take the larger post-fee edge.
- Hold to settlement.
- Thresholds: 2c, 5c and 10c.
- Taker fee is computed from the venue fee formula already pinned in the
  repository; reported `fee_cost` from historical fills is not substituted.
- Stress tables subtract an additional 0.5c and 1.0c per contract.

The decision timestamp is the later of the required CF message receipt and the
book receipt. A quote first observed after that timestamp is not eligible.

## Sampling and statistics

- The independent unit is the quarter-hour close window, not a tick, row,
  market-minute, or asset.
- Report equal-weighted and size-weighted results. Size weighting is capped by
  displayed touch depth and separately shown at clips 1, 3, 10 and 25.
- Report by asset and by chronological half.
- Cluster bootstrap by close window, 10,000 draws.
- Always report trade count, close-window count, loss count, mean cents per
  contract, median, 95% interval, and concentration excluding the best 10% of
  windows.

## Gates

The family is a candidate only if A2 at a predeclared threshold:

1. has at least 100 independent close windows and at least 200 trades;
2. has positive mean after the 1.0c stress with a 95% lower bound above zero;
3. has positive median and positive mean after removing the best 10% of close
   windows;
4. has the same sign in both chronological halves;
5. remains positive at 250 ms delay;
6. does not rely on BTC or on a single asset for more than 50% of P&L; and
7. has enough displayed depth for clip 3 on at least half of signals.

If the overnight collection cannot reach the sample gates, report the measured
latency, coverage, and paper P&L as an interim result only. Do not tune a new
threshold on the same window.

## GLiNER2.5-Decide side arm

The model may be evaluated only as a frozen selector over a text serialization
of A2 state. It does not replace A2 and cannot set prices.

Labels:

- `trade_yes`
- `trade_no`
- `skip_uncertain`
- `skip_expensive`

The serialization contains only causal exact-feed state, time left, volatility,
book prices, spread, depth, and post-fee A2 edges. Before scoring P&L, a
degeneracy test must show at least three distinct labels across a balanced set
of 64 synthetic states. The GLiNER arm must beat A2 at matched turnover using a
rolled-decision null. Failure or degeneracy closes only the GLiNER side arm.

## Amendment 1 — public exact rolling-average instrument

Frozen 2026-10-06 after an authentication smoke test returned 401 for the
locally stored key, and after inspecting one current ETH
`GET /live_data/events/{event_ticker}` response, but before bulk history was
downloaded or any outcome-conditioned statistic was computed.

The public live-data endpoint returns a one-second `timeseries` whose value at a
market boundary matches the next market's published `floor_strike` before
rounding. It is therefore the exact trailing 60-second settlement variable,
rather than a Coinbase proxy. This does not expose the raw CF tick or the
partially accumulated final-minute average, so it is a separate frozen arm:

### B1 — exact settlement-variable diffusion

For each decision second in the final minute, let `Y_t` be the public
one-second rolling-average value and `K` the market's published target.

- Forecast `Y_T` from `Y_t`.
- Estimate horizon-specific uncertainty causally from prior changes
  `Y_s - Y_(s-r)` over the preceding 30 minutes, where `r` is the integer
  seconds remaining.
- Probability is the normal CDF of `(Y_t-K)/sigma_r`.
- If fewer than 20 prior horizon-matched differences exist, no decision is
  emitted.

### B2 — exact settlement-variable trend

Same as B1, but the terminal mean adds a frozen linear continuation term using
the median one-second change over the prior 15 seconds, winsorized at the
10th/90th percentiles of one-second changes over the prior 30 minutes.

### B3 — empirical transition probability

Within each asset separately, use only earlier markets to estimate
`P(Y_T >= K)` by bins of:

- seconds remaining: 1-5, 6-10, 11-20, 21-30, 31-45, 46-60;
- standardized distance `(Y_t-K)/sigma_r`: boundaries
  `[-inf,-3,-2,-1,-0.5,0,0.5,1,2,3,inf]`;
- sign of the trailing-15-second slope.

Laplace smoothing is fixed at one pseudo-win and one pseudo-loss. At least 30
earlier observations are required in a cell. No future market or same-market
later second may enter the estimate.

### Historical execution proxy

Public historical market metadata supplies OHLC candlesticks rather than the
full historical order book. Therefore historical B-arm scoring has two layers:

1. **Probability score:** Brier/log loss against settlement on every eligible
   decision.
2. **Economic upper bound:** compare to the contemporaneous market price only
   where a public live-data capture or existing raw order-book tape provides a
   causal executable bid/ask.

No OHLC close, high, low, midpoint, or later snapshot may be called an
executable price. If exact historical books cannot be joined, the bulk history
can validate forecasting but cannot establish profit.

The live collector may fall back to public REST polling. Such a capture must
label its measured request/response interval and cannot claim sub-request
reaction latency.

### B4 — historical real-print executable upper bound

Existing `kalshi-scalp/data/rpl_tapes/KX{ASSET}15M.jsonl` files contain
microsecond-stamped venue trades with YES-axis price, count, and aggressor side.
For a historical upper bound, evaluate B1/B2 immediately before each real print
inside the final minute:

- a `taker_side=yes` print is an observed YES-ask execution at its printed
  price;
- a `taker_side=no` print is an observed NO-ask execution at
  `1 - printed_yes_price`;
- take at most the first qualifying print per market and model arm;
- require at least one full second between the model's source sample and the
  print timestamp, so the source value was public before the observed
  execution;
- cap size at the printed count and show clips 1, 3, 10 and 25 separately.

This is explicitly an optimistic upper bound: another taker received the fill,
and the tape does not prove that equal size remained after it. A negative result
closes the arm; a positive result requires a live read-only book capture before
promotion.

## Amendment 2 — zero-shot GLiNER2.5-Decide diagnostic

Frozen 2026-10-06 after the B1/B2 320-market interim table was computed but
before downloading or running `fastino/GLiNER2.5-Decide`, and before inspecting
any GLiNER prediction. This is a diagnostic side arm, not a confirmatory
strategy result.

The public-history proxy does not contain the authenticated A2 accumulated
average. To honor the requested model trial without pretending otherwise, the
zero-shot model receives the causal B2 state available immediately before each
B4 real print:

- asset and seconds remaining;
- standardized target distance and trend shift;
- B1 and B2 YES probabilities;
- observed executable side, entry price, taker fee and B2 post-fee edge;
- source-to-print delay and printed count.

The single task is `action`, with these descriptions:

- `trade_yes`: buy the observed YES execution only when the state supports YES
  after price and fee;
- `trade_no`: buy the observed NO execution only when the state supports NO
  after price and fee;
- `skip_uncertain`: abstain because outcome confidence or directional support
  is weak;
- `skip_expensive`: abstain because price plus fee consumes the forecast edge.

No prompt demonstrations, outcome labels, fine-tuning, confidence threshold or
price-setting authority are allowed. A trade occurs only when the returned
label exactly matches the side of the observed print. At most the first such
candidate per market is taken.

Before economic scoring, run the already frozen 64-state balanced synthetic
degeneracy grid. The grid crosses side, time, probability, and cheap/expensive
entry states. At least three distinct labels must appear.

For a matched-turnover comparison, B2 takes the same number of markets by
ranking each market's best causal observed-print B2 edge, with ties broken by
close time then ticker. This ranking is an optimistic comparator because it
uses every print in the market. Report raw and 1c-stressed P&L, close-window
cluster bootstrap, chronological halves, and concentration after removing the
best 10% of windows. A circular roll null shifts the GLiNER take/skip decision
by one market within each asset while preserving the chosen candidate and
turnover; report all non-zero asset-local rolls when available.

The side arm passes only if it is non-degenerate, beats matched-turnover B2 on
raw mean, and its actual-minus-roll-null mean is positive with a 95% interval
above zero after 1c stress. Regardless of outcome, this retrospective
real-print layer remains an optimistic upper bound.

## Amendment 3 — walk-forward calibration at observed prints

Frozen 2026-10-06 after the 2,000-market B1/B2 table and fixed-time B3
probability scores were inspected, but before any print-level B3 probability or
P&L was computed. The motivation is calibration, not subgroup selection: B2
uses a normal approximation while B3 estimates the transition frequency
directly.

For every causal B4 print candidate, construct the same asset, remaining-time,
standardized-distance and slope-sign cell used by B3. Cell counts contain only
strictly earlier markets and count a market at most once per cell. A candidate
is eligible only with at least 30 earlier markets in its cell.

Three frozen arms are scored:

- **C1 empirical:** use the Laplace-smoothed B3 cell probability.
- **C2 shrinkage:** combine B2 and C1 as
  `(100 * p_B2 + n * p_C1) / (100 + n)`, where `n` is the earlier-market cell
  count.
- **C3 unanimity:** require C1 and B2 to favor the same observed side and for
  both side probabilities, separately, to clear entry plus fee plus threshold.
  The conservative probability used for recorded edge is the smaller of the
  two side probabilities.

Use the existing 2c, 5c and 10c thresholds, first qualifying observed print per
market/arm/threshold, one-second source age, fee, stress, close-window
clustering, halves, trim, breadth and capacity rules without change. These are
still optimistic execution upper bounds. No time, asset, distance, confidence,
or price subgroup may be promoted from this sample.

## Amendment 4 — persistence and model-agreement vetoes

Frozen 2026-10-06 after Amendment 3 was launched but before its output existed,
and before any persistence-veto P&L was computed. The mechanism is adverse
selection control: a transient exact-index jump can create a large model edge
at the same moment an informed venue trade arrives.

At each B4 candidate, score three conservative veto arms:

- **P1 two-second persistence:** current B2 and the causal B2 forecast from at
  least two seconds earlier must independently clear the same observed entry,
  fee and threshold for the observed side.
- **P2 five-second persistence:** same, using a forecast from at least five
  seconds earlier.
- **P3 diffusion/trend agreement:** current B1 and B2 must independently clear
  the same observed entry, fee and threshold for the observed side.

The recorded model probability is the smaller qualifying side probability.
The earlier forecast may precede the final minute; the actual decision and
print must remain inside it. At most the first qualifying print per
market/arm/threshold is taken. Existing thresholds, fees, one-second source
age, stress, clustering, halves, trim, breadth, and capacity rules apply.

## Amendment 5 — overlap-aware trend decay

Frozen 2026-10-06 before computing this model's probability scores or P&L.
The public variable is a trailing 60-second mean. Its one-second change is the
new raw-index observation minus the observation leaving the window, divided by
60. Under a zero-drift raw-price model, the overlap supporting an observed
rolling-mean slope decays as old observations leave the window, so B2's
constant-slope continuation is too persistent.

**Q1 overlap trend** uses B2's frozen winsorized median one-second slope but
multiplies its `r`-second continuation by
`1 - (r + 1) / 120`, where `r` is seconds remaining. The factor is the average
remaining overlap of a 60-observation window over the forecast horizon. The
same B1 horizon-matched causal standard deviation converts the resulting mean
to a normal-CDF probability.

Score Q1 at all fixed decision seconds and on B4 real prints, with the existing
thresholds, first-fill rule, fees, source-age rule, stress, clustering, halves,
trim, breadth and capacity gates. No fitted decay coefficient or subgroup is
allowed.

## Amendment 6 — sealed adjacent-history replication

Frozen 2026-10-06 before requesting or inspecting any public live-data history
for the immediately preceding 250 eligible tape markets per asset. The original
2,000-market panel is the development panel. The replication panel is selected
mechanically by skipping those latest 250 eligible markets and taking the next
250 for each of ETH, SOL, XRP, DOGE, BNB, HYPE, NEAR and ZEC.

All already frozen B1/B2, C1/C2/C3, P1/P2/P3 and Q1 arms and all three existing
thresholds may be reported. No new coefficient, threshold, subgroup, or arm can
be selected after the replication files are downloaded. A strategy can be
called replicated only if it independently clears the original economic gates
on both panels and has the same chronological-half sign within each panel.

Because this replication panel is chronologically earlier than the development
panel, it is an untouched reverse-time replication, not a prospective result.
The public live capture running through 10:00 PDT remains the only prospective
overnight panel.

## Displayed-tie audit correction

The sealed-history integrity audit found markets whose displayed expiration
value equals the displayed floor strike, with both YES and NO venue results
across the longer history. The historical endpoint rounds or replaces the close
point with the published expiration precision, so displayed equality does not
reveal the unrounded settlement comparator. Such rows are now reported as
`displayed_ties` and excluded from the independent label-reconstruction check.
Recorded venue results were already the labels in every scorer, so no P&L
changes. Normal-CDF probabilities also do not change under a continuous
forecast distribution.

## Amendment 8 — counterparty-conditioned calibration, third sealed panel

Frozen 2026-10-06 after development and adjacent-replication B1/B2 results
showed that the observed print side wins much less often than its unconditional
B2 probability, but before downloading or inspecting the next-earlier 200
eligible tape markets per asset.

The first two panels are training data. For every causal real-print candidate,
fold YES and NO into the probability and price of the observed aggressor side.
Estimate its settlement win rate in cells of:

- seconds remaining: the existing six B3 time bins;
- B2 side probability:
  `[0,.05,.10,.20,.35,.50,.65,.80,.90,.95,1]`;
- observed side entry price:
  `[0,.02,.05,.10,.20,.35,.50,.65,.80,.90,.95,.98,1]`.

Assets and YES/NO direction are pooled. Each training market contributes its
settlement label at most once to a cell, no matter how many prints land there.
If both directions or several prices map to the same folded cell, the first
chronological observed print supplies that market-cell label.
Use one pseudo-win and one pseudo-loss. A cell requires at least 100 unique
training markets.

**R1 counterparty empirical** buys the observed side when this calibrated
probability clears entry plus fee plus the existing 2c, 5c or 10c threshold.
Take at most the first qualifying print per holdout market and threshold. The
holdout is selected mechanically by skipping the latest 500 eligible tape
markets and taking the next 200 for each of the eight assets. It is untouched,
chronologically earlier, and therefore a reverse-time test rather than a
prospective one.

All original stress, close-window clustering, halves, trim, breadth and
capacity gates apply. No cell boundary, minimum count, threshold, asset filter,
or model blend may change after the third-panel download begins.

## Amendment 9 — direct orderbook endpoint prospective correction

Frozen 2026-10-06 after the first two prospective close windows produced
positive paper signals from the market-summary bid/ask fields, and after one
manual current-market check showed those summary fields could differ by
several ticks from `GET /markets/{ticker}/orderbook?depth=3`.

The market-summary capture is retained but is not sufficient execution
evidence.  From this amendment through 10:00 PDT, replace the second serial
request with the public depth-one orderbook endpoint:

- best YES bid is the highest `yes_dollars` bid;
- best YES ask is one minus the highest `no_dollars` bid;
- displayed size comes from the exact level used;
- the exact live-data request must complete before the orderbook request
  begins;
- all existing B1/B2/Q1/P1/P2/P3 definitions, thresholds, first-signal rule,
  fees, source-age rule, stress, clustering, halves, trim and capacity
  summaries remain unchanged.

This is a prospective measurement correction, not a new fitted arm.  It is
still non-atomic REST paper evidence and cannot be called a fill.  The two
captures must be reported separately; market-summary results cannot rescue a
failed direct-orderbook panel.
