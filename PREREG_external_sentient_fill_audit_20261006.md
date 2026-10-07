# Preregistration — Sentient public fill-ledger audit

Frozen: 2026-10-06 08:34 PDT, before reconstructing ledger P&L or applying
subsets to the public fill file.

Source: `Julian-dev28/sentient-market-reader`, public commit
`bfbf470f49cc7585f59fde02f4501da0ae480e37`. The repository history publicly
contained `python-service/live_fills.json`,
`live_analysis_cache.json`, and `live_analysis_results.json`; commit
`a34c4b745cf38f032433f43a11d18b36bd3b0029` later removed the runtime files
from the current tree.

## Frozen audit

1. Use all public `KXBTC15M` fills whose ticker has a known `yes`/`no` outcome
   in the public cache.
2. For each raw fill, use its reported side price, contract count, action, and
   `fee_cost`.
3. Reconstruct cash exactly:
   - buy: `-count * side_price - fee_cost`;
   - sell: `+count * side_price - fee_cost`.
4. Track YES and NO inventory separately. At settlement, add one dollar for
   each net contract on the winning side.
5. Report:
   - the ledger across every outcome-covered ticker;
   - a self-contained subset whose chronological inventory never becomes
     negative on either side;
   - the repository's independent-buy-hold result for comparison.
6. Aggregate fills to ticker and report total P&L, unique tickers, dates,
   negative-ending-inventory count, day concentration, and day-clustered mean
   P&L per ticker.

The audit will not infer that these fills were generated solely by the
repository's current strategy. The current README's 147-trade April 19–22
filter claim is not testable from a public fill file ending earlier and will
be labeled unverified if the date audit confirms that mismatch.

No public external ledger can promote a local strategy to REAL-LIVE status
because ownership, completeness, and provenance cannot be independently
authenticated.
