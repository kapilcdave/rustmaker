# Findings: 15M commodity two-sided maker shadow (PREREG_commodities.md), 2026-09-24

Run: Ohio box, 09:01-21:01Z, 12 h, no orders. 244 settled markets (49 per series; COPPER 48),
50,975 settled shadow fills, 428k prints on the probe tape. Clean run: 0 errors/reconnects/seq gaps,
0 tape rows dropped, handle_us p99 41-63 us (prereg contamination flag is > 1 ms).
Tapes: `data/box/` (local only). Scorer output: `data/box/shadow_pnl.txt`, `data/box/tox.txt`.

## Verdict: CLOSED (rule 5). No arm passes rule 1.

| arm | c/market | SE | lo95 | H1 | H2 | c/ct | mk5s c/ct |
|---|---:|---:|---:|---:|---:|---:|---:|
| nogate_pr | -59.44 | 6.46 | -72.09 | -68.99 | -49.40 | -0.60 | -0.54 |
| gate | -12.53 | 5.04 | -22.41 | -14.30 | -10.67 | -0.89 | -1.00 |
| gate_pr | -17.70 | 5.72 | -28.91 | -28.45 | -6.40 | -0.62 | -0.55 |
| gate_pr_in | -13.22 | 4.92 | -22.85 | -22.47 | -3.50 | -0.28 | -0.25 |
| gate_pr_front | -4.44 | 5.43 | -15.08 | -4.21 | -4.69 | -0.54 | -0.60 |

Total over 12 h: nogate -$145.02, gate_pr -$43.19, gate_pr_in -$32.25, gate_pr_front -$9.27.

- **Rule 2, gate value:** gate_pr beats nogate_pr by +41.7 c/market (t 7.7). The gate cuts the loss
  about 3.4x but does not make it pay. Same shape as crypto (6x).
- **Rule 3, queue diagnosis: NOT SUPPORTED.** Small-minus-deep queue at fill on gate_pr is
  +1.99 c/ct, SE 4.06, t 0.49 (91 markets). Queue position does not explain the loss.
- **gate_pr_front** is the least bad and beats gate_pr +13.9 c/market (t 2.42), but it is still
  negative in both halves and quotes on only 209 markets. No pass, and no re-tuning (rule 5).
- **Rule 4, per series (exploratory only):** no series is positive in both halves on gate_pr.
  NATGAS and COPPER are the worst on every arm. SILVER on `gate` is +7.9/+15.5, but it is one of
  25 series x arm cells. That is noise until a fresh window says otherwise.

## Same mechanism as crypto: fill selection flips the sign
The population of mid-band prints is breakeven for the maker (mk5s +0.045 c/ct, settle +0.32).
Our back-of-queue shadow fills mark out -0.25 to -1.00 c/ct at 5 s on every arm. We get filled
disproportionately by the part of the flow that moves the price.

## Toxicity features on the probe tape (rule 6: only sign-stable H1/H2 counts)
Replicate in both halves:
- Burst head +0.19/+0.37 mk5s. Deeper continuation -1.20/-1.62 mk5s, settle -2.0/-4.2.
- Time to close 1-3 min: mk5s -0.76/-0.78, settle -4.5/-2.1. 7-15 min: +0.32/+0.31, settle +1.6/+2.0.
Do NOT replicate (crypto's two gate features):
- 1 s momentum "with" the taker: mk5s +0.045/+0.052. Gone on commodities (crypto was -1.1/-2.9).
- Thin hit side (q5): mk5s +0.06/-0.91, a sign flip.
So the crypto gate constants were never the right gate here. Per rule 5 that is an input to any
future prereg, not a license to re-tune on this tape.

## Speed (probe + rtt on the box)
Feed age p50 5.39 ms, signed REST p50 5.2 ms, RSA-PSS sign 0.64 ms. Competitor add-after-print
p50 12.7 ms, join a new touch p50 6.1 ms.
