# PREREG — external "resolution rider" replication

Frozen Tuesday, October 6, 2026 at 07:57:48 PDT, before scoring this rule.

An open-source Kalshi bot survey repeatedly proposed a "done deal" or
"resolution rider": buy the 94–97c favorite during the final 10–180 seconds
only when spot is safely beyond the strike and short momentum is not adverse.
This test freezes a minute-data-compatible version of that outside hypothesis;
no parameter is selected from the local outcome data.

## Frozen rule

BTC only. At the historical candle timestamps 180, 120 and 60 seconds before
close, in that order:

1. derive the executable favorite ask from the YES ask or `1-YES_bid`;
2. require the favorite ask in `[0.94, 0.97]`;
3. use the same-timestamp completed Coinbase minute close as spot;
4. require signed distance from strike of at least
   `0.10% * sqrt(seconds_left/60)` in the favorite's direction;
5. calculate the latest one-minute Coinbase return; reject YES if it is below
   `-0.04%` and reject NO if it is above `+0.04%`;
6. buy one favorite contract at the ask, charge the standard taker fee, hold
   to settlement and take at most the first qualifying trade per market.

The same-timestamp spot close and Kalshi candle quote are non-atomic. Score two
fixed periods separately:

- later: the September 13–October 5 OOS download;
- earlier replication: the July 2–September 8 archive.

Report trade count, fee-adjusted mean, UTC-day-clustered interval,
chronological halves, best-10%-day deletion, loss rate, and one-cent execution
stress. Promotion requires at least 100 trades in each period, positive
one-cent-stressed means and clustered lower bounds, positive halves and
best-day deletions in both periods, and no day above 20% of gross positive
P&L. Failure in either period closes the rule.

## Post-result timing diagnostic (not part of the gate)

The frozen run produced no losses, making same-timestamp non-atomicity the
first concern. After viewing that result, add one explicitly post-hoc
diagnostic: repeat the exact rule while forcing the Coinbase state to be 60
seconds older than the Kalshi candle timestamp. This cannot promote the rule
or replace the prospective direct-book test. It exists only to reveal whether
the attractive row depends on within-minute timing.
