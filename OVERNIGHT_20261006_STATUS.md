# Overnight Kalshi 15-minute crypto research status

Final checkpoint: 2026-10-06 10:01 PDT. The requested 10:00 PDT research
window is complete.

No order route has been called. All new collectors are public/read-only except
one attempted authenticated market-data WebSocket, which returned HTTP 401,
wrote no Kalshi book/trade rows and was stopped.

## Completed

- Exact-CF endgame family: closed. The direct prospective orderbook panel is
  also negative after settlement.
- GLiNER2.5-Decide numeric and rule-relation gates: closed as degenerate.
- BTC 15m/hourly dominance: closed, 3/757 historical candidates and no direct
  prospective signal so far.
- BTC hourly three-leg complete set: closed, 5/757 historical candidates and
  stressed clustered interval crossing zero.
- BTC duplicate upper tails: closed, zero positive historical hours; direct
  books mostly mirror.
- Fresh sub-cent 0.9c OOS: failed promotion despite a +$1.68/day stressed point
  estimate. Second half negative, only five adverse days, clustered interval
  crosses zero.
- Causal no-rise sub-cent filter: closed negative.
- BTC tail hedge: closed; 9 hedges, 0 wins.
- Thin-altcoin spot-gap rule: **passed its frozen historical OOS gate** on
  NEAR/ZEC/HYPE, 1,280 trades, +2.345c/contract and clustered lower bound
  +0.334c. It remains paper because 1c execution stress makes the lower bound
  negative.
- BNB historical secondary was strongly positive and is in a fresh prospective
  direct-book journal only; it has no historical decision authority.
- The external BTC reclaim bot failed an exact post-publication replication:
  29 trades at -5.862c, both halves negative.
- Previous-window, one-minute momentum and consensus rules all failed over
  1,112–2,099 post-source trades.
- A February public three-rule bot also failed exactly: late dead-contract
  -1.750c/429, early favorite -1.257c/921, and mid-window momentum
  -0.860c/1,068.
- An external five-minute >80% favorite study failed on 181 post-publication
  local trades: -0.183c after fees and -0.856c with delayed execution.
- An external BTC/ETH direction-volatility stack appeared to pass at +3.832c
  over 1,883 trades, but a timestamp audit proved its source merge uses one
  minute of future Coinbase data. A correctly shifted same-entry row was only
  +0.562c with negative uncertainty/stress diagnostics; causally delayed
  execution was -1.004c with both halves negative.
- A 1,391-strategy Turbine dump contained 1,276 single-window in-sample BTC
  backtests. Its top reported rule failed frozen local replication at
  -1.898c/contract over 90 completed cycles.
- An independent 40c/40c pair-tape study was negative, and a public matched
  live/paper f6 bot lost $65.66 over 225 live trades despite a 71% win rate.
- A shipped BTC AoI paper ledger's +96c gross became -2c after documented
  round-trip fees; its stale `market.updated_time` metric is not orderbook
  latency. A separate ML framework's default split was XRP-only with only
  6–7 independent outcomes per asset despite 2,470 snapshot rows.
- A public final-minute 90–97c favorite rule failed twice: 539 trades at
  -0.971c on an independent full-population panel and 204 at -1.424c locally.
- A separate 6,257-window public study reproduced locally: unconditional
  actual-ask BTC buying was insignificant even with fees set to zero at every
  reported 1–10 minute horizon.
- Transferring that panel's pre-period logistic calibration maps to local OOS
  actual asks failed: 182 trades at -3.230c, both halves negative.
- A public opening stink-bid simulator's +$17.22 claim came from only 14
  independent filled markets. Its simulator reuses one print across up to five
  ladder levels, ignores taker side, assumes 0ms placement/no queue and has no
  authenticated live ledger.
- A separate BTC XGBoost report's 0.131 Brier score is causally invalid:
  finalized one-minute and hourly candle values are joined to snapshots at
  candle-open timestamps, and 57,379 correlated rows are weighted instead of
  fixed per-window decisions. It reports no executable P&L.
- A causally corrected settlement-basis probit was fit on 6,245 earlier BTC
  windows and frozen on 2,104 later windows. Its 459 actual-ask trades lost
  4.366c/contract, with both sides and halves negative.
- The public BrandonOnChain GBM spot-divergence bot failed a post-publication
  causal replication. Its conservative capture-simulator rule lost
  13.441c/contract over 34 trades; the wider runtime configuration lost
  8.612c over 263. Its advertised one-hour trend multiplier is not applied by
  the trading code.
- AstroTick shipped no completed trades, no capture, and no P&L evidence; its
  referenced OpenClaw client is absent. A Defi-Ape cross-venue bot shipped no
  ledger, routes to an ineligible venue, and assumes away the 22/2,105
  cross-index direction mismatches measured locally.
- A novel transfer of the public K6/K14 hourly spot-distance/volatility rule to
  BTC 15-minute direction contracts failed over 593 causal minute decisions:
  -1.725c/contract, both halves and both sides negative.
- The public Sentient raw fill population was deeply negative, and its current
  deterministic Markov/Hurst stack failed a post-publication local replication:
  192 trades at -6.563c, both halves negative.
- Polymarket and Kalshi settlement directions agreed in 2,083/2,105 windows,
  not 100%; the 22 near-strike index mismatches rule out a risk-free
  opposite-side characterization.
- The public Quantfirm wait-three / 75c favorite failed on 2,022 untouched
  local trades: +0.155c point estimate, but -1.432c clustered lower bound,
  negative second half and negative stress. Its own public production-IOC
  ledger was worse after the selected-rule commit: BTC -$53.65 and ETH
  -$167.05 over 485 confirmed fills.
- A separate 7,544-window Sardine spot-distance/volatility model also failed
  when joined to local executable asks: 1,708 trades/-2.405c, both halves and
  both sides negative.
- The pre-September Oribar market-vs-spot divergence produced only 22 causal
  minute trades. Its +4.327c point estimate had a -13.554c clustered lower
  bound, a negative second half and a negative best-day-deleted mean.
- The public source behind the 94–97c Resolution Rider does not substantiate
  profitable 15-minute deployment. Every shipped 15m cell has negative
  `cv_safe_total`; its optimizer defaults `ZERO_SLIPPAGE=1`, and the claimed
  18,316-trade/12-month result has no shipped supporting ledger.
- A separate public preregistered live rule requiring at least 7c post-fee
  model edge, spread at most 3c and book freshness under two seconds reversed
  from a +6.5c/contract backtest to -$26.60 and -4.62c/contract over 576 live
  contracts. Its maker-first arm filled 0/24 requested contracts. The source
  honored its precommitted kill rule and remains halted.
- A newly found final-two-minute near-strike late-fade source rule also failed
  a causal two-period replication: 281 later trades at -1.843c/contract and
  683 earlier trades at -2.171c, with both halves and all stress diagnostics
  negative. Its recovery/martingale sizing was excluded because sizing cannot
  repair negative expectancy.

## Final prospective reads at 10:00 PDT

- Thin-alt spot-gap: 169/169 complete decisions, 11 settled signals,
  +12.727c mean, but the second half was -2.833c and the frozen 20-signal floor
  was not reached.
- BNB passive transfer: 5/5 strict-through fills, -14.420c/signal and
  -15.420c after the extra 1c stress.
- Polymarket US/Kalshi: 5/5 sampled contracts were exact BRTI equivalents.
  Exact taker, hedged maker, lead/lag, batch-fee and dual-maker tests all
  produced zero prospective candidates over 443–525 eligible paired/depth
  rows.
- Exact-CF direct panel: 16/16 settled signals at -8.938c, both halves
  negative, and -9.938c after 1c stress.
- Coin Race rank calibration: one settled HYPE signal, -15c. Complete-set,
  implication and hourly-dominance journals found no opportunity.
- BTC minute-3 cushion: three decisions and zero signals.
- Mystic: 28/30 complete decisions and zero signals. Resolution Rider:
  17/25 complete decisions and zero signals.
- Sentient Markov/Hurst: 61 decisions and zero signals.
- Duplicate-tail and hourly dominance orderbook journals: zero signals.

No fresh strategy cleared its frozen execution, fee, stress, clustered
uncertainty, concentration, capacity and minimum-sample gates. The old 0.97
altcoin wing scalp remains the corpus's only validated positive strategy.
