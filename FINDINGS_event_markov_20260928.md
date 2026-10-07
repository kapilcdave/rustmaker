# Event-level queue-reactive market making on the Ohio tape

**Date:** 2026-09-28. **Preregistration:**
`PREREG_event_markov_20260927.md`. **Code:** `research_event_markov.py`.
**Report:** `data/event-markov-20260927.json`. No orders were placed.

## Verdict

Do not deploy a new Markov, GBM or continuous-score Rust arm. None passed the
frozen prospective-shadow gate. The fast event stream contains a short-horizon
toxicity ranking, but that ranking did not produce stable settlement economics.

This is the same distinction behind the earlier 0.138-cent result: it was a
five-second midpoint mark, not a completed-pair or settlement edge. Faster
cancellation can improve that mark without improving the dollars left after the
market resolves.

## Data and verification

- The source is the 4,896,623-row full-event tape collected by the Rust engine
  on the Ohio VM: books, public trades, Coinbase receipts and shadow fills.
- Local and VM SHA-256 are identical:
  `9bd7819519a04266de7382ed8fbc358789f3999cccec6d00d09664e39e52c0a3`.
- The corrected common sample has 17,952 causal `base` fills across 48 close
  windows. The untouched pooled test has 5,025 fills and 99 markets in the final
  12 windows.
- All 108 markets used by the pooled or ETH-only held-out settlement rows were
  checked against Kalshi's market result. Final-book inference had zero
  mismatches and zero unresolved markets.
- The VM remains running in `us-east-2a`. No live process was started. After
  closing the model branch, a new ETH-only `base` shadow control was started as
  the remaining sample-size test.

## Pooled holdout

One standard error is shown after `±`.

| latency | policy | retained | 5s mark c/ct | settle c/ct | settle c/market |
|---:|---|---:|---:|---:|---:|
| 5.44 ms | all | 100.0% | -0.0632 ± 0.0496 | +0.0775 ± 0.1183 | +3.33 ± 5.08 |
| 5.44 ms | GBM ≥ 0 | 25.9% | +0.1008 ± 0.0725 | +0.8175 ± 0.7944 | +9.08 ± 9.10 |
| 5.44 ms | ridge | 53.3% | +0.0482 ± 0.0555 | -0.1574 ± 0.7060 | -3.60 ± 16.16 |
| 5.44 ms | Markov | 50.5% | +0.0349 ± 0.0583 | +0.7451 ± 0.5704 | +16.14 ± 12.33 |
| 10.00 ms | ridge | 53.4% | +0.0962 ± 0.0487 | -0.1978 ± 0.6289 | -4.53 ± 14.40 |
| 10.00 ms | Markov | 75.4% | +0.0034 ± 0.0537 | +0.0589 ± 0.2317 | +1.91 ± 7.48 |

The closest row is the 10-ms ridge five-second mark: its two-SE lower bound is
still slightly negative, while settlement is negative. The 5.44-ms Markov row
has a positive point estimate, but its two-SE settlement bound is also negative
and the effect nearly disappears at 10 ms. GBM keeps only one quarter of the
sample and clears neither uncertainty nor the frozen 50% capacity floor.

## ETH holdout

The ETH-only chronological diagnostic contains 1,301 test fills over 13
markets. The unfiltered base earns:

- five-second mark: **+0.1274 ± 0.1324 c/ct**;
- settlement: **+0.2442 ± 0.1177 c/ct**;
- settlement per market: **+18.91 ± 8.79 cents**.

That settlement point estimate is worth more collection, but it is not an
advanced-model result. At both 5.44 and 10 ms, the ETH-only Markov validation
selected **100% retention**: the learned controller's best instruction was not
to cancel any base fills. The ridge model improved the midpoint mark while
turning ETH settlement sharply negative. A pooled cross-asset Markov happened
to make the 5.44-ms ETH settlement point estimate much larger, but it collapsed
at 10 ms and did not reproduce in the ETH-only fit, so it is regime mix rather
than a deployable ETH controller.

## Why the advanced math did not reopen it

- **GBM** mostly changes fair value by less than the noise in a one-cent book.
  Its selected fills marked better at five seconds but did not establish a
  settlement edge or enough retained flow.
- **Binary Avellaneda–Stoikov** was already implemented as the exact Bernoulli
  terminal-payoff controller in `kalshi-scalp/FINDINGS_avellaneda_stoikov.md`.
  It has roughly one tick of quote authority and changes inventory placement;
  it does not repair adverse fill selection. Lower latency does not change that
  mathematical limit.
- **Queue-reactive Markov state** is the right new axis. It used receipt-time
  imbalance, midpoint transitions, same-side flow, touch depletion, Coinbase
  movement and time to close. It ranked some short-horizon toxicity, but the
  ranking was unstable at settlement and across 5.44/10 ms.
- **Continuous regularized state** produced the strongest 10-ms five-second
  mark, but the corresponding settlement row was negative. This directly shows
  why optimizing classification or markout is not enough.

## Decision

No new Rust model arm and no live order are justified. A fresh, prospective
**ETH base-control** collection was run on the Ohio VM to determine whether its
+18.91 c/market settlement row persisted. It used the exact
`kalshi-mm15-v8` build that produced the audited tape, `--only-base`, and no
order-placement mode:

```sh
./kalshi-mm15-v8 shadow --out data/eth-base-oos-20260928 \
  --minutes 720 --series KXETH15M --only-base --env-file .env
```

Remote PID `182443` was stopped cleanly at the user's request after 8h 31m; the
gzip passed integrity validation. The local copy is
`data/box/eth-base-oos-20260928/shadow_1790554130076.csv.gz`.

### Prospective ETH base result

34 settled markets, 4,361 fill messages and 3,752.7 contracts:

| metric | result |
|---|---:|
| total settlement P&L | **+$0.42** |
| settlement | **+0.011 c/contract** |
| settlement per market | **+1.234 ± 5.307 cents** |
| paired share | **99.4%** |
| paired P&L | **+0.079 c/pair** |
| residual P&L | **-4.298 c/residual contract** |
| 5-second markout | **+0.110 c/contract** |
| 60-second markout | **+0.112 c/contract** |

The earlier +18.91 ± 8.79 c/market ETH holdout did **not** reproduce. The new
prospective estimate is statistically zero and economically tiny. Almost all
contracts completed pairs, but the locked spread was only 0.079 cents per pair;
the small residual inventory was adverse enough to consume most of it. This
closes the ETH base-control promotion on the current evidence.

### Why it did not hold

The execution system did not degrade: both samples paired about 99.3% of their
contracts, and the prospective run had no dropped rows or write throttling. The
economics changed:

| component | original 13-market holdout | prospective 34-market run |
|---|---:|---:|
| paired share | 99.28% | 99.35% |
| paired P&L per pair | **+0.4443 c** | **+0.0787 c** |
| residual P&L per contract | **+3.3056 c** | **-4.2982 c** |
| median market P&L | **+18.46 c** | **+3.31 c** |
| winning / losing markets | 10 / 3 | 18 / 16 |

The old block was a favourable 13-market regime: round trips paid 5.6 times
more and its tiny residual inventory happened to settle favourably. In the new
sample the residual sign reversed and round-trip profit compressed toward zero.
The positive +0.110 c five-second markout still did not convert into settlement
P&L, because a midpoint mark is not an executable exit. The box delivered the
intended speed; speed did not make the temporary markout cashable.

## Reproduce

```sh
.venv/bin/python research_event_markov.py --self-test
.venv/bin/python research_event_markov.py data/box/shadow_spot/tape.csv.gz \
  --out data/event-markov-20260927.json
cargo test --offline
```
