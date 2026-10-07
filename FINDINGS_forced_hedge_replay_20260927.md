# Forced-hedge replay — 2026-09-27

## Decision

Do not add forced taker hedging to the live bot from this sample. Strictly removing
all settlement residuals worsened combined BTC+ETH P&L even before taker fees. The
best ETH-only timeout improved gross P&L slightly, but the improvement disappears
at only 0.234 cents of taker cost per synthetic contract.

## Method

Replay all three book-residual live journals: one BTC run and two ETH runs. Freeze
the 162.81 real opening fills and use only causally received top-of-book states for
hypothetical FOK closes. Subtract one contract from displayed touch depth so the
replay never relies on our possible resting quote. Test 193 combinations of:

- maximum residual holds from immediate through 120 seconds;
- zero, one, or two-cent take-profit triggers;
- one, two, five, ten, or twenty-cent stop-loss triggers;
- 5.44, 20, and 50 ms order latency;
- zero or one-cent permitted price slippage;
- zero, 0.5-cent, and one-cent taker-fee sensitivities.

The no-exit replay exactly reconciles the source scores: 162.81 contracts and
-$0.6968 combined settlement P&L.

## Baseline

| Scope | Contracts | Organic paired P&L | Settlement residual | Total P&L |
|---|---:|---:|---:|---:|
| BTC | 50.18 | -$0.5780 | +$0.5670 | -$0.0110 |
| ETH | 112.63 | +$1.3518 | -$2.0376 | -$0.6858 |
| Combined | 162.81 | +$0.7738 | -$1.4706 | -$0.6968 |

The combined runs ended with 9.75 contracts riding to settlement.

## Strict pair-only result

The best policy that left zero settlement residuals was a 120-second maximum hold
with two-cent profit and two-cent stop triggers, 20 ms latency, and one cent of
permitted slippage. This is an in-sample ranking, not a selected trading rule.

| Metric | Result |
|---|---:|
| Organic paired contracts | 30.89 |
| Organic paired P&L | +$0.2489 |
| Synthetic exit contracts | 101.03 |
| Synthetic exit P&L | -$1.3567 |
| Gross total P&L | -$1.1078 |
| Total at 0.5 c/contract taker cost | -$1.6130 |
| Total at 1.0 c/contract taker cost | -$2.1181 |
| Settlement residual contracts | 0.00 |

By asset, the best strict pair-only BTC result was -$0.2946 gross versus the
-$0.0110 baseline. The best strict pair-only ETH result was the simpler 30-second
timeout at -$0.6143 gross versus -$0.6858 baseline.

## Timeout sensitivity

Immediate flattening closed every real fill but lost -$1.7829 gross before fees.
Waiting 30 seconds preserved more organic pairing:

| Scope | Organic paired P&L | Synthetic exit P&L | Settlement residual | Gross total | At 0.5 c fee |
|---|---:|---:|---:|---:|---:|
| BTC | -$0.3982 | -$0.2238 | +$0.6900 | +$0.0680 | +$0.0239 |
| ETH | +$0.8084 | -$1.4227 | $0.0000 | -$0.6143 | -$0.7674 |
| Combined | +$0.4102 | -$1.6465 | +$0.6900 | -$0.5463 | -$0.7435 |

The combined 30-second result is not pair-only because one profitable BTC contract
still rides to settlement. Its apparent gross improvement also becomes worse than
baseline at the 0.5-cent fee sensitivity.

For ETH, the 30-second timeout removes all 7.93 final residual contracts and improves
gross P&L by only $0.0715 while executing 30.61 synthetic taker contracts. A taker
cost above 0.2336 cents per synthetic contract erases the entire improvement.

## Interpretation

The maker edge requires waiting for the opposite passive fill. Crossing immediately
or on a hard deadline pays the bid/ask spread and converts much of the paired edge
into synthetic-exit losses. A forced exit can reduce settlement variance, but this
sample does not show positive pair-only economics.

The replay is optimistic because later real opening fills remain frozen even though
synthetic exits would change inventory, cancellations, and subsequent quotes. It has
only top-of-book depth, not a full aggressive sweep, and uses fee sensitivities rather
than claiming a fee-free executable result. All policy comparisons are in-sample over
13 markets.

## Next action

Do not modify or run the live strategy. Other crypto series have no equivalent
book-residual live journal in this dataset. A future test would first require a fresh
shadow implementation that suppresses later opening orders after synthetic exits,
models exact taker fees, and passes on new markets rather than this replay sample.
