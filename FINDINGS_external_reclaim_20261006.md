# External BTC reclaim bot audit

Date: Tuesday, October 6, 2026. No order was placed.

## Source

`J0shusmc/Kalshi-BTC` published a three-lane BTC 15-minute bot on September 17
from June 15–August 20 research:

- YES Reclaim-70;
- YES BTC-Fade-90;
- NO Reclaim-80.

The public source also contains a later file resembling authenticated Kalshi
fills and settlements. Public provenance is not independently verifiable, so
that file is classified as **external self-reported live evidence**, not local
REAL-LIVE evidence.

## Frozen post-publication replication

The exact September 17 thresholds were frozen before testing September
18–October 5 local data.

The source-like signal-close row failed:

- 1,166 eligible markets;
- 29 trades;
- 17 target touches and 12 full settlement losses;
- **-5.862c/contract** after entry/exit fees;
- UTC-day-clustered lower bound **-22.945c**;
- chronological halves **-2.286c / -9.200c**;
- extra-1c stress **-6.862c**;
- best-10%-day deletion **-18.762c**.

Lane breakdown:

| lane | trades | targets | mean |
|---|---:|---:|---:|
| Reclaim-70 | 1 | 1 | +31.000c |
| BTC-Fade-90 | 21 | 10 | **-11.333c** |
| NO-Reclaim-80 | 7 | 6 | +5.286c |

The principal BTC-Fade lane reversed sharply. This agrees with the source
repository's later decision to retire that lane, but the closure here comes
from the preregistered local holdout rather than that later edit.

A deliberately stricter next-minute-plus-1c row retained only five trades.
All five touched their target and averaged +32.000c, but this is not actionable:
it missed the 100-trade gate by 95 trades and one day supplied 25.6% of gross
positive P&L. It is preserved as insufficient rather than retuned.

## Public fill-file audit

The frozen public JSON contained 84 fill records and 31 settlement records from
August 27–September 16. Reconstructing every settlement from reported counts,
costs and fees produced:

- total cash P&L **+$78.134**;
- 24 positive and 7 negative settlements;
- equal-entry mean **+7.887c/contract**;
- day-clustered lower bound **+2.674c**;
- halves **+13.086c / +3.013c**;
- maximum closed-trade cash drawdown **-$52.157**;
- one UTC day supplied **44.6%** of gross positive P&L.

The September three-lane subset had only 13 settlements:

- self-reported cash P&L **+$79.781**;
- 12 positive and one negative;
- +9.871c/entry contract;
- clustered lower bound **-1.759c**;
- halves **+0.873c / +17.584c**.

The older Pullback-99 arm contributed 18 settlements and **-$1.647** cash P&L.
All 18 occurred on one UTC day, making any uncertainty estimate for that arm
non-identifying.

## Decision

**Closed.** The source-like historical rule was negative on the
post-publication local holdout. The public fill file is encouraging but tiny,
concentrated, self-reported and has a negative lower bound for the actual
three-lane subset. It does not alter the corpus hierarchy or justify live use.

Artifacts:

- `PREREG_external_reclaim_oos_20261006.md`
- `PREREG_external_reclaim_fill_audit_20261006.md`
- `idea_lab/analyze_external_reclaim.py`
- `idea_lab/external_reclaim_oos_report.json`
- `idea_lab/external_reclaim_fill_audit_report.json`
- `tests/test_external_reclaim.py`
