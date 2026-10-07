# Preregistration: BTC minute-3 cushion direct-book shadow

Frozen at Unix time `1791302699.288579` (2026-10-06 09:04:59 PDT).

This is a fresh paper check of the externally specified 1.04-sigma minute-3
BTC cushion rule. It does not place orders.

- Evaluate once at 182–195 seconds after each KXBTC15M open.
- Coinbase input is the completed one-minute candle stamped open+120s, never
  the still-forming candle stamped open+180s.
- Volatility is the sample standard deviation of the 60 completed
  pre-window one-minute dollar changes.
- Denominator is
  `sqrt(sigma_dollar^2 * (12 - 2/3) + 8.32^2)`, preserving the source's
  measured proxy-error term.
- Signal YES if `(spot-strike)/denominator >= 1.04`, NO if it is `<= -1.04`.
- Pay the displayed direct side ask and exact one-contract Kalshi taker fee.
- One decision and at most one signal per window.

Promotion requires at least 20 settled direct-book signals, positive raw and
extra-1c-stressed means, both chronological halves positive, a day-clustered
95% lower bound above zero after stress, and neither side above 70% of gross
positive P&L. The overnight sample cannot meet the count alone; it is a
prospective direction check only.

