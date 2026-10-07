# Audit — public Resolution Rider source

Pinned source: `reedjacobp/kalshi-trading-bot` commit
`217355f3babe0f6a764a360f9b759dbb7bd38594` (2026-04-23).

This is a source audit, not a new parameter search. The repository is relevant
because it publishes the exact 94–97c late-favorite rule replicated in
`PREREG_resolution_rider_replication_20261006.md`.

## Reproducible findings

- The strategy docstring claims an 18,316-trade walk-forward result with 12/12
  profitable months, but the repository does not ship the claimed trade
  ledger, tick panel, or a report from which those figures can be reproduced.
- Its optimization guide instead describes the available tick recordings as
  “currently Apr 8-11.”
- The optimizer's `ZERO_SLIPPAGE` environment switch defaults to `"1"`.
  Therefore the default taker simulation subtracts zero in the variable named
  `slippage_cost`; no direct fee function is called in that taker P&L path.
- Most importantly, the shipped `rr_params.json` contradicts a profitable
  15-minute conclusion under its own conservative fields. Every 15-minute cell
  has negative `cv_safe_total`:

| cell | validation trades | losses | raw CV profit | safe total |
|---|---:|---:|---:|---:|
| BNB 15m | 36 | 2 | -$3.00 | -$54.85 |
| BTC 15m | 175 | 6 | +$28.30 | -$19.85 |
| DOGE 15m | 48 | 10 | -$77.90 | -$180.44 |
| ETH 15m | 54 | 7 | -$43.70 | -$137.19 |
| HYPE 15m | 117 | 26 | -$206.60 | -$346.52 |
| SOL 15m | 61 | 16 | -$130.60 | -$285.58 |
| XRP 15m | 47 | 7 | -$48.20 | -$144.98 |

Only BTC has positive raw validation profit, and its conservative total is
negative before adding the local corpus's fresh timing failure.

## Decision

**No promotion.** The source's own current machine-readable artifact contains
no positive 15-minute cell after its conservative rare-loss adjustment. The
local historical 60-second-lag replication and prospective direct-book journal
remain the controlling tests.

Reproduction:

```bash
python3 idea_lab/audit_external_reed_resolution_rider.py
```
