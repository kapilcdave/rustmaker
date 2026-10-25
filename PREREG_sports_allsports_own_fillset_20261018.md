# PREREG — all-sports maker, OUR fill set, before any arm (2026-10-18)

Frozen 2026-10-18 ~08:40Z, before the scored sample exists (first 5 min: 10 fills, not read).

**Question.** Does `sports-live --series auto` (two-sided, 1–3c books, join below 3c, one tick
inside at 3c+, fee-free series only) get fills that are NOT adversely selected, at our queue
position and latency? The 2026-09-13 study says no (AS ≈ 0.96 × spread, net −2.29c at 3–4t).
Per `never-arm-on-a-venue-average-edge`, this decides it for $0 before any order is sent.

**Data.** Dry runs on the us-east-2 box (`sports-dry-s0`, `sports-dry-s3`), binary
`kalshi-mm15-ed25519-47bffe5` (rustmaker 47bffe5, sha256 4a60d081…), both shards = every
fee-free sports series, `--max-markets 300` per shard, 08:37Z 10-18 → ~03:35Z 10-19.
Journals `data/sports-dry-all-s{0,3}-20261018/`. Scorer `score_sports_fillset.py`,
`--latency-ms 30` primary (our ~8 ms + venue lag + feed lag, rounded up), 100 ms sensitivity.

**Primary statistic.** OUR fills, clamp queue, 5-minute markout `mk300` (= spread captured −
adverse selection), equal-weight per fill, bootstrap CI clustered by event (game).

**PASS (all five, else do not arm):**
1. `mk300` clamp **CI lower bound > 0**.
2. `mk300` strict-queue point estimate **> 0** (the result must not depend on cancel credit).
3. `mk300` at 100 ms latency point estimate **> 0**.
4. Power: **≥ 200 fills on ≥ 40 events**. Below that → INCONCLUSIVE, extend; never arm on it.
5. Concentration: no single series carries **> 50%** of total `mk300` P&L.

**Reported, not gated:** venue maker-side `mk300` in the same markets and the gap ours − venue
(the selection penalty); `mk5/30/60`; AS = mk − capture by horizon; per-series table; settlement
markout (`--settle`) — hold-to-settlement is not the design, so it does not gate.

**Known limits.** A dry run never updates position, so it keeps quoting both sides after a
"fill" where the live engine would quote only the exit; this scores ENTRY fills, not round
trips. Queue position is modelled (displayed size ahead at post, prints eat it; clamp also lets
displayed-size drops eat it). Kalshi exposes no historical resting depth, so this cannot be
re-scored later from any other source — the journal is the only record.

**Prediction (recorded):** FAIL on 1. Expect `mk300` ≈ −1 to −2.5c, the 09-13 band.

**If PASS:** shard 0 holds $0.00 and shard 3 $2.52 (10-18 08:33Z); arming needs an operator
funding decision and the live engine's caps (`--max-loss-c`, `--cum-max-loss-c`) set as a
percent of the funded balance, not hardcoded.
