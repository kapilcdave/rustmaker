# External Kalshi bot hypothesis audit

Date: Tuesday, October 6, 2026. Public repositories, public venue data and
read-only collectors only. No order was placed.

Repositories inspected:

- `mickey1995/kalshi-btc15m-trading-bot`
- `Julian-dev28/sentient-market-reader`
- `AdrianBerkmans/ksdw`
- `DeweyMarco/simple-kalshi-bot`
- `brandononchain/kalshibot`
- `fabricio-sousa/mystic-bot`
- `reedjacobp/kalshi-trading-bot`
- `Razzleberryss/AstroTick`
- `J0shusmc/Kalshi-BTC`
- `yyardi/Meridian`

The useful ideas were translated into frozen local tests rather than accepting
README win rates or simulator P&L. Most public bots repeat four families:
spot/GBM divergence, momentum plus book skew, buying a 94–97c "done deal," and
using the parallel Polymarket contract as a signal.

## Claims that do not survive basic execution review

- **YES+NO below $1:** Kalshi has one complementary book. Crossing both sides
  is not an atomic order and ordinary direct asks include a spread, so the
  combined cost is normally above $1 before fees. The public bot itself later
  disabled this arm as non-atomic.
- **GBM fair value:** already closed in the local 35,394-window study. The
  drift correction is sub-tick, heavy tails dominate the Gaussian forecast,
  and apparent accuracy does not establish executable edge.
- **Momentum/orderbook consensus:** the Ohio full-event tape found
  short-horizon toxicity ranking but no stable settlement economics. A public
  README's stated 60–65% win rate without timestamped fills, prices, fees and
  clustered P&L is not validation.
- **Synthetic backtest claims:** one inspected repository explicitly warns
  that its old GBM simulator used future within-slot data and booked settlement
  before simulated settlement. Another ships an empty `trades.csv`. These are
  implementation examples, not evidence.

## External BTC reclaim bot

The `J0shusmc/Kalshi-BTC` repository was unusually useful because it shipped
both the frozen September 17 source rules and a later public fill/settlement
file.

Exact post-publication replication on September 18–October 5 local data closed
the rule:

- 29 source-like signal-close trades;
- **-5.862c** mean after fees;
- clustered lower bound **-22.945c**;
- halves **-2.286c / -9.200c**;
- BTC-Fade-90 alone: 21 trades, **-11.333c**.

A next-minute-plus-1c timing row was +32.000c but had only five trades and
failed the frozen 100-trade and concentration gates.

The public JSON resembled 84 authenticated fills and 31 settlements. Auditing
all rows reconstructed +$78.134 cash P&L, but the published three-lane subset
had only 13 settlements and a clustered lower bound of **-1.759c/contract**.
One day supplied 44.6% of total gross positive dollars. Because provenance is
external and unverifiable, this is self-reported evidence, not local REAL-LIVE
validation. Canonical details are in `FINDINGS_external_reclaim_20261006.md`.

## Independent high-resolution corroboration

The October 4 `yyardi/Meridian` source is primarily a Polymarket US harness, but
its BTC 15-minute contract uses the same BRTI open/close averages and it also
recorded Kalshi as a reference. Its published tape work independently reached
the same negative conclusions as this corpus:

- an LLM that bought its favorite every window lost $8.74 over 63 paper fills
  and scored slightly worse than the market;
- 4,263 historical Kalshi windows found the market mid better than random-walk,
  logistic, XGBoost and histogram-gradient-boosting forecasts, with every
  reported taker configuration non-positive;
- fresh same-second Kalshi/Polymarket US mid gaps had median 0.4c and only 2 of
  8,703 ticks cleared 6c, while earlier apparent gaps came from a 30-second
  cached REST book;
- sub-second Coinbase movement led the venue book by roughly 250ms, but a taker
  reacting 300ms later lost about 2.1c at five seconds over 1,887 events;
- print-judged touch makers had negative 60-second markouts whether or not a
  $10/500ms spot-move trigger preceded the fill;
- a 70-window hourly maker A/B ended at -5.9c/window with a confidence interval
  spanning zero.

Those are external, non-local measurements and do not replace the frozen local
tests. They do materially reduce the plausibility that another generic LLM,
technical-indicator, cross-book taker or back-of-queue maker will create edge
without genuinely new information or queue priority.

## Previous-window and spot-momentum consensus

The exact three core rules from `DeweyMarco/simple-kalshi-bot` were frozen
before a September 13–October 5 local replication. To allow the previous
settlement to arrive, entry used the first minute's ask rather than an
unobservable instant at rollover.

| rule | trades | mean after fee | clustered low | halves |
|---|---:|---:|---:|---:|
| same as previous result | 2,099 | **-2.610c** | -4.844c | -1.914c / -3.305c |
| prior 60s spot momentum | 2,099 | **-2.988c** | -5.452c | -3.892c / -2.085c |
| agreement of both | 1,112 | **-3.069c** | -6.553c | -3.266c / -2.872c |

Every half and best-day-deleted row was negative. Agreement filters prediction
noise but does not overcome the ask and taker fee. This family is closed.

## Three-rule public bot replication

The fixed February 14 rules from
`mickey1995/kalshi-btc15m-trading-bot` were tested on 2,105 entirely
post-publication BTC windows. The source's midpoint integer conversion,
minute ranges, actual side ask and taker fee were preserved.

| frozen rule | trades | mean after fee | clustered low | halves |
|---|---:|---:|---:|---:|
| buy NO when YES midpoint is 8–14c after minute 10 | 429 | **-1.750c** | -4.767c | -1.814c / -1.686c |
| buy 60–80c YES in minutes 1–3 | 921 | **-1.257c** | -4.685c | -3.372c / +0.852c |
| follow a 10c move in minutes 5–7 | 1,068 | **-0.860c** | -3.902c | +0.343c / -2.064c |

The one-minute-delayed diagnostics were also negative for all three. The
headline 145/145 dead-contract backtest is not usable evidence: the repository
ships no input history, its simple backtester does not enforce the stated
minute-10 condition or 92c maximum, and it omits spread and fees. A 100% hit
rate did not imply positive expectancy; 50 of the 429 post-publication local
entries lost and the fee-adjusted mean was negative in both halves. All three
rules are closed.

## External five-minute favorite-overbet study

The August 30 `mlysophia/kxbtc15m-overbet-study` publication reported a
fee-adjusted +2.137% aggregate return when the favorite was above 80% for more
than half of the first five minutes. Its own sensitivity table reduced the
result to nearly zero under 2c slippage, and it had no untouched holdout.

The exact threshold and five-minute window were frozen before applying a
one-minute-close proxy to the entirely post-publication September 14–October 6
local sample. Entry used the actual side ask and the local conservative fee:

- 181 trades, 163 wins;
- **-0.183c/contract** after fee;
- clustered lower bound **-4.174c**;
- chronological halves **-2.456c / +2.064c**;
- extra 1c stress **-1.183c**;
- best-10%-day-deleted mean **-2.817c**;
- minute-6 execution **-0.856c**.

YES alone had a +1.081c point estimate but a -3.972c lower bound and opposite
half signs. That side is a holdout diagnostic, not a new rule. The fixed
favorite-overbet rule is closed.

## External BTC/ETH direction × volatility regime

The June 27 `rodiiipooo/kalshi-crypto-regime-edge` source reported +3.13c per
trade over ten in-sample days. Its executable minute-5 map used BTC direction,
ETH direction and a BTC intra-window/pre-window volatility ratio.

A port of the source's index join initially appeared to replicate:

- 1,883 trades;
- **+3.832c/contract** after the actual side ask and fee;
- clustered lower bound **+1.406c**;
- halves **+5.241c / +2.425c**;
- extra 1c stress **+2.832c**.

The timestamp audit invalidated that row. Coinbase timestamps each candle by
its **open**, while Kalshi `end_period_ts` is the candle **close**. The source
joins Coinbase close at `t0+5m`, which is only known at `t0+6m`, to the Kalshi
book close at `t0+5m`. That gives the strategy one full minute of future spot.

Rebuilding the minute-5 signal from Coinbase bars available only through
`t0+4m` removed the lookahead without delaying the Kalshi entry:

- 1,742 trades;
- **+0.562c/contract** after ask and fee;
- clustered lower bound **-1.586c**;
- extra 1c stress **-0.438c**;
- best-10%-day-deleted mean **-0.758c**.

The positive point estimate therefore does not survive uncertainty, execution
stress or concentration.

Moving execution to `t0+6m`, the earliest time that the frozen signal is
observable, changed the same 1,883 signals to:

- **-1.004c/contract**;
- clustered lower bound **-3.315c**;
- halves **-0.091c / -1.915c**;
- best-10%-day-deleted mean **-2.487c**.

This is a useful demonstration of a dangerous crypto-bar merge rather than a
profitable strategy. A tiny prospective public-book journal was started to
verify the causal timing through 10:00 PDT, but it has no promotion authority.

## Mass-generated Turbine strategy collection

The public `ojo-network/kalshi-bots-collection` dump contained 1,391 strategy
DSLs and 1,276 KXBTC15M backtests. It is useful as a broad idea inventory, not
as a validation corpus:

- all 1,276 BTC backtests used one in-sample time window;
- 245 reported positive net P&L and 191 reported ROI above 100%;
- only 1,225 DSLs were exact-unique;
- the top reported strategy came from this massively searched population and
  claimed +$29,529.97 / 19,686% ROI over 12,034 trades, while only 40 positions
  were marked settled by outcome and reported Sharpe was 0.11.

The highest reported strategy, `btc-martingale-40-80-dc2b2e655aac`, was frozen
before a causal one-minute replication on the local September 14–October 6
sample. Completed buy/sell cycles produced:

- 90 cycles, below the preregistered 100-trade minimum;
- **-1.898c/contract** after modeled entry and exit fees;
- day-clustered lower bound **-5.728c**;
- halves **+2.122c / -5.918c**;
- extra 1c stress **-2.898c**;
- best-10%-day-deleted mean **-4.170c**.

This closes the selected top rule. More importantly, picking another winner
from the same 1,276 in-sample runs would repeat the multiple-testing error
rather than create an independent hypothesis.

## Independent maker and live-bot counterevidence

The public `Astanwa/kalshi-pair-tape` repository tested the common idea of
resting both 40c sides and waiting for both to fill. Its quote-touch paper
simulation lost $2,115 over 802 windows. A trade-tape forward sample lost $35
over 11 windows, and its 557-window historical tape paired both sides in 64.3%
of windows versus a 66.7% break-even rate. A selected 00:00–03:00 ET slice was
71.8%, but its interval still spanned break-even. This independently supports
the local conclusion that touch or quote presence is not a fill and that
two-sided resting inventory remains adversely selected.

The public `d589f/kalshi-trading-bot` repository supplied a rare matched
live-versus-paper record. Across 225 matched trades, the live side lost
**$65.66** despite a 71% win rate: -$41.34 direction P&L and -$24.32 fees, with
approximately zero reported entry slippage. Its later F1 run also reported
instability and the f6 strategy was retired. This is external evidence, not a
local audit, but it directly demonstrates that headline win rate and a close
paper/live entry match do not establish positive expectancy.

An additional cross-platform repository claimed very large synchronized
Kalshi/Polymarket samples, but its methodology used whole-window average
prices rather than synchronized executable asks, selected wallet labels from
the same outcomes used for P&L, treated each trade as independently held to
settlement, and inferred common control from same-second activity. Those
results are selection-contaminated and do not override the direct
2,105-window outcome audit below.

## Public AoI paper tape and ML framework audits

The `yuno444/cross-market-arbitrage-aoi` repository shipped 70,535 AoI rows and
51 BTC paper round trips. The source ledger reported +96c before fees. Applying
the documented one-contract taker fee to both displayed-ask entry and
displayed-bid exit used 98c, leaving **-2c total**. Removing its largest trade
changed after-fee P&L to **-37c**; that trade entered at 7c, outside the
repository's current 10–90c band.

More fundamentally, its "Kalshi AoI" is `now - market.updated_time`, not the
age of an orderbook message. The shipped median was 465 seconds, 94.1% of rows
were supposedly more than 60 seconds stale, and the maximum was 64,602
seconds. The code treats that metadata age as a latency signal, falls back from
missing asks to bids, and assumes instant execution at displayed prices.
Ticker IDs were not recorded in the paper ledger, so independent-window
clustering is impossible. This tape does not validate a latency bot.

The `kapelame/kalshi-crypto-bot` framework shipped 2,470 snapshots but only
6–7 unique quarter-hour tickers per asset. Its `train.py` concatenates every
BTC snapshot, then ETH, SOL and XRP, before taking the last 20% of rows as the
test set. On the shipped sample, all 1,976 test rows are XRP and hundreds of
rows share each single settlement target. One ticker also crosses the split.
The displayed row count is therefore not an independent-outcome sample size,
and the default split is neither a chronological market holdout nor a valid
profitability test. The repository's newer walk-forward backtester is a more
appropriate scaffold, but the shipped sample is far too short to validate a
strategy.

## External strict-subminute 90–97c favorite

The February `seanrobenalt/kalshi-bot` source adds a fixed rule that buys the
90–97c favorite with fewer than 60 seconds remaining. Historical candles only
provide the actual side ask at the exact 60-second boundary, so this is a
timing proxy rather than a claim about a strict-subminute fill.

The frozen rule failed in two independent samples:

| sample | trades | wins | mean after fee | clustered low | halves |
|---|---:|---:|---:|---:|---:|
| public Jun 27–Sep 3 full population | 539 | 507 | **-0.971c** | -3.116c | -0.388c / -1.551c |
| local Sep 14–Oct 6 | 204 | 191 | **-1.424c** | -4.878c | +0.954c / -3.802c |

Extra 1c stress was -1.971c and -2.424c respectively; best-day-deleted means
were also negative. A 94% win rate did not pay for the 90–97c premium and rare
full losses. The broad favorite-band economics are closed before taking on the
harder question of fills inside the final minute.

## Independent 6,257-window full-population study

The public `theruviparambil/kalshi-btc-15m` repository ships its entire
6,257-window, 69-day minute-book panel and standard-library analysis. Its 17
statistical unit tests passed locally, and rerunning the source reproduced the
reported results:

- buying YES at the actual ask with fees set to zero returned -0.26c, +0.59c,
  +0.54c, +0.24c and +1.57c at 10, 5, 3, 2 and 1 minutes remaining;
- none of those zero-fee rows was significant under its day-clustered wild
  bootstrap;
- applying the taker fee made the corresponding means -1.63c, -0.51c,
  -0.53c, -0.84c and +0.48c;
- by one minute remaining, only 1,089/6,256 windows still had a midpoint
  strictly inside 5–95c;
- the market midpoint's Brier score beat 0.50 at every reported horizon.

This external study is exploratory and displayed-touch fills remain an upper
bound. Its value is breadth: it independently reaches the same conclusion as
the local corpus using a larger shipped dataset, correct window-level units,
actual asks, zero-fee diagnostics and clustered inference.

### Pre-period calibration-map transfer

To test the study's small calibration departures as an actual strategy, a
logistic calibration map was fit separately at 10, 5, 3, 2 and 1 minutes on
the entire pre-period public panel, then frozen before scoring the local
September 14–October 6 sample. The local rule bought the first side with at
least 2c modeled edge after its actual ask and one-contract fee.

All 182 qualifying trades were one-minute YES entries. They produced:

- 137 wins and 45 losses;
- **-3.230c/contract** after fee;
- clustered lower bound **-10.250c**;
- halves **-4.046c / -2.413c**;
- extra 1c stress **-4.230c**;
- best-10%-day-deleted mean **-6.660c**.

The pre-period one-minute underreaction slope did not transfer into executable
OOS value. Calibration improvement without fresh information remains a
re-expression of the market price, not an edge.

## Public Sentient fill-ledger audit

The git history of `Julian-dev28/sentient-market-reader` contained an
authenticated-looking raw Kalshi fill export before a later commit removed
runtime files from the current tree. This was unusually large external
evidence, but it does not authenticate ownership or prove that every fill came
from the advertised Markov strategy.

Independent accounting found:

- 4,359 raw fills, including 2,714 buys;
- 3,437 BTC-15m fills across 450 outcome-covered tickers;
- the repository's own independent-buy-as-hold calculation: 2,690 rows,
  **-$3,510.73** after $847.71 of modeled fees;
- full observed buy/sell/settlement ledger: **-$2,557.31**;
- 254 of 450 tickers had negative ending side inventory, showing that the
  public excerpt is incomplete for portfolio accounting;
- the 179 self-contained tickers still reconstructed to **-$3,319.65**, with
  a day-clustered lower bound of -$44.57 per ticker and 43.6% of gross positive
  day P&L concentrated in one day.

The current README's 147-fill April 19–22 filter claim cannot be checked
against the public analysis rows, which end April 7. Its price caps and blocked
hours were selected from a later private slice, so they are not independent
validation. The public ledger strongly rejects the broad strategy population
but cannot exactly reconstruct the latest Markov/Hurst gate because those
signal states were not stored with each fill.

This audit also exposes why per-fill win rates are unsafe: many fills share the
same ticker and some are later sold. Treating each buy as a separate held
contract double-counts market outcomes and ignores exits.

### Post-publication replication of the current deterministic stack

The public April 29 model and risk constants were then ported to the local
September 14–October 6 sample, using completed Coinbase 5m/15m bars and actual
Kalshi side asks instead of the source backtest's fabricated empirical price
table. The frozen rule included its 9-state Markov chain, 0.11 probability gap,
0.82 current-state persistence, Hurst/volatility/velocity/distance gates,
blocked hours, side-specific price caps and golden-band timing.

- 192 trades;
- 102 wins and 90 losses;
- **-6.563c/contract** after taker fee;
- day-clustered lower bound **-12.732c**;
- chronological halves **-7.875c / -5.250c**;
- best-10%-day-deleted mean **-10.423c**;
- next-minute execution diagnostic **-4.339c**.

YES signals drove the loss: 107 trades at -12.262c. The 85 NO signals were
+0.612c at the point estimate but failed one-cent stress (-0.388c), had a
-7.609c clustered lower bound, and split -2.381c / +3.535c. The entire current
stack is closed rather than mining a new side-only rule from its holdout.

## Independent late-favorite ZEC result

`AdrianBerkmans/ksdw` studied the most plausible execution improvement for a
94–99c done-deal strategy: use ZEC, where prints persist into the final 20
seconds, and require an actual subsequent same-side print rather than assuming
a fill. Its published 4,097-market, June 30–August 12 study reports 896 filled
T-20s entries, +$100 on $72,388 staked, but an exact loss-rate confidence
interval that straddles break-even. Entry horizons from 10 to 300 seconds were
all inconclusive. This is external corroboration, not a local result, but it
agrees with the corpus: executability can improve while the fee-adjusted rare
loss tail remains unresolved.

## Polymarket transfer

Kalshi `KXBTC15M` and Polymarket BTC 15-minute markets use matching
quarter-hour windows and 60-second TWAP direction logic, but different source
indexes: CF Benchmarks BRTI and Chainlink BTC/USD.

Frozen historical sample:

- 176 seeded quarter-hour opens;
- 175 matched Polymarket events;
- **175/175 settlement directions agreed**;
- 98 first-trigger Kalshi trades using the one-minute Polymarket UP reference;
- mean **-1.081c** after Kalshi taker fees;
- clustered lower 95% bound **-7.340c**;
- chronological halves **-5.163c / +3.002c**;
- one-cent stress **-2.081c**;
- best-10%-day deletion **-3.976c**.

The directional transfer is closed. Cross-venue consensus was not information
that could be bought profitably at the historical Kalshi ask.

A broader preregistered outcome audit removed the false comfort of the small
sample:

- 2,105 local Kalshi windows;
- all 2,105 had matching closed Polymarket events after retrying a throttled
  first request burst;
- **2,083 agreed and 22 disagreed: 98.955% agreement**;
- Wilson 95% interval: **98.423% to 99.309%**;
- every mismatch occurred with the Kalshi result only 0.079–0.597bp from its
  strike.

The lower bound missed the frozen 99% risk gate. An opposite-side cross-venue
pair can lose the full combined premium when the indexes straddle zero; it is
not a guaranteed-spread bot. The direct collector remains a descriptive
journal through the stop, but this broader audit closes any "risk-free
arbitrage" characterization.

A separate prospective collector now measures the actually executable
opposite-side pair: Kalshi YES + Polymarket DOWN, or Kalshi NO + Polymarket UP.
It includes a conservative 1c Polymarket fee allowance and requires at least
3c remaining spread. This is not called risk-free because the source indexes
can disagree. Early direct books had no signal; final scoring is at 10:00 PDT.

## Resolution rider

The fixed external rule buys a 94–97c BTC favorite 60–180 seconds before
close, subject to a time-scaled 0.10% spot buffer and a 0.04% adverse-momentum
veto.

The same-timestamp historical row looked unusually strong:

| period | trades | losses | mean after fee | 1c stress | clustered low |
|---|---:|---:|---:|---:|---:|
| Jul 2–Sep 8 | 117 | 0 | +3.091c | +2.091c | +2.916c |
| Sep 13–Oct 5 | 60 | 0 | +3.020c | +2.020c | +2.839c |

The later period missed the frozen 100-trade minimum, so the combined gate did
not pass. More importantly, a post-result timing diagnostic forcing Coinbase
to be one completed minute older changed the later period to:

- 21 trades, one loss;
- **-1.538c** mean;
- clustered lower 95% bound **-11.164c**;
- halves **+3.690c / -6.291c**.

The older period stayed positive under that lag, showing regime sensitivity
rather than a universal timing artifact. Because the main row uses
same-timestamp, non-atomic Coinbase and Kalshi candle closes, it is not a
profitable-strategy claim. A frozen direct-book prospective collector is the
governing evidence through 10:00 PDT.

### Exact external Mystic rule on post-report data

The inspected corrected PDF was dated August 18 and acknowledged that its
93–95c band, 1.75–5 minute window and 1c fill assumption were selected on the
May–August sample. Reproducing its fixed distance, three-minute momentum and
opposing-wick rules on the local September 13–October 5 period gave:

- 159 trades and three losses;
- **+1.978c** mean after an explicit 1c adverse slip and taker fee;
- extra-one-cent stress **+0.978c**;
- chronological halves **+2.559c / +1.404c**;
- best-10%-day deletion **+1.354c**;
- clustered lower 95% bound **-0.578c**.

This is a useful near-pass, not a promotion: clustered uncertainty crosses
zero, Coinbase substitutes for Binance, same-minute candles are non-atomic,
and the external intraminute stop cannot be reconstructed. A separate frozen
direct-book journal is collecting the exact entry rule through 10:00 PDT.

## External opening stink-bid simulator

The public `artyomderkach-bit/btc-15m-vol-backtest` repository shipped a
retrospective opening-ladder result that looked superficially positive:
56,820 submitted 1–10c YES bids, 41 simulated fill rows and +$17.22 reported
net P&L. Source and artifact inspection makes this non-promotable:

- only **14 independent markets** produced any simulated fill;
- the fill rate was **0.0722%**, with 56,779 cancellations;
- nine historical prints were reused to fill multiple ladder levels and one
  print filled all five levels, because the print's quantity is not decremented
  across orders;
- the fill test ignores `taker_side`, so a trade at or through the price is
  treated as hitting the resting bid even when the tape does not establish
  that direction;
- `max(1, int(trade_qty * volume_fill_pct))` upgrades every qualifying print,
  including fractional prints, to at least one simulated contract;
- orders are assumed live at the exact market-open timestamp with zero
  submission latency and no queue position;
- the summary's +$17.22 double-counts $0.41 of entry fees relative to its own
  +$17.63 bankroll change, an internal accounting inconsistency;
- reported Sharpe was only 0.236 and the source itself discloses approximate
  partial fills and minute-bar exits.

The positive point estimate is therefore a tiny, optimistic fill-model
artifact, not a venue-fill result. `analyze_live.py` requires private
credentials and was not run; no authenticated live fill ledger was committed.

## External XGBoost probability framework

The `SiddhaBasu/kalshi-bot` training report claims XGBoost Brier 0.131 versus
market 0.149 over 2,845 settled BTC windows and five walk-forward folds. The
chronological ticker split is better than the snapshot-row split used by some
other public frameworks, but the shipped result is still invalid as trading
evidence and has a direct causality defect:

- its aggregate metric weights **57,379 snapshots** sharing only 2,695 fold
  test-window outcomes rather than one fixed decision per independent market;
- the latest fold has 49.1 rows per window versus about 14.3 in the first four,
  so it receives 3.41 times the row weight per outcome;
- Coinbase rows are keyed by candle **open time**, but the features use the
  finalized candle close, volume, high and low;
- `merge_asof(... direction="backward")` joins those finalized values to a
  snapshot at or after the candle open, leaking up to one minute into all
  one-minute indicators;
- the same error is larger for `h1_log_return` and `h1_range_pct`: a snapshot
  during an hour receives the finalized close/range of that still-open hour,
  leaking up to one hour;
- the repository's own parity comments acknowledge that live features see
  provisional candles while offline recomputation sees finalized candles;
- the report contains no actual side asks, fees, fill model or executable P&L,
  and the underlying snapshot database and paper/live trade ledger are not
  committed.

The model's forecast score cannot be trusted until candles are made
point-in-time causal and evaluation is reduced to frozen per-window decision
times. Even a corrected Brier improvement would remain forecast-only until
tested against executable asks and fees.

### Causal settlement-basis probit transfer

A cleaner version of the external settlement-basis idea was preregistered and
tested locally. One probit was fit on 6,245 earlier BTC windows using only the
completed Coinbase candle available one minute before the final Kalshi quote.
The frozen fit estimated a +$5.15 offset and $12.79 residual scale.

Applying it at exactly T-60s to 2,104 later windows and trading only modeled
edge of at least 2c at the actual side ask produced:

- 459 trades, 223 wins and 236 losses;
- **-4.366c/contract** after fee;
- day-clustered lower bound **-7.409c**;
- halves **-4.816c / -3.918c**;
- extra 1c stress **-5.366c**;
- best-10%-day-deleted mean **-5.996c**;
- YES -4.802c and NO -3.684c.

This uses causal completed spot rather than same-minute future data, so the
failure is directly informative: estimating the residual index/remaining-move
distribution from binary outcomes did not create information beyond the final
Kalshi ask. The basis-only transfer is closed without retuning horizon, side or
threshold.

## BrandonOnChain GBM spot-divergence bot

The public `brandononchain/kalshibot` repository was audited at commit
`a01af900f96a6038db081de18ebbb1e98cb3cbb2`. Its production strategy and newer
real-capture simulator use BTC displacement from a spot observation at the
contract open, trailing realized volatility, remaining time, and a normal CDF
to buy a 35–65c Kalshi side when modeled expected value clears a threshold.
The repository itself labels its older synthetic backtest invalid and ships no
real capture or fill ledger.

The source's conservative capture-simulator rule was frozen before local
scoring: first four minutes, 15c modeled edge, one-cent adverse entry slippage,
fees, and one trade per market. On post-publication BTC windows it produced:

- 34 trades and 13 wins;
- **-13.441c/contract**;
- day-clustered lower bound **-26.003c**;
- chronological halves **-5.882c / -21.000c**;
- another one-cent stress **-14.441c**.

The source runtime `.env.example` diagnostic (8c threshold, ten-minute window)
was also negative over 263 trades: **-8.612c/contract**, clustered lower bound
**-16.456c**, first half **-17.695c**, second half **+0.402c**.

This is a causal minute-resolution Coinbase replication rather than an exact
Binance tick replay, but the failure is too large and broad for finer timestamps
alone to establish a buyable edge. The GBM spot-divergence claim is closed
unless an independently shipped direct-book capture demonstrates otherwise.
The code also computes one-hour trend multipliers but assigns the unmultiplied
edge to both trading decisions, so the advertised trend adjustment is currently
inactive.

## AstroTick momentum/orderbook and time-delay bot

The public `Razzleberryss/AstroTick` repository was audited at commit
`53abc23965c4459452052e3a2f501764fce1fe2c`. It contains two rule families:

1. a 60%-BTC-momentum / 40%-Kalshi-book-skew composite whose probability is
   constructed by adding `0.5 * composite` to the market midpoint; and
2. a "Reddit-style" time-delay rule that buys whichever side first reaches 90c
   with up to 14 minutes remaining and exits if that side's bid falls to 40c.

There is no performance evidence to score. The committed `trades.csv` contains
only its header, no historical capture is shipped, and no settlement P&L report
exists. The advertised OpenClaw integration also references
`openclaw_client.py`, but that file is absent from the repository. The
momentum/book construction is circular as a probability model because it starts
from the market midpoint and adds an uncalibrated score; the 90c favorite arm is
already dominated by the larger independent favorite tests in this corpus,
which were negative after executable asks and fees. AstroTick is classified
**NO EVIDENCE / NOT REPLICATION-ELIGIBLE**, not profitable.

## Defi-Ape Polymarket/Kalshi spread bot

The public `defi-ape/polymarket-kalshi-arbitrage-bot` repository was audited at
commit `dc3bd80a1c0d2db2d598045faf4b3601abc5c604`. Its default rule buys
Polymarket UP when Kalshi YES is 93–96c and at least 10c higher, or buys after
Kalshi is marked finished while the Polymarket book remains open.

It ships no trade ledger or backtest, compares labels rather than settlement
specifications, and does not require evidence that the two contracts resolve
from the same index. The local cross-index audit found 22 direction mismatches
in 2,105 paired windows, so "Kalshi finished" is not an oracle for a distinct
Polymarket contract. It also routes orders to Polymarket International, which
is outside the operator's US-person venue set. The claimed arbitrage is
therefore **INELIGIBLE AND BASIS-RISKED**, not a deployable Kalshi strategy.

## Late public-repository sweep

Five additional public repositories were inspected at their 2026-10-06 heads.
None supplies new profitable execution evidence.

### `darup67/kalshi-btc-agent`

Commit `a3f1ebaad385dec9a0595ad558649fa199a20256` contains a careful
spot-gap/remaining-volatility caller rather than an order bot. Its latest
shipped study covers 2,720 recorded windows. On the untouched half, confidence
gates from 0.80 through 0.99 earn roughly **-0.7c to -0.1c per call** after
recorded asks and the fee. Kalshi midpoint Brier is better than the model,
while the apparent minute-3 cell is explicitly treated by the author as an
unregistered hypothesis. This independently corroborates the corpus result:
high directional accuracy is already embedded in the ask.

### `darup67/market-lab`

Commit `84a14967a88896fceae5abc53a92d91091bbed23` ships an 8,817-window
BTC/ETH/SOL/XRP spot-and-volatility study. Buying model-estimated mispricing
loses **2.2c–2.5c per contract** after asks and fees, in both halves; favorite
variants also lose. The repository documents the same causal trap found in
this overnight audit: a Kalshi candle at minute `i` ends at that timestamp,
while a Coinbase/Binance candle stamped at that timestamp has only just
opened. Using its finalized close leaks one minute and can manufacture an
8c–15c apparent edge.

### `davidyangHY/kalshi-btc-mean-reversion`

Commit `3354cfeb4836abc20312a297fa7d757e70ce94d3` reconstructs 6,428
windows from about 81 million prints. Fixed-exit and take-profit mean reversion
lose approximately **4.9c–6.5c per contract**, with day-clustered intervals
below zero and held-out failures. Buying the favorite wins 76% but earns
approximately **-0.1c**, with an interval spanning zero. Minute VWAP still
ignores spread and queue, so the negative taker result is an optimistic upper
bound. The only proposed remaining thread is a zero-fee passive momentum order,
which the source explicitly leaves untested for fills and adverse selection.

### `nepalanurag/kalshi-btc15m-bot`

Commit `e1fad66958b0cdfbff320d726ddb790d505a7749` is an RSI/ATR bot
template with a validation harness, but its own documentation states that
historical Kalshi entry prices, spreads, queues and fills are modeled rather
than measured. No committed real-book profitability report or venue-fill
ledger exists. Its synthetic P&L therefore has no promotion authority.

### `NotACheeto/BTC15ARB`

Commit `2d9818e077a8548f9fc3e3d576a201569c7b531e` implements the correct
high-level Polymarket US/Kalshi exact-pair architecture, equivalence checks,
dual fees, concurrent legs and orphan recovery. It commits no observed
opportunity tape, fill ledger or settled P&L. Its paper execution path marks
both legs filled at displayed asks, so tests establish software invariants,
not economic availability. The overnight prospective exact-pair collector is
the governing evidence.

### `OffGrid0xDAO/cross-platform-arbitrage`

Commit `0c434a3e3a2e85d44f740d09bdc3782bfba27004` analyzes roughly
10.5 million February–March trades and reports large same-second
Polymarket/Kalshi synchronization. It is useful descriptive corroboration, not
deployable evidence:

- it uses Polymarket International Gamma/Data APIs, not Polymarket US;
- its 15-minute contracts use Chainlink versus Kalshi BRTI and disagree in
  5.7%–6.2% of windows, which the paper itself says makes the hedge non-risk-free;
- raw files needed for full reproduction are omitted;
- matching allows window offsets up to 120 seconds, while same-second and
  amount similarity cannot identify the anonymous Kalshi counterparty;
- its P&L is reconstructed from Polymarket wallets, not a joined two-leg fill
  ledger with exact asks, both venue fees, orphan exposure and settlement;
- the paper's own second week says latency P&L fell from +$0.49 to
  **-$0.31/trade** and two days were negative.

The work supports the structural facts that bots synchronize and that
cross-index "arbitrage" carries basis risk, while providing no eligible
current US-person strategy.

### Polymarket venue correction

The earlier `idea_lab/collect_polymarket_crossvenue.py` journal queried
Polymarket International (`gamma-api.polymarket.com` and
`clob.polymarket.com`), not Polymarket US. Its two positive rows averaging
+5.5c are **ineligible and explicitly retracted**. They have no strategy,
promotion, or deployment authority.

The corrected US-person comparison is Polymarket US
`cpc-btc-updown-15m-*` versus Kalshi `KXBTC15M`. Both specify BRTI 60-second
opening and closing averages. The exact-pair and hedged-maker collectors and
preregistrations supersede the invalid journal.

## Quantfirm wait-three / 75c favorite

The public `vinilpolepalli/quantfirm` repository is unusually useful because
its September 12 strategy-selection history, current execution code, and a
confirmed-production-fill settlement CSV are all public.

Commit `364984643cc35a2a69bea44399a0d1fd95d697ec` selected the live BTC/ETH
rule after sweeping an August 29–September 12 tape: wait three minutes, then
buy the first 75–92c favorite. The source reported +$80 for BTC and +$20 for
ETH on that selected two-week tape, while noting that 75c was the sweep peak.

A frozen transfer to the untouched local September 14–October 5 BTC panel
closed the rule:

- 2,022 first-qualifying actual-ask trades;
- **+0.155c/contract** after the exact one-contract fee, but day-clustered
  lower bound **-1.432c**;
- chronological halves **+0.385c / -0.075c**;
- extra 1c execution stress **-0.845c**;
- best-10%-day-deleted mean **-0.777c**;
- one-minute-later execution **-0.235c**, with the second half -0.507c.

The repository's public settlement ledger provides stronger external
counterevidence. Its code labels a row `adapter=live` only after a production
IOC response reports a qualifying fill. Restricting the committed CSV to
settlements after the 75c-rule commit and fill prices from 75c through 92c:

| asset | confirmed fills | contracts | cash P&L | c/contract | clustered low | halves |
|---|---:|---:|---:|---:|---:|---:|
| BTC | 240 | 3,921 | **-$53.65** | -1.368c | -8.708c | +3.854c / -6.588c |
| ETH | 245 | 3,811 | **-$167.05** | -4.383c | -8.210c | -3.751c / -5.015c |
| combined | 485 | 7,732 | **-$220.70** | -2.854c | -6.207c | +0.164c / -5.873c |

The ledger is external and cannot be independently authenticated, so it is
not local REAL-LIVE evidence. It nevertheless uses reported actual fills,
fees and settlements and directly reverses the source-selected backtest.
Canonical local artifacts are
`PREREG_external_quantfirm_wait3_favorite_20261006.md`,
`idea_lab/external_quantfirm_wait3_favorite_report.json`, and
`idea_lab/external_quantfirm_live_audit.json`.

## Additional public bot sweep

`seanrobenalt/kalshi-bot` adds no independent edge evidence. Its two order
rules are the already-closed combined-ask pair and a final-minute 90–97c
favorite. The pair compares asks to $1 without reserving fees, and the
favorite has no shipped fill ledger. Its optional three-CEX "lag" calculation
uses a fixed 45bp/65bp sigmoid with no time-to-expiry input; when enabled it
only gates the same pair/favorite orders and does not choose the side implied
by the signal. No backtest or settled P&L is included.

Sardine's October 6 public history supplied a separate 7,544-window causal
spot-distance/volatility probability model. A frozen no-refit transfer joined
that model to the local actual asks at 12, 8, 5 and 2 minutes remaining.
Buying the first side with at least 3c modeled net edge produced 1,708 trades
at **-2.405c/contract**, clustered lower bound **-4.814c**, halves
**-1.524c / -3.285c**, and extra-1c stress **-3.405c**. YES and NO were both
negative. The only positive checkpoint diagnostic, eight minutes remaining,
was +0.849c but was not a frozen standalone rule and cannot rescue the
strategy. Canonical artifacts are
`PREREG_external_sardine_model_oos_20261006.md` and
`idea_lab/external_sardine_model_report.json`.

The pre-September `oribarlevco-cell/kalshi-btc-15m-bot` divergence signal was
also transferred exactly at minute resolution: follow a Kalshi bid above 65c
or below 35c only when completed Coinbase spot is on the other side of the
opening strike. Excluding the candle stamped at the market close left only 22
causal trades. They averaged +4.327c, but the clustered lower bound was
-13.554c, halves were +11.836c / -3.182c, and deleting the best 10% of days
made the mean -1.459c. Twenty-one of 22 calls were YES and the lone NO lost
84c, so this is concentrated and insufficient rather than an edge.

## Canonical artifacts

- `PREREG_polymarket_transfer_20261006.md`
- `idea_lab/polymarket_transfer_report.json`
- `PREREG_polymarket_crossvenue_live_20261006.md`
- `idea_lab/polymarket_crossvenue_live_report.json`
- `PREREG_resolution_rider_replication_20261006.md`
- `idea_lab/resolution_rider_replication_report.json`
- `PREREG_resolution_rider_live_20261006.md`
- `idea_lab/resolution_rider_live_report.json`
- `PREREG_mystic_done_deal_oos_20261006.md`
- `idea_lab/mystic_done_deal_oos_report.json`
- `PREREG_mystic_done_deal_live_20261006.md`
- `idea_lab/mystic_done_deal_live_report.json`
- `PREREG_external_reclaim_oos_20261006.md`
- `PREREG_external_reclaim_fill_audit_20261006.md`
- `FINDINGS_external_reclaim_20261006.md`
- `PREREG_external_previous_momentum_20261006.md`
- `idea_lab/external_previous_momentum_report.json`
- `PREREG_external_dead_contract_oos_20261006.md`
- `idea_lab/external_dead_contract_report.json`
- `PREREG_external_mickey_remaining_oos_20261006.md`
- `idea_lab/external_mickey_remaining_report.json`
- `PREREG_external_sentient_fill_audit_20261006.md`
- `idea_lab/external_sentient_fill_audit_report.json`
- `PREREG_external_sentient_markov_oos_20261006.md`
- `idea_lab/external_sentient_markov_report.json`
- `PREREG_external_overbet_oos_20261006.md`
- `idea_lab/external_overbet_report.json`
- `PREREG_external_crossasset_regime_oos_20261006.md`
- `idea_lab/external_crossasset_regime_report.json`
- `PREREG_external_crossasset_regime_live_20261006.md`
- `PREREG_external_turbine_top_oos_20261006.md`
- `idea_lab/external_turbine_top_report.json`
- `idea_lab/external_turbine_collection_audit.json`
- `idea_lab/audit_external_aoi_framework.py`
- `idea_lab/external_aoi_framework_audit.json`
- `idea_lab/audit_external_stink_ml_framework.py`
- `idea_lab/external_stink_ml_framework_audit.json`
- `PREREG_external_settlement_basis_oos_20261006.md`
- `idea_lab/analyze_external_settlement_basis.py`
- `idea_lab/external_settlement_basis_report.json`
- `PREREG_external_fastclose_band_oos_20261006.md`
- `idea_lab/external_fastclose_band_report.json`
- `PREREG_external_calibration_transfer_oos_20261006.md`
- `idea_lab/external_calibration_transfer_report.json`
- `PREREG_external_brandon_gbm_oos_20261006.md`
- `idea_lab/analyze_external_brandon_gbm.py`
- `idea_lab/external_brandon_gbm_report.json`
- `PREREG_pm_us_exact_pair_live_20261006.md`
- `idea_lab/analyze_pm_us_exact_pair_live.py`
- `PREREG_pm_us_crossvenue_hedged_maker_20261006.md`
- `idea_lab/analyze_pm_us_crossvenue_hedged_maker.py`
- `PREREG_external_quantfirm_wait3_favorite_20261006.md`
- `idea_lab/analyze_external_quantfirm_wait3_favorite.py`
- `idea_lab/external_quantfirm_wait3_favorite_report.json`
- `idea_lab/audit_external_quantfirm_live.py`
- `idea_lab/external_quantfirm_live_audit.json`
- `PREREG_external_sardine_model_oos_20261006.md`
- `idea_lab/analyze_external_sardine_model.py`
- `idea_lab/external_sardine_model_report.json`
- `PREREG_external_oribar_divergence_oos_20261006.md`
- `idea_lab/analyze_external_oribar_divergence.py`
- `idea_lab/external_oribar_divergence_report.json`
