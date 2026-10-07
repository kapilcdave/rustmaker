# PREREG — Polymarket US × Kalshi exact BTC 15-minute pair

Frozen Tuesday, October 6, 2026 at 08:55:12 PDT. Only rows with local receipt
time `ts >= 1791302112.315213` are eligible for the prospective score.

This corrects the earlier `polymarket_crossvenue_live` journal, which queried
Polymarket International (`gamma-api.polymarket.com` / `clob.polymarket.com`).
That venue is not eligible for this US-person operator and uses a different
settlement index. The earlier two positive paper rows have no promotion
authority.

The admissible comparison is Polymarket **US** `cpc-btc-updown-15m-*` against
Kalshi `KXBTC15M`. Both contracts use the same quarter-hour window and BRTI
60-second opening/closing averages. The journal is read-only and uses the
authenticated Polymarket US market-data WebSocket plus Kalshi's public
orderbook endpoint. No order route is present in the collector.

For every fresh paired book:

1. require `abs(pm_age) <= 10ms`, open PM state, and at least one contract at
   both executable top levels;
2. calculate both complete-set directions:
   - PM US YES ask + Kalshi NO ask;
   - Kalshi YES ask + PM US NO ask;
3. charge one-contract taker fees exactly as implemented by the venues:
   Kalshi rounds up to a cent per order and PM US uses half-even cent rounding;
4. call a raw candidate only at `net >= 3c`, so a fixed additional 1c stress
   on each leg still leaves at least 1c;
5. require the same direction to qualify on two consecutive samples no more
   than 1.5 seconds apart;
6. take at most the first persistent candidate per quarter-hour window.

Report candidate windows, raw and 1c-per-leg stressed edge, minimum displayed
size, and whether the edge remains nonnegative on the next sample. A short
screen needs at least five independent windows, positive stressed edge in both
chronological halves, at least 80% next-sample survival, and at least one
contract displayed on every candidate.

Passing would authorize only a longer two-venue atomicity/legging study. It
does not authorize trading: two independent taker calls cannot be atomic, and
the second leg can disappear after the first fills.
