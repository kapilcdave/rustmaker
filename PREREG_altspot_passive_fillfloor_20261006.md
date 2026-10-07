# PREREG — passive fill-floor audit of the frozen thin-altcoin spot-gap signal

Frozen 2026-10-06 at 07:13 PDT, before any trade tape for the selected
2026-09-14 through 2026-10-05 signal markets was fetched or joined.

This is an execution audit of the already selected
`PREREG_altspot_tilt_20261006.md` candidate, not a new independent outcome
holdout. The signal, population, basis, volatility estimate, decision minutes
and 10-cent threshold are unchanged.

At the first frozen signal in each NEAR, ZEC or HYPE market:

- YES signal: hypothetically join the displayed YES bid from the decision
  minute's closing candle.
- NO signal: hypothetically join the displayed NO bid, equal to one minus the
  displayed YES ask.
- The order is assumed submitted two seconds after the decision boundary and
  remains for one contract until market close.
- Credit a fill only on a subsequent public non-block aggressor print strictly
  through the order: a NO-taker print below the YES bid for a YES order, or a
  YES-taker print above the corresponding YES ask for a NO order.
- Prints at the order price are not credited because historical queue ahead is
  unknown. Truncated histories are excluded.
- Fill price is the posted limit, settlement is the published binary result,
  and the standard maker fee is zero for KX*15M.

The strict-through rule is a fill floor conditional on the candle close being a
causal order price. It does not prove that an order could have been submitted
at the exact historical close quote, and it does not test the original taker
arm.

Report fill rate, P&L per credited fill, P&L per submitted order, UTC-day
clustered intervals, chronological halves, asset concentration, latency
buffers of 2/5/10 seconds, and an additional one-cent adverse price stress.

The audit passes its paper maker screen only at the governing two-second
buffer if:

1. at least 100 strict-through fills survive;
2. the day-clustered lower 95% bound of P&L per submitted order is positive;
3. both chronological halves have positive P&L per submitted order;
4. every selected asset has non-negative P&L per submitted order; and
5. no asset contributes more than 60% of gross positive P&L.

A pass can only justify a prospective queue-aware shadow collector. It cannot
authorize live orders.
