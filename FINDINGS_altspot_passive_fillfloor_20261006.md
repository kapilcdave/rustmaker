# Altspot passive strict-through fill floor — BNB passes, primary basket fails

Date: 2026-10-06. Historical public trade tapes only; no orders were placed.

## Frozen primary execution audit

`PREREG_altspot_passive_fillfloor_20261006.md` converted the already frozen
NEAR/ZEC/HYPE spot-gap signal into a passive order:

- join the signaled side's candle-close bid two seconds after the decision;
- leave one contract until close;
- credit only a later aggressor print strictly through the limit;
- never credit at-price prints because queue ahead is unknown.

Of 1,280 submitted signal orders, 1,277 had uncapped histories and 1,153
received a strict-through fill. The result was **-0.606c per submitted order**
with a UTC-day-clustered 95% interval **[-2.249c, +1.038c]**. Both halves were
not positive: the chronological split was **-2.233c / +1.019c**. NEAR and
HYPE were negative, while ZEC was +0.475c/submission.

At a five-second buffer the result was -0.871c/submission; at ten seconds it
was -1.307c. An additional one cent per credited fill made the governing
two-second result -1.509c/submission.

**Verdict: CLOSED as a passive implementation.** The historical taker signal
passing does not imply that a stale bid left resting until close is profitable.
Strict-through fills select reversals against the signaled side.

## Frozen BNB secondary audit

BNB was preregistered separately in
`PREREG_bnb_passive_fillfloor_20261006.md` before its selected signal-market
tapes were fetched.

Governing two-second result:

- 980 usable submitted orders; one capped history excluded;
- 874 strict-through fills, an 89.2% fill rate;
- **+3.226c per submitted order**;
- day-clustered 95% interval **[+1.029c, +5.424c]**;
- chronological halves **+2.565c / +3.888c**;
- +3.618c per credited fill.

With an extra one cent charged to every credited fill:

- **+2.334c per submitted order**;
- clustered interval **[+0.138c, +4.531c]**;
- halves **+1.698c / +2.971c**.
- deleting the best 10% of UTC days leaves **+1.098c/submission**;
- the largest positive day is 15.4% of gross positive day P&L;
- 265 credited fills won and 609 lost, so the edge is cheap-entry payoff
  asymmetry rather than a high hit rate.

The five- and ten-second raw variants remained positive, but their one-cent
stressed lower bounds crossed zero. The apparent edge is therefore timing
sensitive around the frozen two-second buffer.

Post-hoc mechanism diagnostics, with no rule-selection authority, found:

- all four frozen decision minutes positive after one-cent stress:
  +3.40c, +2.01c, +1.46c and +2.48c per submission for k=2/3/5/8;
- both YES and NO signals positive after stress;
- median strict-through time was 19.1 seconds, with p90 240 seconds;
- the 0.75-0.90 limit-price bin lost money, but it contained only ten
  submissions and cannot be retrospectively removed.

## Post-selection pre-period replication

The later-window pass did **not** replicate over the preregistered July 9
through September 13 sample:

- 807 seeded, day-stratified submitted signals over 68 UTC days;
- 483 strict-through fills, 59.9% fill rate;
- raw +0.350c/submission, clustered interval **[-1.756c, +2.455c]**;
- one-cent stress **-0.249c/submission**, interval
  **[-2.354c, +1.856c]**;
- stressed chronological halves **-1.365c / +0.864c**;
- deleting the best 10% of days leaves **-2.407c/submission**.

This is not an independent holdout because BNB was already selected, but the
failure is still material: the later September-October edge is not a stable
feature of the longer prior regime. The fill-rate shift from 59.9% to 89.2%
also shows that execution state changed substantially.

## Interpretation

BNB passes the later historical strict-through maker screen. It is stronger
execution evidence than a candle-only quote because every credited fill has a
later public aggressor print beyond the posted limit, and at-price queue credit
is forbidden.

It is still **not independently validated profitability**:

1. BNB was a secondary discovered after its historical return was observed.
2. The trade-tape audit uses the same outcome period, so it tests execution
   mechanics rather than a fresh return population.
3. A candle close is not an atomic direct orderbook snapshot.
4. The two-second timing sensitivity requires prospective receipt-clock and
   queue validation.
5. It remains one asset and one historical regime.
6. The frozen pre-period robustness panel failed.

The admissible next step is the already-running prospective BNB direct-book
journal followed by a fresh future strict-through queue shadow. Nothing here
licenses a live order.

Canonical artifacts:

- `idea_lab/audit_altspot_passive_fillfloor.py`
- `idea_lab/altspot_passive_fillfloor_report.json`
- `idea_lab/bnb_passive_fillfloor_report.json`
- `idea_lab/bnb_passive_preperiod_report.json`
