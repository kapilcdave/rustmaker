# PREREG — Polymarket US × Kalshi dual-maker complete set

Frozen Tuesday, October 6, 2026 at 09:10:01 PDT. Only paired-book rows with
`ts >= 1791303001.936524` have prospective authority.

This tests whether the exact BRTI identity can be harvested without crossing
either spread. Rest complementary one-contract buy limits on the two venues:

- buy PM-US YES as maker and Kalshi NO as maker; or
- buy Kalshi YES as maker and PM-US NO as maker.

At an error-free, open paired snapshot with `abs(pm_age) <= 10ms`, quote one
tick above each best bid when that remains below the ask; otherwise join the
bid. Use the venue tick (1c on PM-US and Kalshi's 0.1c extreme / 1c central
tick). Maker fee is zero, consistent with the exact-pair fee audit. Require the
two prices to total at most 97c and the same direction to persist on two
consecutive samples no more than 1.5 seconds apart. Take the first candidate
per 15-minute window.

Because quote touch is not a fill, count a leg only if a later displayed ask
for that same contract moves **strictly below** the resting limit. A paired fill
requires strict-through evidence on both legs before the window changes.
Record one-leg orphans and the time between strict-through witnesses. The
paired settlement margin is `1 - pm_price - kalshi_price`; matching-contract
settlement makes it direction-independent only when both legs fill.

This short journal cannot authorize trading. A descriptive pass requires at
least five independent paired fills, positive margins in both chronological
halves, paired strict-through on at least 60% of candidate windows, no more than
25% orphan windows, and a maximum fill-witness gap of 15 seconds. Queue
position, cancellation latency, and simultaneous inventory risk remain
unmeasured even if those gates pass.
