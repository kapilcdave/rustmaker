# Preregistration: crypto-leader liquidity reward probe

Frozen 2026-09-28 before the first captured reward window. This is public-data
collection only: no authentication, orders, balances or capital.

## Question

Can a 1, 3, 5 or 9-contract quote earn a meaningful share of the new
`KXCRYPTOLEAD15M` liquidity pools after accounting for displayed competing
depth and the official distance discount?

## Capture

- Poll the public incentive-program endpoint every 30 seconds.
- During active `KXCRYPTOLEAD15M` programs, capture every outcome order book at
  one-second cadence with receipt timestamps.
- Run for three hours, covering up to twelve 15-minute windows.
- Preserve missing/error snapshots; do not silently forward-fill them.

## Promotion gate

No live reward quoting unless the offline replay estimates, under the official
score formula:

1. expected payout of at least $1 per settlement period after competition;
2. reward minus observed maker-fill settlement loss is positive;
3. the result holds in at least eight complete windows; and
4. no position path can exceed the available cash balance.

This probe does not authorize live orders.
