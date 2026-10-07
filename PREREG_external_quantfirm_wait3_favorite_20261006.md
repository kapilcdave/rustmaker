# PREREG — external Quantfirm wait-3 / 75c BTC favorite

Frozen 2026-10-06 before scoring the local panel.

Source: `vinilpolepalli/quantfirm`, commit
`364984643cc35a2a69bea44399a0d1fd95d697ec`, published 2026-09-12. The
source selected its live crypto rule from an August 29–September 12 sweep:
wait three minutes, then buy the first BTC favorite offered from 75c through
92c and hold to settlement. It claimed +$80 for BTC on the selected
two-week tape, while explicitly warning that the threshold was the sweep
peak.

Replication:

- untouched local BTC panel begins 2026-09-14, after the source commit;
- inspect minute-close books from T+3 through T+14;
- take the first row where the richer actual side ask is in `[0.75, 0.92]`;
- one contract, exact one-contract Kalshi taker fee, hold to settlement;
- a one-minute-later same-side row is a timing diagnostic, not a retune.

Pass requires at least 100 trades, positive day-clustered 95% lower bound,
positive chronological halves, positive after an extra 1c execution stress,
positive after deleting the best 10% of days, and no day above 20% of gross
positive P&L. This paper replication cannot authorize orders.
