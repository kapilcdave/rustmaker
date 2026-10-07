# PREREG — prospective BNB transfer of the frozen altspot rule

Frozen 2026-10-06 after BNB's pre-declared secondary historical score was
observed positive, and before any BNB direct-book journal was collected.
This is therefore a prospective replication of a discovery, not confirmation
from the historical panel.

Use the exact `PREREG_altspot_tilt_20261006.md` rule with BNB's already frozen
2.44 bps basis: prior-120-minute volatility, decision minutes 2/3/5/8, first
10-cent fair-versus-executable-ask gap, one paper trade per market, standard
taker fee. Require the same 2–15 second decision timing and direct two-sided
public orderbook as `PREREG_altspot_tilt_live_20261006.md`.

Report every decision and settled paper P&L. At least 20 settled signals,
positive mean and positive chronological halves are required for a paper
screen pass. Non-atomic public REST evidence cannot authorize an order.
