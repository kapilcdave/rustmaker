# Preregistration — external final-minute 90–97c favorite

Frozen: 2026-10-06 before calculating this rule on either historical dataset.

Source rule:

- repository: `seanrobenalt/kalshi-bot`
- commit: `9698c3dfe8ce811f1ec16ba6eb76196810e4d871`
- public rule: with fewer than 60 seconds remaining, buy whichever side has
  an ask in the inclusive 90–97c band.

## Historical timing proxy

The available historical books are one-minute candles, so the governing
historical proxy is the actual side ask at exactly one minute remaining. This
is earlier than the source's strict `<60s` runtime check and therefore cannot,
by itself, promote the source rule. It can reject the broad economics.

For each settled BTC 15-minute market:

1. Read the one-minute-left YES bid and ask.
2. Derive `NO ask = 1 - YES bid`.
3. Choose the more expensive side. If its ask is in `[0.90, 0.97]`, buy one
   contract.
4. Hold to settlement.
5. Subtract the local conservative one-contract taker fee.
6. Enter at most once per market.

No spot, momentum, volatility, hour, direction or price subfilter may be added.

## Frozen samples

Score separately:

1. the public 6,257-window June 27–September 3 full-population file at commit
   `ed3230f21b0cb2b66f5b870e94695c1fd1e3bdc2`;
2. the local September 14–October 6 OOS BTC candle panel.

## Pass gate

Both samples must independently have:

- at least 100 trades;
- positive mean after fee and an extra 1c stress;
- positive day-clustered 95% lower bound;
- positive chronological halves;
- positive mean after deleting the best 10% of days;
- no day above 20% of gross positive P&L.

Failure of either sample closes the proxy. Passing both still leaves the
strict-subminute execution question for a prospective direct-book journal.

