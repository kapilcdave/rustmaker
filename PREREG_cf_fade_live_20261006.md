# PREREG — prospective exact-CF disagreement fade

Frozen 2026-10-06 at 07:03:35 PDT. Governing data begins at Unix second
`1791295415`; all earlier rows in the already-running raw orderbook journal are
development data and must be excluded.

## Origin

In the pre-cutoff direct-book paper journal, buying the side selected by the
exact-CF models was negative after settlement. A post-hoc diagnostic found that
buying the opposite side was positive for B2, Q1, P1 and P2. This can be a real
conditional-book effect, a small-sample accident, or an artifact of sequential
REST requests. The only admissible test is future raw rows.

## Frozen rule

- Universe: ETH, SOL, XRP and DOGE 15-minute markets already covered by
  `collect_public_cf_orderbook.py`.
- Model: P2, the B2 probability whose chosen side agrees both now and five
  source seconds earlier.
- Trigger: the original P2 model-selected taker side has calculated edge
  strictly above 2 cents after its taker fee.
- Action: buy the **opposite** side at its contemporaneous displayed ask and
  charge that side's standard taker fee.
- One first trigger per market.
- Exclude every market whose first P2/2c trigger occurred before the cutoff,
  every book observation completing before the cutoff, stale source points,
  one-sided books and unsettled markets.
- Evidence remains non-atomic REST paper. No fill is claimed.

## Reporting and gate

Report signals, settlements, mean and median cents per contract, chronological
halves, equal-close-window mean, per-asset P&L, displayed size and 0.5c/1c
additional stress.

The short prospective screen passes only with at least 20 settled signals,
positive mean and median, positive chronological halves, positive equal-window
mean after 1c stress, and no asset above 60% of gross positive P&L. Even a pass
cannot authorize live trading because the capture is short, single-session and
non-atomic.
