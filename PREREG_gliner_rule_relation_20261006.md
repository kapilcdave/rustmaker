# PREREG — GLiNER2.5-Decide as a rule-relation triage model

Frozen 2026-10-06 before running the model on this task.

The numeric trading-action experiment was degenerate (`trade_yes` 64/64).
This experiment uses the checkpoint for its advertised operational
classification role instead: triaging pairs of contract rules for a
deterministic settlement-relation scanner.

Labels:

- `same_event`
- `first_superset`
- `first_subset`
- `different_settlement`

The balanced synthetic gate crosses BTC/ETH/SOL/XRP rule templates, equal and
unequal thresholds, same and unequal close windows, and same and unequal
indices.  There are no price, outcome, or P&L fields in the input.

Pass requires:

- all four labels emitted;
- at least 80% exact-label accuracy overall;
- at least 70% recall for every label.

A pass authorizes only catalog triage.  Deterministic parsing must still verify
the index, settlement timestamp, averaging window, comparator and strike
before any pair enters a trading screen.  A failure closes GLiNER for this
role; it does not affect the deterministic hourly-dominance strategy.
