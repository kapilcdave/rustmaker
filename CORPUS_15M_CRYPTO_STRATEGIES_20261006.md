# Kalshi 15-minute crypto strategy corpus — 2026-10-06

This index separates realised profitability from forecast accuracy, paper
markout, and attractive-looking backtests. Historical profit is not a
guarantee. No strategy in this overnight study was armed or traded.

## Evidence ladder

1. **REAL-LIVE:** venue fills and settlements, correct fees, clustered
   uncertainty, concentration and capacity measured.
2. **REAL-PRINT UPPER BOUND:** venue execution occurred, but another trader got
   it; useful for rejecting a strategy, insufficient for promoting one.
3. **PAPER BOOK:** causal read-only snapshots; no fill claim.
4. **FORECAST ONLY:** outcome prediction without executable prices.
5. **CLOSED/VOID:** failed gates, invalid instrument, insufficient sample, or
   stopping-rule closure.

## Strategy matrix

| strategy | class | evidence | measured status | binding issue | canonical file |
|---|---|---|---|---|---|
| Altcoin 0.97 wing scalp | passive maker / rare-tail | **REAL-LIVE** | **Validated positive on its measured 10-day run:** +$21.83 / 150 settlements; day-clustered interval above zero | only ~$2.15/day at measured throughput; capital and correlated tail risk | `../kalshi-scalp/FINDINGS_wing_edge_vs_throughput.md` |
| Thin-altcoin spot-gap tilt (NEAR/ZEC/HYPE) | directional taker | historical same-close paper + prospective direct book | **OOS HISTORICAL PASS, PROSPECTIVE INSUFFICIENT:** historical 1,280 trades/+2.345c; fresh direct book 11 settled signals/+12.727c but second half -2.833c and below 20-signal floor | non-atomic historical quotes; 1c stress makes historical lower bound negative; fresh sample fails frozen promotion gate | `FINDINGS_altspot_tilt_oos_20261006.md` |
| BNB spot-gap transfer | directional taker / passive variant | historical secondary + strict-through audits + prospective direct book | **CLOSED/REGIME-UNSTABLE:** Jul–Sep replication -0.249c; fresh prospective 5/5 strict-through fills lost 14.420c/signal and 15.420c stressed | selected later regime reversed prospectively | `FINDINGS_altspot_passive_fillfloor_20261006.md` |
| External BTC/ETH direction × vol regime | directional taker | source-code join + causal timestamp audit + prospective direct book | **CLOSED CAUSALLY:** source-index join showed +3.832c/1,883 and passed, but used one minute of future Coinbase data; correctly shifted minute-5 row was +0.562c with negative lower bound/stress; causally delayed execution was -1.004c | Coinbase timestamps bar open while Kalshi timestamps bar close; source same-index merge is lookahead | `FINDINGS_external_bot_hypotheses_20261006.md` |
| External fresh-book high-model-edge rule | directional taker, mixed crypto universe including 15m | **external preregistered REAL-LIVE** | **CLOSED/REFUTED:** +6.5c/ct backtest became -$26.60 and -4.62c/ct over 576 live contracts; source halted at its precommitted kill rule | instrument split is unpublished, so this is counterevidence rather than a 15m-only estimate; early +1.88c/ct at 109 contracts was noise | `AUDIT_external_kyle_live_edge_20261006.md` |
| External maker-first high-edge variant | passive-maker probe | external preregistered live orders | **CLOSED/REFUTED:** 0/24 requested maker contracts filled versus 64% taker fill rate | one-cent-inside maker order did not execute within ten seconds and degraded loop freshness | `AUDIT_external_kyle_live_edge_20261006.md` |
| BrandonOnChain GBM spot divergence | directional taker | public source audit + post-publication causal minute OOS | **CLOSED:** conservative source simulator rule 34 trades/-13.441c; runtime-env diagnostic 263/-8.612c; both clustered lower bounds negative | source ships no real capture/fills; advertised trend multiplier is computed but not applied; spot model does not pay asks, fees and slippage | `FINDINGS_external_bot_hypotheses_20261006.md` |
| AstroTick momentum/book composite | directional taker | public source audit only | **NO EVIDENCE:** committed trade log has zero rows; no capture or P&L report; referenced OpenClaw client is absent | uncalibrated probability is market midpoint plus weighted score; no executable validation | `FINDINGS_external_bot_hypotheses_20261006.md` |
| AstroTick 90c time-delay favorite | favorite taker with stop | public source audit + corpus analogues | **CLOSED BY STRONGER COUNTEREVIDENCE:** no source trades; independent 90–97c/five-minute/final-minute favorite tests in this corpus lose after asks and fees | 90c threshold alone does not price rare losses; exit adds another spread/fee toll | `FINDINGS_external_bot_hypotheses_20261006.md` |
| Defi-Ape Kalshi→Polymarket spread/late-resolution | cross-venue directional | public source audit + local cross-index outcome audit | **INELIGIBLE/BASIS-RISKED:** no source ledger; 22/2,105 paired directions mismatch locally | Polymarket International unavailable to US person; contracts use distinct settlement sources | `FINDINGS_external_bot_hypotheses_20261006.md` |
| K6/K14 hourly-strike transfer to BTC 15m | spot-distance/volatility directional taker | exploratory post-source causal minute transfer | **CLOSED:** 593 trades/-1.725c; halves -1.693c/-1.758c; both YES and NO negative | fixed 88% win probability does not transfer from an hourly strike ladder to the opening-reference direction contract | `EXPLORATORY_external_k6_15m_transfer_20261006.md` |
| External 5-minute favorite-overbet | favorite taker | post-publication local candle-close proxy | **CLOSED:** 181 trades, -0.183c; clustered low -4.174c; delayed execution -0.856c | source aggregate edge vanished after actual asks/fees; first half and 1c stress negative | `FINDINGS_external_bot_hypotheses_20261006.md` |
| External Quantfirm wait-3 / 75c favorite | favorite taker | post-publication local actual-ask proxy + external confirmed-live ledger | **CLOSED:** local 2,022 trades/+0.155c but clustered low -1.432c and second half negative; source's public post-rule live ledger lost $53.65 BTC and $167.05 ETH | 75c was selected at the peak of an in-sample sweep; external live results reversed immediately and are not independently authenticated | `PREREG_external_quantfirm_wait3_favorite_20261006.md` |
| External Sardine spot-distance probability | model-value directional taker | 7,544-window external causal model + local actual-ask OOS | **CLOSED:** 1,708 trades/-2.405c; clustered low -4.814c; both halves and sides negative | source model probability does not survive executable asks and fees | `PREREG_external_sardine_model_oos_20261006.md` |
| External Oribar market-vs-spot divergence | informed-market favorite taker | post-publication local minute actual asks | **INSUFFICIENT/CLOSED:** 22 causal trades/+4.327c, but clustered low -13.554c, second half and best-day-deleted mean negative | 21/22 signals were YES; minute panel cannot reproduce source's subminute event count | `PREREG_external_oribar_divergence_oos_20261006.md` |
| Turbine top in-sample strategy | multi-entry directional scalp | audit of 1,276 public BTC backtests + frozen local replication | **CLOSED:** selected top rule had 90 completed cycles, -1.898c; clustered low -5.728c; second half -5.918c | winner selected from 1,276 single-window in-sample tests; below 100-cycle minimum | `FINDINGS_external_bot_hypotheses_20261006.md` |
| 40c/40c resting pair | two-sided passive maker | external quote-touch and trade-tape studies | **CLOSED/COUNTEREVIDENCE:** quote-touch -$2,115/802 windows; historical pair rate 64.3% vs 66.7% break-even | non-atomic fills and adverse selection; selected hour slice uncertain | `FINDINGS_external_bot_hypotheses_20261006.md` |
| External f6/F1 direction bot | directional taker | external matched live/paper ledger | **CLOSED EXTERNALLY:** 225 matched trades, 71% win rate but -$65.66 live after fees; later run unstable | win rate concealed payout asymmetry and $24.32 fees | `FINDINGS_external_bot_hypotheses_20261006.md` |
| External AoI latency paper bot | short-horizon directional scalp | shipped 70,535-row AoI tape + 51 paper round trips | **CLOSED:** +96c source gross became -2c after documented round-trip taker fees; -37c without largest trade | `market.updated_time` is not book-message age; synthetic ask fallback; no ticker IDs for clustering | `FINDINGS_external_bot_hypotheses_20261006.md` |
| External opening stink-bid ladder | extreme-price passive maker | shipped 56,820-order retrospective simulation | **INVALID FILL MODEL:** 41 simulated fills from only 14 markets; one print fills up to five levels; no queue or taker-side test | 0ms placement, 0.072% fill rate, shared print quantity not decremented, no live ledger | `FINDINGS_external_bot_hypotheses_20261006.md` |
| External BTC XGBoost framework | directional classifier | shipped five-fold report over 2,845 windows | **INVALID CAUSAL SCORE / FORECAST ONLY:** 0.131 reported Brier vs 0.149 market, but finalized 1m/1h candles are joined at open timestamps | within-bar future leakage; 57,379 row-weighted snapshots; no asks, fees or P&L | `FINDINGS_external_bot_hypotheses_20261006.md` |
| Causal settlement-basis probit | final-minute directional taker | 6,245-window earlier fit + 2,104-window later actual-ask OOS | **CLOSED:** 459 trades, -4.366c; clustered low -7.409c; both sides and halves negative | binary-outcome basis fit adds no buyable information beyond the T-60s ask | `FINDINGS_external_bot_hypotheses_20261006.md` |
| External snapshot-level ML framework | directional classifier | shipped 2,470-row sample and source audit | **INVALID DEFAULT TEST:** all 1,976 test rows are XRP snapshots; only 6–7 outcomes per asset; one ticker crosses split | rows are concatenated by asset before 80/20 split and hundreds share each target | `FINDINGS_external_bot_hypotheses_20261006.md` |
| External final-minute 90–97c favorite | late favorite taker | frozen rule on public 6,257-window panel + local OOS | **CLOSED:** public 539 trades/-0.971c; local 204/-1.424c; both clustered lows and stress negative | exact-60s candle is only a boundary proxy for source's strict `<60s`; rare losses overwhelm 94% wins | `FINDINGS_external_bot_hypotheses_20261006.md` |
| Full-population unconditional BTC taker | actual-ask directional baseline | external shipped 6,257-window panel, source reproduction | **CLOSED/CORROBORATION:** zero-fee YES buying insignificant at every 1–10 minute horizon; fee-adjusted means non-positive except uncertain +0.48c at one minute | displayed touch still overstates fills; no conditional novel information | `FINDINGS_external_bot_hypotheses_20261006.md` |
| Pre-period calibration-map transfer | market-calibration directional taker | logistic horizon maps fit on 6,257 pre-period windows, local OOS asks | **CLOSED:** 182 one-minute YES trades, -3.230c; clustered low -10.250c; both halves negative | calibration slope is not fresh information and did not persist economically | `FINDINGS_external_bot_hypotheses_20261006.md` |
| External Mystic 93–95c done deal | late favorite taker | post-report historical OOS + prospective direct book | **CLOSED/NOT PROMOTED:** 159 historical trades/+1.978c after stress but clustered low -0.578c; prospective journal completed 28/30 decisions and produced zero signals | same-minute non-atomic historical proxy; no fresh executable confirmation | `FINDINGS_external_bot_hypotheses_20261006.md` |
| External 94–97c resolution rider | late favorite taker | source audit + two historical periods + timing diagnostic + prospective direct book | **CLOSED/UNSUBSTANTIATED:** same-timestamp rows had 177 trades/0 losses, but 60s-lag later row -1.538c; all seven shipped public 15m cells have negative `cv_safe_total`; fresh journal produced zero signals | main row can see within-minute spot later than quote; public optimizer defaults to zero slippage and ships no ledger for its headline claim | `AUDIT_external_reed_resolution_rider_20261006.md` |
| External BTC three-lane reclaim | post-sweep directional scalp | post-publication local candles + external self-reported fills | **CLOSED:** 29 source-like OOS trades, -5.862c; both halves negative. Public three-lane file had 13 settlements/+9.871c but lower bound -1.759c | BTC-Fade reversed; public provenance unverifiable; tiny concentrated live-looking subset | `FINDINGS_external_reclaim_20261006.md` |
| Previous-result / 60s-momentum consensus | rollover directional taker | frozen external rule + local post-source candles | **CLOSED:** previous -2.610c, momentum -2.988c, consensus -3.069c over 1,112–2,099 trades; every half negative | simple directional accuracy cannot pay first-minute ask and fee | `FINDINGS_external_bot_hypotheses_20261006.md` |
| External final-two-minute near-strike late fade | reversal taker | frozen causal source-rule proxy across two periods | **CLOSED:** later 281 trades/-1.843c; earlier 683/-2.171c; both halves, clustered lows, 1c stress and best-day deletions negative | source's recovery sizing cannot repair negative per-contract expectancy | `FINDINGS_external_momotsanya_late_fade_20261006.md` |
| External three-rule BTC bot | early favorite / momentum / late dead-contract taker | exact post-publication local replication | **CLOSED:** dead-contract -1.750c/429; favorite -1.257c/921; momentum -0.860c/1,068 | source backtest omitted fees/spread and did not enforce its own minute rule | `FINDINGS_external_bot_hypotheses_20261006.md` |
| Sentient Markov/Brownian bot | directional taker | public external raw fill ledger + exact post-publication local replication + prospective direct book | **CLOSED:** current stack 192 trades/-6.563c, both halves negative; source buy-hold -$3,510.73; fresh journal made 61 decisions and produced zero signals | Markov confidence did not pay actual asks; external excerpt incomplete and later filter slice private | `FINDINGS_external_bot_hypotheses_20261006.md` |
| Final-seconds ZEC favorite | late favorite with print-backed fill floor | external public tick-level study | **INCONCLUSIVE:** reported 896 T-20s fills/+0.14% ROI, but exact rare-loss interval spans break-even | one loss repays hundreds of wins; external data not shipped; no local validation | `FINDINGS_external_bot_hypotheses_20261006.md` |
| Polymarket-to-Kalshi transfer | cross-venue directional signal | historical price-reference paper | **CLOSED:** 98 trades, -1.081c; lower bound -7.340c | consensus not buyable at Kalshi ask | `FINDINGS_external_bot_hypotheses_20261006.md` |
| Kalshi/Polymarket International opposite-side pair | cross-venue paired taker | broad outcome audit + invalid prospective journal | **INELIGIBLE/RETRACTED:** 22/2,105 index-direction mismatches; the two +5.5c live rows came from Polymarket International and have no authority | ineligible venue, distinct indexes, non-atomic execution | `FINDINGS_external_bot_hypotheses_20261006.md` |
| Polymarket US/Kalshi exact BRTI pair | cross-venue paired taker | preregistered prospective direct books | **CLOSED FOR SAMPLE:** 5/5 metadata pairs exactly equivalent, but zero persistent candidates over 525 fresh paired rows | no executable margin after exact one-contract fees and persistence gate | `PREREG_pm_us_exact_pair_live_20261006.md` |
| Polymarket US/Kalshi hedged maker | maker plus cross-venue taker hedge | preregistered prospective direct books | **CLOSED FOR SAMPLE:** zero persistent candidates over 510 fresh paired rows | maker fill uncertainty, queue, hedge slippage and orphan risk | `PREREG_pm_us_crossvenue_hedged_maker_20261006.md` |
| Polymarket US/Kalshi lead-lag taker | cross-venue directional taker | preregistered prospective direct books | **CLOSED FOR SAMPLE:** zero persistent signals over 493 fresh paired rows | directional value floor never cleared ask, fee, persistence and stress gates | `PREREG_pm_us_leadlag_taker_20261006.md` |
| Polymarket US/Kalshi dual-maker complete set | two-venue complementary passive maker | preregistered prospective direct books + strict-through audit | **CLOSED FOR SAMPLE:** zero prospective candidate windows over 2,917 rows; three retrospective pairs had 23–68s fill gaps | queue position and temporary orphan exposure; no timely paired fill evidence | `PREREG_pm_us_dual_maker_complete_set_20261006.md` |
| External BTC minute-3 1.04-sigma cushion | directional taker | external-source transfer on overlapping local history | **CLOSED:** 183 trades/+3.338c, but clustered lower bound -0.262c; post-source slice -1.643c/14 | source-selected minute; overlapping history; stress and fresh slice fail | `EXPLORATORY_external_cushion_m3_20261006.md` |
| BTC minute-3 cushion direct shadow | directional taker | preregistered prospective direct book | **CLOSED/NO SAMPLE:** three scheduled decisions, zero signals | overnight could not reach 20-signal gate and produced no executable confirmation | `PREREG_btc_cushion_m3_live_20261006.md` |
| BTC/ETH consensus gate on thin-alt spot-gap | directional taker filter | post-hoc historical exploration | **CLOSED:** 263 trades, -0.003c; clustered lower bound -5.703c; first half negative | post-hoc and discards the parent edge | `EXPLORATORY_altspot_major_consensus_20261006.md` |
| Coin Race complete sets and logical implications | guaranteed-payout multi-leg taker | historical candles + prospective direct books | **CLOSED:** no all-NO/four-NO/implication opportunities; one non-atomic all-YES anomaly only | five- or three-leg atomicity and no recurring spread | `FINDINGS_crypto_leader_structural_20261006.md` |
| Coin Race covariance Monte Carlo | cross-sectional directional taker | historical paper | **CLOSED:** 25 trades, -15.600c; both halves negative | Gaussian winner probabilities badly miscalibrated | `FINDINGS_crypto_leader_structural_20261006.md` |
| Coin Race held-out rank calibration | cross-sectional directional taker | chronological historical paper + prospective direct books | **CLOSED:** historical 31 trades/-1.065c; prospective one settled HYPE signal lost 15c | insufficient, negative and concentrated; direct journal does not rescue it | `FINDINGS_crypto_leader_structural_20261006.md` |
| 0.9c sub-cent sweep-backed maker | passive maker / rare-tail | fresh historical real-print fill floor | **FAILED FRESH OOS:** +$1.68/day stressed point, but second half negative and clustered interval crosses zero | only 5 adverse days, 692 joins, ZEC -$230 stressed | `FINDINGS_subcent_sweep_oos_20261006.md` |
| 0.9c no-rise filter | passive maker / causal path filter | fresh historical real-print fill floor | **CLOSED:** -$1.09/day stressed; both halves negative | causal filter retained the loss concentration | `FINDINGS_subcent_sweep_oos_20261006.md` |
| 0.9c maker + BTC tail hedge | passive maker + taker hedge | fresh historical paper overlay | **CLOSED:** 9 hedges, 0 wins; drawdown slightly worse | cheap BTC tails did not coincide with credited altcoin exposure | `FINDINGS_subcent_sweep_oos_20261006.md` |
| BTC 15m/hourly dominance | guaranteed-payout cross-instrument taker | historical candles + prospective direct book | **CLOSED:** 3/757 historical candidates; no prospective direct signal | non-atomic coincidences too sparse and concentrated | `FINDINGS_btc_hourly_dominance_20261006.md` |
| BTC hourly three-leg partition | guaranteed-payout complete set | historical candles | **CLOSED:** 5/757 candidates; stressed clustered interval crosses zero | three-leg execution and insufficient recurrence | `FINDINGS_btc_hourly_complete_set_20261006.md` |
| BTC range/directional duplicate tails | duplicate-contract complete set | historical candles + prospective direct book | **CLOSED:** zero positive historical hours; direct tops mirror | labels alias the same liquidity rather than independent books | `FINDINGS_btc_hourly_tail_duplicate_20261006.md` |
| Exact settlement B1/B2 | final-minute taker | REAL-PRINT UPPER BOUND + live paper | **CLOSED** | observed print side is strongly selected; adjacent-history replication negative | `FINDINGS_cf_exact_endgame_20261006.md` |
| Q1 overlap-decayed trend | final-minute taker | REAL-PRINT UPPER BOUND | **CLOSED** despite better Brier score | forecast improvement does not survive price/selection | `FINDINGS_cf_exact_endgame_20261006.md` |
| P1/P2 persistence | final-minute taker | REAL-PRINT UPPER BOUND + prospective direct book | **CLOSED:** final direct panel 16/16 settled signals at -8.938c, both halves negative; 1c stress -9.938c | transient-signal veto does not fix adverse selection | `FINDINGS_cf_exact_endgame_20261006.md` |
| P3 model agreement | final-minute taker | REAL-PRINT UPPER BOUND | **CLOSED** | trim and replication strongly negative | `FINDINGS_cf_exact_endgame_20261006.md` |
| C1 empirical transition | final-minute taker | REAL-PRINT UPPER BOUND | **CLOSED at 2c** | correctly calibrates apparent edge to roughly zero; 1c stress negative | `FINDINGS_cf_exact_endgame_20261006.md` |
| C2/C3 empirical blends | final-minute taker | REAL-PRINT UPPER BOUND | **VOID FOR SAMPLE** | only 1-7 trades per cell | `FINDINGS_cf_exact_endgame_20261006.md` |
| GLiNER2.5-Decide selector | classifier sidecar | synthetic gate | **CLOSED AS DEGENERATE** | `trade_yes` on 64/64 balanced states | `data/cf_exact/gliner_synthetic.json` |
| GLiNER2.5-Decide rule relation | catalog classifier sidecar | synthetic gate | **CLOSED AS DEGENERATE** | `same_event` on 80/80 balanced rule pairs | `FINDINGS_gliner_rule_relation_20261006.md` |
| GLiNER2.5 LoRA numeric gate | maker entry selector | held-out 5s markout | **CLOSED BY PREREG BAR** | tied/underperformed logistic; markout is not settlement P&L | `FINDINGS_gliner_gate_20261002.md` |
| Improve-one-tick penny jump | passive maker | paper/shadow | **CLOSED/CONTAMINATED** | stopping-rule recut and invalid control sign | `../kalshi-scalp/FINDINGS_penny_jump_scored.md` |
| Wide mid-band two-sided maker | passive maker | real-fill capture audit | **CLOSED** | real capture is ~1.2% of paper half-spread identity | `../kalshi-scalp/FINDINGS_armed_realisation.md` |
| Touch-resting/two-sided settlement maker | passive maker | real prints + settlement | **CLOSED** | capture cannot pay adverse selection and residual/exit | `../kalshi-scalp/FINDINGS_hold_to_settlement_maker.md` |
| GBM/lognormal direction | directional taker | historical forecast/execution | **CLOSED** | unique drift is sub-tick; venue prices conditional sigma | `../kalshi-scalp/FINDINGS_gbm_altcoin_15m.md` |
| Jev / language-model direction | directional taker | OOS | **CLOSED** | confidence without stable incremental information | `../kalshi-scalp/FINDINGS_jev_oos.md` |
| Book-imbalance direction | taker / cancel filter | real fills | **INFORMATION, NOT EDGE** | fee and adverse selection exceed signal | `../kalshi-scalp/FINDINGS_real_signal.md` |

## The only currently validated profitable crypto strategy in this corpus

### Altcoin 0.97 wing scalp

Historical measured shape:

- passive maker only; never cross the spread;
- quote the extreme favourite/underdog wing selected by the existing runner;
- 3-contract clips in the measured campaign;
- 150 venue settlements, 149 wins and one loss;
- +$21.832 total, +$0.1455 per settlement;
- day-clustered 95% interval +$0.0767 to +$0.1946 per settlement;
- 0.62 settlements/hour, approximately $2.15/day;
- one loss cost $2.913, roughly 18 mean wins to repay;
- the factor-notional cap blocked 55.8% of crypto candidates.

Interpretation:

- **edge passed; scale did not;**
- do not infer the hot-run return as a durable daily percentage;
- do not raise clip or loosen correlated-factor caps to chase income;
- adding more nominal series does not solve a capital-bound correlated window;
- all operational decisions remain governed by the existing armed repository,
  not this research tree.

## Strongest new candidate: thin-altcoin spot-gap tilt

The frozen NEAR/ZEC/HYPE rule is the only new arm that passed its preregistered
OOS gate:

- 1,280 fee-adjusted historical paper trades;
- +2.345c/contract with day-clustered lower bound +0.334c;
- every selected asset positive;
- intended BTC/ETH/SOL/XRP control pooled negative;
- both chronological halves and best-day deletion positive.

It is **not** in the validated-profitable set. Coinbase and Kalshi candle closes
share a timestamp but are not an atomic trade, and adding 1c of execution stress
makes the clustered lower bound negative. The direct-book collector is the
governing next evidence, not the historical point estimate.

## Exact-endgame lesson

The exact trailing settlement variable is highly predictive but not scarce
information. In live paper snapshots the book stays within one cent of every
tested model, and retrospective observed prints are the selected subset where
the aggressor side beats the unconditional forecast.

The reusable rule is:

> Never convert settlement accuracy into expected value without conditioning on
> the execution event itself.

For taker strategies, require all of:

- actual ask/bid, never OHLC or midpoint;
- venue fee at the entry price;
- source timestamp strictly before execution;
- one trade per market and frozen threshold;
- quarter-hour close-window clustering;
- equal-window and trade-weighted estimates;
- both chronological halves;
- best-10%-window removal;
- displayed depth and clip capacity;
- a fresh prospective capture.

## Overnight artifacts

Core:

- `PREREG_cf_exact_endgame_20261006.md`
- `FINDINGS_cf_exact_endgame_20261006.md`
- `collect_cf_exact_endgame.py`
- `collect_public_cf_endgame.py`
- `download_cf_rolling_history.py`
- `analyze_cf_rolling_endgame.py`

Novel arms:

- `analyze_cf_empirical_prints.py`
- `analyze_cf_persistence_prints.py`
- `analyze_cf_overlap_trend.py`
- `analyze_cf_counterparty_calibration.py`

Audits and prospective scoring:

- `audit_cf_rolling_history.py`
- `audit_public_cf_revisions.py`
- `analyze_public_cf_live.py`
- `resummarize_cf_trades.py`
- `tests/test_cf_rolling_endgame.py`

Data:

- `data/cf_exact/history/` — 2,000-market development panel
- `data/cf_exact/validation_history/` — 2,000-file adjacent replication
- `data/cf_exact/third_history/` — sealed counterparty-calibration holdout
- `data/cf_exact/live/public_20261006_021838.jsonl.gz` — prospective REST capture

## Standing stop rule

A new idea is not added to the profitable set because it has a positive point
estimate. It must clear execution, fee, stress, clustered uncertainty,
concentration, capacity, and independent replication. If it fails, preserve the
failure in the corpus instead of retuning the same window.
