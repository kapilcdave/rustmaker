> ## ⛔ ERRATUM 2026-10-08 — every `c/market` LEVEL below is measured on a broken fill model
>
> Two bugs in `features.market_rows` were found and fixed after everything on this page was
> measured. Neither is a modelling choice; both changed **which fills exist**:
>
> 1. **The liveness rule was a collector-staleness proxy.** `live = (i == j)` on the book index
>    required *no book update at all* in the 11 ms window. That passes **70.30%** of prints on the
>    throttled `data/box/tape_*.csv.gz` (11.7 book updates/s) and **1.64%** on a full-fidelity
>    gate-probe tape (p90 100 updates/s) — a 43× swing in the fill population from feed density
>    alone. Fixed to touch-price equality.
> 2. **A sweep is one fill opportunity reported as many prints.** The venue stamps every level of a
>    sweep with one microsecond — measured, **332 prints at a single µs** walking 39c → 15c — and
>    the model counted each as a fill. That produced **50.6%** exact-duplicate rows in `mk5s-v1/v2`
>    data, concentrated in the most **adverse** events. Fixed by deduping to one opportunity per
>    `(vt, side)` (full prints are still used for VWAP/volume/flow).
>
> Together they move the ungated per-contract level from **−0.1321 → +0.1215 c/ct** against the
> 8.04M-real-print ledger's **+0.254** — from the wrong sign to the right sign, 2.1× low.
>
> **What this changes on this page.** `always` is **not** −33.4 c/market; on a corrected instrument
> the ungated improved-quote maker is **profitable**. `room4` is not +65.7 and does not carry a
> +91.7 c/market edge over `always` — its real marginal value is about **+2.6**, i.e. **2.0% of the
> oracle headroom**. So "this seat needs a gate", the premise of this whole page, is largely an
> artifact. The *paired* comparisons here survive (all arms shared the rows); the levels do not, and
> neither does the width gradient they rest on.
>
> Current numbers: `FINDINGS_rlmm_v3_20261008.md`. Rebuilt datasets: `ml/data/mk5s-v5`.
> The RL arm that supersedes the GLiNER work: `ml/rl/`.

# GLiNER gate for the 15M quoter — on GCP

A LoRA-fine-tuned GLiNER2.5 classifier that reads a serialized book snapshot and answers
`BUY_YES` / `BUY_NO` / `HOLD` / `TAKE_PROFIT` / `STOP_LOSS` with a confidence. The Rust engine keeps every hard limit:
the model proposes, `ml/serve.py` enforces size, price band, exposure caps, a time-to-close floor
and a kill switch. Nothing here touches `src/live.rs`, `src/main.rs` or `src/shadow.rs`.

## Verdict (2026-10-02): the adapter ties the logistic arm and so FAILS the bar

`FINDINGS_gliner_gate_20261002.md` has the full table. Short version, 140 held-out markets at
`mark = mk5s`: `gliner` +62.09 ± 12.97 (t = 4.79) against `logit` +69.41 ± 15.01 (t = 4.62), paired
difference **−7.33 ± 7.55 (t = −0.97)**. Both beat the ungated quoter by ~+100 c/market at t ≈ 8. The
adapter is the more accurate classifier (.601 vs .575) and the weaker policy, which is the whole
point of scoring cents instead of accuracy. The preregistered bar required beating `logit` at all, so
this serialization is closed; a new one is a new PREREG. One shape worth remembering: the adapter
takes 2.2x the entries at 0.41x the edge each, so it would be the better selector if capacity ever
bound instead of edge.

## Size and speed, measured on this Mac (2026-10-02)

GLiNER2.5 is small, not large: `-small-v1` is **74,545,431 parameters** (the trainer prints it) and
`-base-v1` is 194M — a DeBERTa-v3 encoder, three orders of magnitude under an LLM. Both run on CPU
here with no GPU and no quantization:

| checkpoint | load | p50 | p90 | LoRA adapter |
| --- | --- | --- | --- | --- |
| `gliner2.5-small-v1` | 4.3 s | **55.9 ms** | 90.0 ms | 2.6 MB |
| `gliner2.5-base-v1` | 17.9 s | **80.1 ms** | 103.3 ms | ~5 MB |

**That latency decides where the model is allowed to sit.** Our measured decision→book time is
11 ms, and the cancel path is where 15M crypto punishes delay. A 56–80 ms round trip must therefore
never be in the cancel path: the deterministic rule keeps pulling quotes, and the model is consulted
on decisions that tolerate tens of milliseconds — whether to post, and whether to flatten a lot on
the 5 s exit grid. A GPU is needed for training only, and only because it is 10× faster than this.

## One thing to know before spending credits

GLiNER2.5 is an encoder for text — NER, classification and schema extraction. Here it is being fed
a numeric snapshot rendered as a sentence, so none of its pretrained advantage (knowing words) is
doing work; it is a 194M-parameter tabular classifier with a tokenizer in front. `ml/policy_eval.py`
therefore always reports a logistic arm fit on the identical numeric rows, and the GLiNER arm has to
beat it on cents per market, not on accuracy. If it does not, the serialization or the label set is
the thing to change, not the GPU.

## Files

| file | what it does |
| --- | --- |
| `features.py` | decision rows from a probe tape: entry rows (penny.py's improved-quote fill model, LAG 11 ms, position cap 10 ct) and exit rows (1 s grid while inventory is open). Labels come from net P&L: settlement for entries, crossing-out-vs-carrying for exits, per contract. |
| `build_dataset.py` | tapes → `train/val/test.jsonl` in GLiNER2 classification format, `rows.parquet`, `manifest.json`. Split is **chronological at a market boundary**. |
| `baseline.py` | the control arms, no GPU. Run this first. |
| `policy_eval.py` | cents per **market** for a policy, lot-wise, with per-market SE; arms `always`, `detgate` (penny.py's toxicity rule), `logit`. |
| `train_gliner.py` | LoRA fine-tune + OOS arm table. Writes `report.txt`. |
| `serve.py` | CPU sidecar on loopback. Holds no venue credential; `GATE_SHADOW_ONLY=1` by default. |
| `gcp.sh` | bucket, Spot GPU trainer that **deletes itself**, CPU serving VM, teardown. |

## Run order

```bash
# 1. dataset from local tapes (CPU, minutes). --mark picks the P&L the labels and the arm table
#    BOTH read; mk5s is powered on the tape in hand, settle needs ~550 h (see below).
.venv/bin/python ml/build_dataset.py data/box/tape_*.csv.gz data/local/tape_*.csv.gz \
  --out ml/data/v1 --mark mk5s

# 2. controls — if these do not separate, stop here
.venv/bin/python ml/baseline.py ml/data/v1

# 3. GCP: upload, train on a self-deleting Spot L4, pull the report
./ml/gcp.sh bucket
./ml/gcp.sh push ml/data/v1
./ml/gcp.sh train v1
./ml/gcp.sh logs
./ml/gcp.sh pull v1

# 4. shadow the sidecar locally before any VM
GATE_ADAPTER=ml/runs/v1/adapter/final GATE_BASE=fastino/gliner2.5-small-v1 \
  .venv/bin/uvicorn ml.serve:app --host 127.0.0.1 --port 8008
```

## Where it stands (measured 2026-10-02)

Built from the one 3-hour crypto probe tape in this tree
(`data/box/tape_1790136632445.csv.gz`): **1.39 M rows over 117 markets** in the parquet, 70 train /
17 val / 30 test. The JSONL keeps every entry row and 4 exit rows per lot (122 k train lines),
because at the full grid exit rows outnumber entries 37:1 and the model becomes an exit model that
never enters. The parquet keeps the full grid — the policy evaluation needs every row to find the
first exit a policy would call.

Arms on the 30 test markets, cents per market:

| arm | c/market | SE | t | entries | TP | SL |
| --- | --- | --- | --- | --- | --- | --- |
| always | +30.0 | 52.3 | 0.6 | 6,386 | 0 | 0 |
| detgate | −2.9 | 102.8 | −0.0 | 4,376 | 0 | 0 |
| **bracket_2_3** | **−399.7** | 61.1 | **−6.5** | 6,386 | 91,705 | 96,011 |
| logit | +51.1 | 123.3 | 0.4 | 4,331 | 3,844 | 56,626 |
| oracle_entry | +2,259.8 | 465.9 | 4.9 | 3,196 | 0 | 0 |
| oracle_exit | +2,017.9 | 447.7 | 4.5 | 6,386 | 29,494 | 84,500 |
| oracle_both | +2,259.8 | 465.9 | 4.9 | 3,196 | 29,494 | 84,500 |

Three things to read off this:

1. **A hand-written take-profit / stop-loss bracket destroys the edge**: −400 c/market at t = −6.5,
   the only well-powered number on the table. Crossing out pays the full touch plus the taker fee
   every time, and on a 15-minute binary that is dearer than carrying.
2. **Perfect exits add exactly nothing on top of perfect entries** — `oracle_both` equals
   `oracle_entry` to the cent. Checked directly: of the 3,196 lots a perfect entry gate accepts,
   **0** ever have a row where crossing out beats carrying. This is near-identity, not luck: a lot
   that settles at 100 collects 100 − P, and any intermediate exit collects at most 99 − P − fee. So
   `TAKE_PROFIT` as labelled can only ever fire on a lot that was going to lose — it is "leave
   before it reverts", not "bank a winner". **Exit timing is a loser-rescue, and entry selection
   already excludes losers.** If the goal is lower variance rather than higher mean, that is a
   different objective and needs a different label.
3. **Entry selection is where the headroom is**, and nothing causal has touched it: `always`,
   `detgate` and `logit` are all inside one SE of zero at 30 markets, where per-market settlement
   variance is ±50 c. The binding constraint is tape, not model capacity — 3 hours of 15M crypto is
   117 markets.

## The mark is the decision that was blocking everything (2026-10-02)

Required tape is set by the estimator, not the model. Paired gate-minus-always SD per market, over
all 117 markets in this tree, and the markets needed for a t = 2 test of a 10 c/market effect:

| mark | paired diff | SD/market | markets for t=2 at 10 c | hours of 15M crypto |
| --- | --- | --- | --- | --- |
| settlement | +103.8 ± 67.7 | 732 | 21,425 | **549 h** |
| 60 s markout | +31.9 ± 21.2 | 229 | 2,104 | 54 h |
| **5 s markout** | **+29.5 ± 5.7** | **61** | 151 | **3.9 h** |

Carrying lots to settlement adds directional variance the quoting decision does not control, and it
costs a factor of 140 in tape. `--mark` therefore picks the P&L **once**, the entry label is read off
it, `manifest.json` records it and the scorer prints it, so training and scoring cannot disagree.
Exit rows are dropped under a markout mark, because cross-out-vs-carry is inherently a settlement
statement.

With `--mark mk5s` the same 117 markets give a powered table (one 3-hour tape):

| arm | c/market | SE | t | entries taken |
| --- | --- | --- | --- | --- |
| always | **−55.8** | 18.8 | **−3.0** | 6,386 |
| detgate | −29.1 | 16.9 | −1.7 | 4,376 |
| logit | +3.4 | 4.3 | 0.8 | 204 (3%) |
| oracle_entry | +152.8 | 23.4 | 6.5 | 2,834 |

Read: at 5 s the ungated quoter is **losing**, significantly; `penny.py`'s gate removes about half of
it; the logistic arm only breaks even by declining 97% of the fills; and the oracle says +153 is
there for a selector that works. This is the adverse-selection half of the problem, which is exactly
what a gate is for — but note it is **not** the estimand of the live penny-jump result
(+5.9 c/market, settlement, room ≥ 5). Two different questions:

- `--mark mk5s` — powered today, 117 markets is enough, and the gate's job *is* adverse selection.
- `--mark settle` — what actually pays, and needs ~550 h of tape on the box before it can be scored.

The sequence those imply: select on `mk5s` now, then confirm the survivor at `settle` once the tape
exists. Do not read an `mk5s` win as money.

### 558 markets, `mark = mk5s` (the scored dataset, `ml/data/mk5s-v1`)

Adding `data/box/shadow_spot/tape.csv.gz` (441 markets, 0 overlap with the box tape) gives 558
markets — 334 train / 84 val / 140 test, 247,575 entry rows:

| arm | c/market | SE | t | entries taken |
| --- | --- | --- | --- | --- |
| always | −33.4 | 18.3 | −1.8 | 83,191 |
| detgate | −11.7 | 10.6 | −1.1 | 54,873 |
| **logit** | **+69.4** | 15.0 | **+4.6** | 9,777 (12%) |
| oracle_entry | +505.8 | 46.0 | 11.0 | 38,485 |

The reproduction arm now clears the bar: a multinomial logistic on 14 numeric features, fit on the
334 earlier markets, makes **+69.4 ± 15.0 c/market on the 140 later ones** by declining 88% of the
fills. That is the number GLiNER has to beat, and it is a much harder benchmark than the −33 the
ungated quoter posts.

**The jump from the 117-market table was the thing to distrust, and it survived every cheap check**
(`ml/stability.py`, run 2026-10-02 — all on the built parquet, no GPU). An edge that grows with
sample size is the classic noise signature, so the level was tested directly:

| check | result | reads |
| --- | --- | --- |
| PREREG sign split, test fold by close | early +72.3 ± 14.6 (t 4.9), late +66.5 ± 26.3 (t 2.5) | same sign, both halves powered |
| per series | positive in **9 of 9**, t > 2 in 6 | not a pooled statistic moving against its parts |
| cross tape — fit on the earlier 117-market tape ALONE | +53.4 ± 13.9 (t 3.8) on the same 140 markets | the fit transfers across a different tape three days earlier |
| edge vs n (10 → 140 markets, close order) | level flat +62 to +83, t rises 2.1 → 4.6 | SE shrinking, not the mean growing — the opposite of the noise signature |
| val fold, 84 markets, used for nothing until now | +48.2 ± 11.4 (t 4.2) | holds on a second untouched block of time |
| breadth | 109 of 140 markets positive, median +16.2, best-5 removed still +44.8 | not five markets |

So **+69.4 ± 15.0 is a real bar**, not an artifact, and it is the number GLiNER must beat. Two
things it is still not: it is an `mk5s` number, so it is a claim about adverse selection and not
about money (the `settle` arm needs the tape now being collected), and the arm pays by *declining
88% of the fills* — the selector's value is in what it refuses, which is what a gate is.

### The benchmark collapses to one line — and that line contradicts 8 M real prints

Stable is not the same as valid, and the next question was *what the logistic arm is actually
selecting on*. Its standardized coefficients are dominated by four collinear price columns
(`px_c` +45 / −45 against `mid_c`, `bid_c`, `ask_c` at −15), which is the model reconstructing one
difference: `s·(px_c − mid_c)` — **our signed half-spread**. The accepted rows confirm it:
mean `spread_ticks` **10.95 vs 4.03** over all rows, median **7 vs 2**.

So the arm was re-scored against a one-line gate, threshold fixed on train (optimum k=4) and val
(optimum k=4), then scored **once** on test:

| arm | c/market | SE | t | entries taken |
| --- | --- | --- | --- | --- |
| always | −33.4 | 18.3 | −1.8 | 83,191 |
| detgate | −11.7 | 10.6 | −1.1 | 54,873 |
| **logit (14 features)** | **+69.4** | 15.0 | +4.6 | 9,777 |
| **`room4` = `spread_ticks >= 4`, nothing else** | **+65.7** | 15.3 | +4.3 | 25,125 |
| `room4 & adverse mom1s <= 0` | +45.4 | 9.7 | +4.7 | 16,541 |

**Paired, `logit − room4` = +3.7 ± 4.1 c/market, t = 0.91.** The 14-feature model is a width gate
with noise on top. That matters twice: a width gate is a *deterministic* rule, so it belongs in
`penny.py` inside the 11 ms path, not in a 56–80 ms sidecar; and GLiNER's real bar was never the
logistic arm, it is a single comparison.

**And the width gradient is the one thing this venue has already refuted on real fills.** Per
contract in this dataset, gross rises monotonically with quoted spread:

| spread (ticks) | 2 | 3 | 4 | 5 | 6–7 | 8–10 | 11–20 | 21+ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| mk5s c/ct | −0.39 | −0.17 | +0.15 | +0.21 | +0.28 | +0.22 | +0.37 | **+1.03** |
| win rate | 0.39 | 0.46 | 0.52 | 0.52 | 0.56 | 0.59 | 0.64 | 0.73 |

On **8,038,551 real Kalshi prints** the same quantity held to settlement is **flat** — +0.249 /
+0.264 / +0.252 / +0.240 / +0.244 c/ct at 0-1, 1-2, 2-3, 3-5, 5-10 c — because capture and adverse
selection rise together, and the simulated fill model overstates adverse selection **5.17×**. A
width gradient is exactly the artifact that measurement predicts. So the honest reading of
`+65.7 c/market` is **a property of the improved-quote fill model, not of the venue**, and it is
load-bearing for the whole `mk5s` table: richer features fit the artifact harder, they do not test
it. The fill model, not the model class, is now the branch's binding constraint — and the
`settle`-mark tape being collected is on the critical path for a different reason than power.

### Two candidate explanations for the width gradient, both closed by measurement

**Not a price-reach failure in the fill model.** `market_rows` accepts a fill on `live & room` and
never compares the print price to the quote it assumes we posted. On this venue a yes-print *is* the
ask, so a print worse than our quote would mean our order was behind the market. `ml/fill_audit.py`
checks all 341,261 assumed fills: **99.42% reach the quote** (99.41% no-side, 99.42% yes-side), with
no gradient in the pass rate across spread buckets (0.989–0.997). The fill model is sound on price.

**Not a price-tail selector.** `spread_ticks` conflates two lattices — the tick is 0.1 c below 10 c
and above 90 c — and 72.5% of `room4`'s accepted rows are in that deci regime against a 43.9% base
rate, so `room4` looked like it might be "quote the tails" in disguise. Scored on the test fold, the
decomposition is disjoint and additive, and it says the opposite:

| arm | c/market | t | entries |
| --- | --- | --- | --- |
| `room4`, all | +65.7 ± 15.3 | 4.3 | 25,125 |
| `room4` ∩ **cent band** (mid 10–90 c) | **+53.5 ± 14.6** | 3.7 | 7,382 |
| `room4` ∩ deci tails | +12.2 ± 5.4 | 2.3 | 17,743 |
| deci tails, **no** width condition | −3.5 ± 5.6 | −0.6 | 35,256 |

82% of the edge is mid-band books with a genuine ≥4 c spread, on 29% of the accepted rows; the tails
pay nothing without the width condition. So it is a real width gate on a real cell.

**Which sharpens the conflict rather than resolving it.** In the cent band alone the per-contract
gradient is steep — −0.48 c/ct at 2 c, +0.38 at 4 c, +0.74 at 5 c, +1.53 at 6–7 c, +2.58 at 8–10 c —
against the real-print ledger's flat +0.24 to +0.26 over that same range. One difference is real and
matters: the ledger measures a maker resting **at the touch**, while this instrument is a quote one
tick **inside** it, and the inside quote has a live result of its own — the penny-jump run at
room ≥ 5 pays **+5.9 ± 2.6 c/market at settlement on real fills**, with +9.0 ± 3.2 offline. Against
that, `room4`'s +53.5 at `mk5s` is roughly 6–9× too large, which is the order of the fill model's
known 5.17× adverse-selection overstatement.

**Reading:** the SHAPE is right and already earns money live; the LEVEL on this dataset is inflated
by the fill model. That makes `room4` the benchmark — not the logistic arm, and not `always` — and
it means a model earns its place here only by beating a one-line rule that is already deployed.

### v2: VWAP, moving averages, realized vol, taker flow and Avellaneda-Stoikov add nothing

Operator-proposed (2026-10-02): enrich the serialized snapshot — VWAP for the contract, moving
averages, and A-S pricing — and let the model output buy/sell confidence. Built as
`ml/data/mk5s-v2`, 14 -> **30 features**, identical 558 markets and 247,575 rows, so the comparison
is on the same rows. Everything is computed strictly before `t − LAG_US`; `ml/test_serialize.py`
passes, so the sidecar speaks the new language too. Scored by `ml/feature_lift.py`:

| arm | TEST c/market | SE | t | entries |
| --- | --- | --- | --- | --- |
| always | −33.4 | 18.3 | −1.8 | 83,191 |
| detgate | −11.7 | 10.6 | −1.1 | 54,873 |
| **room4** (one line) | **+65.7** | 15.3 | +4.3 | 25,125 |
| logit_base14 | +69.4 | 15.0 | +4.6 | 9,777 |
| logit_ctx16 (new features only) | +38.1 | 8.9 | +4.3 | 5,609 |
| logit_all30 | +69.3 | 14.8 | +4.7 | 10,608 |

Paired per market, which is the only fair comparison:

| comparison | diff | t | reads |
| --- | --- | --- | --- |
| `logit_all30 − logit_base14` | **−0.14 ± 3.33** | −0.04 | the 16 new features add **exactly nothing** |
| `logit_all30 − room4` | +3.57 ± 5.98 | +0.60 | still cannot beat one line |
| `logit_ctx16 − room4` | **−27.62 ± 10.04** | −2.75 | the new features ALONE are significantly worse |

**The A-S terms are the most informative single features on the table, and that is the finding.**
For "which side of a big move are we on", `as_room_c` — our half spread in CENTS minus the A-S
optimum — scores **AUC 0.675**, beating `spread_ticks` (0.643) because it does not conflate the 1 c
and 0.1 c lattices. It is a better *coordinate for the width gate*, not new information: a gate on
it, tuned on train and val, pays +23.4 (train) / +41.6 (val) against `room4`'s +25.7 / +44.0, so the
better AUC does not convert — `spread_ticks` also collects the deci cell worth +12.2.

Everything genuinely directional is at chance on the same rows: `flow_adv60` 0.500, `flow60` 0.507,
`px_vwap_c` 0.511, `mid_ma10_c` 0.516, `vwap_dev_c` 0.524. What does predict is magnitude —
`sigma_c` 0.631, `tvol60` 0.610 — and adding features makes the side classifier WORSE
(all30 0.576 < base14 0.638 < `spread_ticks` alone 0.643) on 2,364 training rows.

**So the feature space is exhausted for this label on this instrument**, and the three operator
proposals resolve the same way: swing trading and reversal prediction die on the fee
(continuation is +0.15 c/5 s and +2.44 c/60 s at its strongest against a 4 c round trip), and
sweep avoidance dies on the symmetric tail (dropping the worst 1% of fills gains +26 c/market,
dropping the best 1% loses −30 c). Magnitude is easy here; sign is not.

### Collection for the settlement arm (started 2026-10-02 20:10Z)

`runners/gate_probe.sh` on the Ohio box runs the read-only probe in hourly files with backoff and a
disk guard (the binary in `kalshi-mm15-penny4/` has only `probe` and `rtt` subcommands, so it cannot
place an order). It covers all 9 crypto 15M series at ~23 MB/h with feed age 5.7 ms p50.
`runners/com.kapil.mm15gatearchive.plist` (loaded) drains finished tapes to the Mac, into
`data/box_archive/<box path>` — NOT `data/probe_gate/`, which only exists on the box. `gate_probe.sh`
also lives only on the box (`~/trading/kalshi-mm15/runners/`), not in this tree.
Stop collection with `touch ~/trading/kalshi-mm15/data/probe_gate/STOP` on the box.

**The drain was silently dead for its first 24 h (found and fixed 2026-10-03).** Every run reported
`archived 0 files, 0 MB` while stderr said `sh: aws: command not found`: launchd starts a job with
`/usr/bin:/bin:/usr/sbin:/sbin`, the AWS CLI is in `~/.local/bin`, and `~/.ssh/config` reaches the
box through a `ProxyCommand` that runs `aws ssm start-session`. The plist now sets an explicit
`PATH`. A manual run then archived 8 files / 131 MB.

**Measured budget, so the next person does not have to re-derive it.** The supervisor's guard is
`MIN_FREE_MB=400` and it has never tripped (0 `disk low` events since 2026-10-02T20:10:33Z). The
probe writes ~20 MB/h (tape + stats), not the 23 MB/h first estimated, so a 6-hourly drain removed
~120 MB per cycle against ~120 MB written — **exactly break-even**, which left the entire 219 MB
buffer over the guard as the only margin. The cadence is now **2 h** (`StartInterval`), holding the
on-box high-water mark near 40 MB.

⚠ The free-space series in `supervisor.log` (938 → 819 → 619 → 544 MB over 01:10–04:10 on 10-03)
reads as a 131 MB/h bleed and **is not one**: it was a single episodic ~300 MB writer. Sampled
directly afterwards, `~/.hermes/state.db` grew 0 KB in 60 s and free space moved 2 MB in 25 min.
`~/.hermes` is 3.7 GB and is the obvious reclaim, but it belongs to an unrelated live service on
that box — check what is writing it before deleting anything.

## The pilot adapter, 2026-10-03 (PILOT, not this PREREG's scored run)

`gliner2.5-small-v1` (74 M), LoRA r=8 (663,552 trainable, 0.89%), 2 epochs on a 20,000-line
subsample of `mk5s-v2`, MPS, ~1h50m. Declared a pilot in `PREREG_gliner_gate.md` amendment 3
BEFORE the number existed, so it can fail informatively but cannot pass. Scored on the full
140-market test fold (`ml/runs/pilot-v2/report.txt`):

| arm | c/market | SE | t | entries |
| --- | --- | --- | --- | --- |
| always | −33.4 | 18.3 | −1.8 | 83,191 |
| detgate | −11.7 | 10.6 | −1.1 | 54,873 |
| **room4** | **+65.7** | 15.3 | +4.3 | 25,125 |
| logit | +69.3 | 14.8 | +4.7 | 10,608 |
| **gliner** | **+11.4** | 7.5 | +1.5 | 18,038 |

Paired: `gliner − room4` **−54.27 ± 15.33 (t −3.54)**, `gliner − logit` −57.84 ± 15.08 (t −3.84),
`gliner − detgate` **+23.13 ± 11.08 (t +2.09)**. It beats `penny.py`'s toxicity rule and loses badly
to one line of code.

**Two separable failures, and the diagnostics say which is which.**

1. **The head is degenerate**: it never emits `BUY_NO` once — all 19,395 no-side test rows go to
   HOLD, and its only output classes are `BUY_YES` and `HOLD`. This is NOT label imbalance (the
   pilot's train labels are 4,542 / 4,447 / 11,011) and NOT a missing input (the side is explicit in
   every sentence as `candidate improved-bid` vs `improved-ask`). Mean confidence on the class it
   does emit is **0.517**, i.e. chance. A 74 M encoder with 0.89% of its parameters unfrozen, two
   epochs, 20 k rows, did not learn a three-way label it is handed verbatim.
2. **It loses on its own side anyway.** Restricting BOTH arms to yes-side rows removes the
   degenerate-head penalty: `room4` +37.90 ± 10.08 (t 3.76) vs `gliner` +11.44 ± 7.49 (t 1.53),
   **paired −26.46 ± 9.80, t = −2.70**.

(2) is the informative part, because (1) alone would have excused the result. A full-scale GPU run
(116 k rows, 6 epochs, 194 M base) would plausibly fix the collapse; it has to close a −26 c/market
gap on the half of the book the model was willing to trade, against a benchmark that is one
comparison in `penny.py`'s 11 ms path. Under the stopping rule the pilot does not close the
serialization — only the scored run can — but it is evidence about the expected value of buying one.

## GLiNER for DIRECTION, not for the gate — closed on the target, 2026-10-03

A fair question, because the gate's label ("was this fill profitable") is not the direction of the
mid. Tested model-agnostically first: is direction in the inputs at all? CIs are market-clustered —
every row in a market shares one price path, so a row-level CI is a fiction.

| target | logit base14 | logit ctx16 | logit all30 | GBM all30 | 95% CI (clustered) |
| --- | --- | --- | --- | --- | --- |
| sign(next 5 s mid move) | 0.6276 | 0.6118 | 0.6262 | 0.6344 | [0.609, 0.650] |
| sign(next 60 s mid move) | 0.7424 | 0.7446 | 0.7375 | 0.7053 | [0.692, 0.783] |

**It is predictable, and it is still worthless, because the signal is the price level.** `mid_c`
alone scores **0.7583** on the 60 s target — higher than all 30 features — and `sign(mid − 50)`
alone scores 0.695. The mid drifts toward its own settlement. Genuine flow terms are weak:
`mom5s_c` 0.571, `flow60` 0.539, `mom1s_c` 0.533, `imb` 0.515.

Converted to cents as a taker (crossing in and out pays the full spread plus two fees, **4.790 c**
mean round trip):

| arm | per contract | hit rate | per market (140) |
| --- | --- | --- | --- |
| logit all30, every row | **−4.095 c** | 0.687 | −2,433.6 ± 304.6 (t −7.99) |
| logit all30, \|p−0.5\| ≥ 0.15 | −3.230 c | 0.744 | −1,172.6 ± 192.5 (t −6.09) |
| logit all30, \|p−0.5\| ≥ 0.30 | **−1.876 c** | **0.800** | −56.4 ± 21.4 (t −2.64) |
| GBM all30, every row | −4.502 c | 0.648 | −2,674.9 ± 316.7 (t −8.45) |
| `mid_c` alone | −4.073 c | 0.690 | −2,420.3 ± 343.0 (t −7.06) |
| **ORACLE, perfect direction** | **+2.735 c** | 1.000 | pays on only **50.1%** of rows |

**An 80%-accurate directional model loses 1.88 c per contract.** Accuracy and P&L point in opposite
directions on a converging binary: you are right because the price drifts to settlement, so a win
collects a few convergence ticks while a loss pays the full adverse move. Even perfect foresight
nets +2.7 c against a 4.8 c round trip, on half the rows.

**So this closes on the TARGET, not the model class.** No encoder reading these inputs can beat an
80%-accurate rule that loses money, and the 15M crypto maker fee is 0 while the taker fee is ~2 c a
leg — which is the whole reason every surviving edge on this venue is maker-side.

## Cost shape on GCP

**A free-trial billing account cannot attach any non-TPU accelerator**, whatever the GPU quota says:
`instances create` is refused with "your billing account is currently in the free tier where non-TPU
accelerators are not available". Per-region quota in this project does show 1 each of L4/T4/V100/
P100/P4 and 0 A100, so the quota was never the blocker. Upgrading the billing account keeps the
remaining trial credit (it expires 91 days from trial signup either way) and unlocks the GPU; until
then `./ml/gcp.sh train-cpu` runs the same job on `e2-standard-16`, ~10x slower and ~$0.55/h.
After the upgrade, Spot `g2-standard-4` + 1×L4 is roughly $0.20–0.25/hr and the trainer deletes
itself when the run ends, so an hour or two per experiment.

The image family also matters: `pytorch-latest-gpu` no longer exists. The current ones are
`pytorch-2-9-cu129-ubuntu-2204-nvidia-580` (GPU) and `common-cu129-ubuntu-2204-nvidia-580` (used for
the CPU path — a CUDA torch build runs fine with no GPU present). The CPU serving VM
(`e2-standard-4`, no external IP, reached over IAP) is the item that accrues while idle — run it
locally until the arm table justifies it.

## Security notes

- The sidecar binds `127.0.0.1`, never calls Kalshi, and holds no key: a bug in the model path
  cannot place an order. Set `GATE_TOKEN` so only the engine can reach `/decide`.
- The serving VM is created with `--no-address`; reach it with
  `gcloud compute ssh --tunnel-through-iap`, not a public IP and an open firewall rule.
- `GATE_KILL_FILE` (default `/tmp/gate.kill`) forces HOLD on every request. Touch it to stop the
  model proposing without stopping the engine.
