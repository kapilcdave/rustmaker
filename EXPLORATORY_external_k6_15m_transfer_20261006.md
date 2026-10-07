# Exploratory K6/K14 transfer to BTC 15-minute direction contracts

This branch was generated after seeing the public K6/K14 hourly-threshold
strategy result, so it is explicitly exploratory and cannot be promoted from
this sample.

The public K6 rule trades an hourly BTC strike ladder when completed spot is
$50–$500 beyond a strike with at most 15 minutes left. YES additionally
requires annualized 15-minute realized volatility at least 0.33; NO requires it
below 0.65. It assumes an 88% win probability and enters only when that fixed
probability exceeds ask plus fee by at least 0.5c.

The transfer treats each `KXBTC15M` opening reference as the strike, uses only
the last completed Coinbase minute and the matching Kalshi side ask, and takes
the first qualifying decision per market after the source publication date.

Result:

- 593 trades, 433 wins;
- **-1.725c/contract** after fee;
- chronological halves **-1.693c / -1.758c**;
- YES **-2.663c**, NO **-1.370c**;
- one-additional-cent stress **-2.725c**.

The fixed 88% probability does not transfer from a multi-strike hourly ladder
to a direction contract whose strike is the opening reference. The arm is
closed without threshold or volatility retuning.

Canonical report: `idea_lab/external_k6_15m_transfer_report.json`.
