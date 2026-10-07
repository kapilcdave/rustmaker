# PREREG — BNB passive fill-floor pre-period replication

Frozen Tuesday, October 6, 2026 at 07:38:43 PDT, after the
September 14-October 5 BNB passive audit passed but before trade tapes were
selected or fetched for this test.

This is explicitly **post-selection temporal robustness**, not an independent
holdout. BNB and the passive implementation were already chosen.

Apply the unchanged BNB signal and strict-through maker mechanics to markets
opening from July 9, 2026 through September 13, 2026:

- 2.44 bps basis;
- prior 120 Coinbase one-minute returns;
- first 10-cent signal at minute 2/3/5/8;
- post the signaled side's bid after a two-second buffer;
- leave one contract to close;
- credit only a later aggressor print strictly through the limit;
- no at-price queue credit and no maker fee.

To bound API work without outcome selection, take a seeded sample of at most 12
signal markets per UTC day (`seed=20261006`) before fetching any trade tape.
Exclude truncated histories.

Report raw and one-cent-per-fill stressed P&L per submitted order, day-clustered
intervals, chronological halves and best-10%-day deletion. Agreement with the
later panel is supportive only; disagreement weakens the BNB candidate.
