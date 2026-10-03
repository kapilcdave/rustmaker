# Pre-registration: ETH / SOL gated 15M maker (frozen 2026-10-03, before any ETH- or SOL-only data)

## The adverse prior, stated first and in full

This reopens a cell inside a branch whose own record says not to. Quoted verbatim from
`kalshi-15m-real-fills-queue-clears-by-cancels`:

> "Overall: the 15M crypto gated maker has not been positive in any of ~730 shadow market-runs;
> **do not reopen on a speed or queue-model argument** — only Premier-tier access (FIX market data /
> PrivateLink) changes the seat."

And the per-series route has already been screened once and failed:

> "Per-series OOS check 2026-09-24: series picked positive in R1 (**NEAR, SOL**) scored −3.4 ± 8.6
> c/mkt in R3-R4 vs rest −8.2 ± 5.2 — **early positives regress**. NEAR alone is 3/3 windows
> positive, OOS +13.3 ± 11.4 (23 mkts) — but **~1.1 of 9 series would do that by chance**."

So **SOL has already been picked and already regressed**, and the branch carries an explicit
instruction against reopening on a mechanism argument. Both facts are recorded here so that a
positive result cannot later be read as stronger than it is, and a negative one cannot be read as
new information.

## Why this window is run anyway, and what makes it admissible

Two reasons, neither of them a speed or queue-model argument, and neither of them a P&L argument:

1. **The closure was BTC-only by construction.** `PREREG_btc_focus.md` picked BTC on flow (85.6% of
   contracts), depth (1,000–8,000 ct at the touch) and write budget (one series spends the whole
   300 tokens/s). Its verdict — −18.9 ± 8.6 c/market, 26 settled markets — is a statement about
   KXBTC15M and is scoped that way in its own decision rule. **No PREREG has ever been scored on
   KXETH15M or KXSOL15M.**
2. **Operator direction, recorded as such.** The operator named ETH and SOL on 2026-10-03
   ("Don test on 15min btc do eth instead for all this testing"; "Doing it on eth or sol might
   reopen some stuff"). The series choice is therefore **exogenous to this venue's P&L**, which is
   the same basis `PREREG_btc_focus.md` used when it wrote "NOT a reason: any per-series P&L".

**NOT a reason, and explicitly excluded from this decision:** SOL's R1 positive, NEAR's 3/3 record,
any per-series number in any shadow window to date, and any argument that our latency or the
cancel-credit fill model has improved.

## Frozen rule — identical to `PREREG_btc_focus.md`, series substituted

- 1 ct per side; `|position| <= 1`; mid band 15–85 c; no posts with < 120 s to close.
- Join the touch; pull a side if 1 s mid momentum >= 0.25 c runs into it, or it holds < 7.87% of
  touch size (`imb > 0.9213`). The closing side quotes like any side (v4 showed ungated pairing
  loses).
- Cancel credit: pro-rata by level shrink (v3 rule). A shadow that does not credit cancels ahead is
  scoring the wrong fill set — 41% of real queue clearance was cancels, and a trades-only model
  admits 5 of 26 real fills.
- Position cap checked as `pos ± clip`, never `pos` alone.
- Arms: `--only-base`. No arm selection after the fact.

## Series, and the multiplicity declared before the fact

| role | series | status entering this window |
| --- | --- | --- |
| **PRIMARY** | `KXETH15M` | never preregistered, never scored alone |
| secondary | `KXSOL15M` | never preregistered; picked positive in R1 and regressed in R3–R4 |
| secondary | `KXNEAR15M` | `PREREG_near.md` frozen 2026-09-24, got **ZERO** data, never scored |

**Three series means three tests.** The decision rule below is applied to **ETH alone** as the
primary. SOL and NEAR each carry their own pre-registered claim and their own bound, and a positive
on a secondary while ETH is flat is reported as **one of three**, with the chance rate stated. No
pooled "crypto gated maker works" claim may be made from a single series clearing.

## Window and metric

- **48 settled markets per series**, measured on a fresh window opened after this file is committed.
  At 4 markets/h per series that is ~12 h.
- Primary metric: **settlement cents per market**, SE clustered by market (a market is one unit of
  evidence; every fill in it shares one settlement).
- Pre-declared secondaries, diagnostic only, no decision weight: throttled count, mk60s,
  paired vs unpaired c/ct, naked residual c/ct, and the realised queue-clear split (trades vs
  cancels) so the fill model can be audited against this window rather than the 2026-09-23 one.

## Decision rule, per series, fixed now

- Lower 95% bound (`mean − 1.96·SE`) **> 0** → candidate for a capped live run, which needs separate
  operator approval and its own arming PREREG.
- Mean **> 0** but bound <= 0 → inconclusive; extend **once** by 48 markets on the same rule, then stop.
- Mean **<= 0** → that series is closed. Do not re-tune on this window.

If ETH closes, the operator-directed reopening is answered in the negative and the 15M crypto gated
maker should not be reopened again without Premier-tier access, exactly as the corpus already says.

## Sample precondition and the honest power statement

The BTC window scored 26 markets, not its specified 48, because Kalshi set `trading_active=false`
on all shards mid-window. At SD ~43 c/market (BTC window implied), 48 markets gives SE ~6.2 c, so
this window can only resolve an effect of roughly **>= 12 c/market**. An effect smaller than that
will land inconclusive by construction, and that is accepted in advance rather than discovered
afterwards.

## What is not being tested

Anything armed. This is `shadow`, read-only: the binary used must have no `live` path reachable
without `--armed`. Capacity is not tested either — NEAR's own prereg notes ~$10/day at 1-ct clips on
thin flow, and ETH/SOL are not materially deeper for a 1-ct seat.

## Stopping rule

One scored window per series per rule. A failed bound closes that series for this rule; a different
rule is a new PREREG, not a re-score of these markets.

## Amendment 1, 2026-10-03, written BEFORE the window opened: the series run SEQUENTIALLY, solo

`PREREG_btc_focus.md` ran **one series alone**, and gave as a reason that "at 9 series the Advanced
write budget throttled the gated quoter ~34k times in 3 h. One series spends the whole 300 tokens/s
on the deepest book." The shadow models that budget and reports a `throttled` count, so three series
sharing one window would hand the primary a different (worse) write budget than the BTC window it is
being compared against.

Therefore: **three sequential solo windows, `--series` with exactly one value, ETH first.**
`KXETH15M` is the primary and is scored first and alone. `KXSOL15M` and `KXNEAR15M` follow in their
own windows only if the operator still wants them after ETH is read out. This is a change to
scheduling, not to the rule, the metric or the decision bound, and it is recorded before any
ETH-only datum exists.
