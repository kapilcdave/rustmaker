# PREREG — multi-asset duplicate upper-tail direct-book capture

Frozen 2026-10-06 at 05:10 PDT before collecting this journal.

BTC, ETH, XRP, BNB and HYPE currently expose an hourly range series and an
hourly directional series whose upper-tail contracts can share the exact
ticker suffix, strike, close time, settlement source and rule text. For each
asset, pair only contracts that pass all of those identity checks.

Prospectively poll both public depth-one orderbooks. A candidate buys YES in
one duplicate and NO in the other, in either direction. Require:

- both requests span no more than 750 ms;
- both asks and at least one contract of paired displayed size;
- post-standard-taker-fee guaranteed edge of at least 2.0 cents;
- one first signal per asset-hour.

Report the maximum edge in every asset-hour even when no signal fires. The
historical candle study remains a non-atomic upper bound; this direct journal
is prospective paper evidence, not a fill. Promotion would require at least
25 independent asset-hours, positive edge after a further 1 cent per leg,
and a later authenticated atomic execution test. No order route is allowed.
