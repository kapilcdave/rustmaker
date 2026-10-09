#!/usr/bin/env python3
"""Does the 15M sim instrument understate the maker, or is the real-print ledger a
different population?

Scores PREREG_instrument_residual_20261009.md. One pass, all tables, no interactive
exploration. See the prereg for the frozen universe, estimator and decision rules.

The whole study rests on one fact: ml/features.py:285 computes
    settle_c = s * (px_c - 100 * result_yes) - MAKER_FEE_C,  MAKER_FEE_C = 0.0
which is the real-print ledger's own gross estimator. C2 verifies that rather than
trusting a reading of the source.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "ml" / "data" / "mk5s-v5" / "rows.parquet"
OUT = ROOT / "reports" / "instrument_residual.json"

# --- frozen by prereg §3/§4 ---------------------------------------------------------
SERIES_8 = [  # the real-print ledger's universe: 8 non-BTC 15M crypto series
    "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M",
    "KXHYPE15M", "KXZEC15M", "KXNEAR15M", "KXBNB15M",
]
MID_BAND = (15.0, 85.0)
N_BOOT = 10_000
SEED = 20261009
SPREAD_BUCKETS = [  # (label, lo, hi_inclusive, midpoint for the slope)
    ("2", 2, 2, 2.0), ("3", 3, 3, 3.0), ("4", 4, 4, 4.0),
    ("5-6", 5, 6, 5.5), ("7-9", 7, 9, 8.0), ("10+", 10, 10**9, 12.0),
]

# published real-print bands we are testing against (prereg §5)
POP_BAND = (0.118, 0.386)      # population resting side, +0.254
TOUCH_BAND = (0.0415, 0.6501)  # touch resting maker, +0.3473
LEDGER_SPREAD_PROFILE = {      # flat-in-spread on real prints, prereg §5 P2
    "0-1c": 0.249, "1-2c": 0.264, "2-3c": 0.252, "3-5c": 0.240, "5-10c": 0.244,
}


def cluster_boot(df: pd.DataFrame, col: str, *, n_boot: int = N_BOOT, seed: int = SEED):
    """Mean of per-market means, with a market-clustered bootstrap CI.

    THE reducer for every cell in this study (prereg §4). Resamples markets with
    replacement, never rows: every row in a 15-minute market shares one price path, so a
    row-level CI is a fiction.
    """
    g = df.groupby("ticker", observed=True)[col].mean().to_numpy()
    n = len(g)
    if n == 0:
        return dict(n_markets=0, n_rows=0, mean=None, lo=None, hi=None, se=None)
    rs = np.random.default_rng(seed)
    idx = rs.integers(0, n, size=(n_boot, n))
    draws = g[idx].mean(axis=1)
    return dict(
        n_markets=int(n), n_rows=int(len(df)), mean=float(g.mean()),
        lo=float(np.percentile(draws, 2.5)), hi=float(np.percentile(draws, 97.5)),
        se=float(draws.std(ddof=1)),
    )


def boot_slope(df: pd.DataFrame, col: str, *, n_boot: int = N_BOOT, seed: int = SEED):
    """OLS slope of per-bucket per-market mean on bucket midpoint, recomputed inside each
    market-clustered resample (prereg §5 P2). Positive slope = the sim pays more in wider
    books, which is what every width gate in this corpus assumes and what the real print
    tape does NOT show."""
    df = df.copy()
    lab = pd.Series(index=df.index, dtype=object)
    mids = {}
    for name, lo, hi, mid in SPREAD_BUCKETS:
        m = df.spread_ticks.between(lo, hi)
        lab[m] = name
        mids[name] = mid
    df["bucket"] = lab
    df = df[df.bucket.notna()]

    # per (market, bucket) cell mean, then the slope over buckets; a market contributes to
    # whichever buckets it visited
    cell = df.groupby(["ticker", "bucket"], observed=True)[col].mean().unstack("bucket")
    cell = cell.reindex(columns=[b[0] for b in SPREAD_BUCKETS])
    x = np.array([mids[c] for c in cell.columns], dtype=float)

    def _slope(mat: np.ndarray) -> float:
        y = np.nanmean(mat, axis=0)
        ok = ~np.isnan(y)
        if ok.sum() < 2:
            return np.nan
        xc, yc = x[ok], y[ok]
        return float(np.polyfit(xc, yc, 1)[0])

    mat = cell.to_numpy(dtype=float)
    point = _slope(mat)
    rs = np.random.default_rng(seed)
    n = mat.shape[0]
    draws = np.array([_slope(mat[rs.integers(0, n, size=n)]) for _ in range(1000)])
    draws = draws[~np.isnan(draws)]
    return dict(
        slope=point,
        lo=float(np.percentile(draws, 2.5)), hi=float(np.percentile(draws, 97.5)),
        n_boot=int(len(draws)),
    )


def circular_null(df: pd.DataFrame, k: int = 96, *, seed: int = SEED):
    """Prereg §7 C3. Roll result_yes by market WITHIN series, never redraw, so turnover and
    the fill set are held exactly fixed and only the outcome is decoupled from the price
    path. The real-print ledger's own null paid +0.674 and EXCLUDED its +0.254, i.e. most
    of the real level is a static price-level effect rather than conditional skill."""
    out = []
    for ser, d in df.groupby("series", observed=True):
        mkts = d.ticker.drop_duplicates().tolist()
        if len(mkts) < 2:
            continue
        shift = k % len(mkts)
        ry = d.groupby("ticker", observed=True).result_yes.first()
        ry = ry.reindex(mkts)
        rolled = dict(zip(mkts, np.roll(ry.to_numpy(), shift)))
        dd = d.copy()
        dd["result_yes_null"] = dd.ticker.map(rolled)
        dd["settle_null_c"] = dd.s * (dd.px_c - 100.0 * dd.result_yes_null)
        out.append(dd)
    allz = pd.concat(out, ignore_index=True)
    return cluster_boot(allz, "settle_null_c", seed=seed + 1)


def overlaps(ci: dict, band: tuple[float, float]) -> bool:
    return ci["lo"] <= band[1] and ci["hi"] >= band[0]


def main() -> None:
    rep: dict = {"prereg": "PREREG_instrument_residual_20261009.md", "controls": {}}

    cols = ["ticker", "series", "s", "px_c", "spread_ticks", "result_yes",
            "settle_c", "mk5s_c", "mk60s_c", "vt", "fold", "samp_rate", "n_fillable",
            "kind"]
    df = pq.read_table(DATA, columns=cols).to_pandas()
    rep["raw"] = dict(rows=int(len(df)), markets=int(df.ticker.nunique()),
                      vt_min=str(pd.to_datetime(df.vt.min(), unit="us", utc=True)),
                      vt_max=str(pd.to_datetime(df.vt.max(), unit="us", utc=True)),
                      kinds=df.kind.value_counts().to_dict())

    # ---- C1: settlement provenance (prereg §7). A failed C1 VOIDS the study. ----------
    n_null = int(df.result_yes.isna().sum())
    ry_mean = float(df.result_yes.mean())
    rep["controls"]["C1_settlement"] = dict(
        null_result_yes=n_null, result_yes_mean=round(ry_mean, 5),
        passed=bool(n_null == 0 and 0.3 <= ry_mean <= 0.7),
    )

    # ---- C2: the identity this whole study rests on. A failed C2 VOIDS the study. -----
    recomputed = df.s * (df.px_c - 100.0 * df.result_yes)
    dev = float(np.nanmax(np.abs(recomputed - df.settle_c)))
    rep["controls"]["C2_identity"] = dict(
        max_abs_deviation=dev, formula="s*(px_c - 100*result_yes) == settle_c",
        passed=bool(dev < 1e-9),
    )

    if not (rep["controls"]["C1_settlement"]["passed"]
            and rep["controls"]["C2_identity"]["passed"]):
        rep["VERDICT"] = "VOID — a frozen control failed; see controls"
        OUT.parent.mkdir(exist_ok=True)
        OUT.write_text(json.dumps(rep, indent=2))
        print(json.dumps(rep["controls"], indent=2))
        print("VOID")
        return

    # ---- universe, frozen §3 ---------------------------------------------------------
    btc = df[df.series == "KXBTC15M"]
    u = df[df.series.isin(SERIES_8) & df.px_c.between(*MID_BAND)].copy()
    rep["universe"] = dict(
        series=SERIES_8, mid_band=list(MID_BAND),
        rows=int(len(u)), markets=int(u.ticker.nunique()),
        frac_of_raw=round(len(u) / len(df), 4),
        samp_rate_mean=round(float(u.samp_rate.mean()), 4),
        samp_rate_p05=round(float(u.samp_rate.quantile(0.05)), 4),
        inventory_walk_drop_frac=round(
            1.0 - float((u.groupby("ticker", observed=True).size()
                         / u.groupby("ticker", observed=True).n_fillable.first()
                         / u.groupby("ticker", observed=True).samp_rate.first()).mean()), 4),
    )

    # ---- P1: the matched level -------------------------------------------------------
    p1 = cluster_boot(u, "settle_c")
    p1_printwt = dict(mean=float(u.settle_c.mean()), n_rows=int(len(u)))
    if overlaps(p1, POP_BAND):
        v1 = "INSTRUMENT VINDICATED (overlaps the population band)"
    elif p1["hi"] < POP_BAND[0]:
        v1 = "RESIDUAL REAL AND SIGNED DOWN (entirely below the population band)"
    elif p1["lo"] > POP_BAND[1]:
        v1 = "SELECTION, NOT UNDERSTATEMENT (entirely above the population band)"
    else:
        v1 = "INDETERMINATE"
    rep["P1_level"] = dict(
        per_market=p1, print_weighted=p1_printwt,
        population_band=list(POP_BAND), touch_band=list(TOUCH_BAND),
        overlaps_population=overlaps(p1, POP_BAND),
        overlaps_touch=overlaps(p1, TOUCH_BAND),
        ratio_to_published_population=(round(0.254 / p1["mean"], 3)
                                       if p1["mean"] not in (None, 0) else None),
        verdict=v1,
    )

    # ---- P2: flat-in-spread ----------------------------------------------------------
    by_sp = {}
    for name, lo, hi, _mid in SPREAD_BUCKETS:
        d = u[u.spread_ticks.between(lo, hi)]
        by_sp[name] = cluster_boot(d, "settle_c")
    sl = boot_slope(u, "settle_c")
    if sl["lo"] <= 0 <= sl["hi"]:
        v2 = "FLAT IN SPREAD reproduced — the width gate selects turnover, not edge"
    elif sl["lo"] > 0:
        v2 = "SIM MANUFACTURES A SPREAD GRADIENT the real tape does not have"
    else:
        v2 = "NEGATIVE GRADIENT (sim pays less in wider books)"
    rep["P2_flat_in_spread"] = dict(
        by_bucket=by_sp, slope=sl, ledger_profile=LEDGER_SPREAD_PROFILE, verdict=v2)

    # ---- secondary: horizon ladder on ONE identical fill set -------------------------
    rep["S1_horizon_ladder"] = {
        "mk5s_c": cluster_boot(u, "mk5s_c"),
        "mk60s_c": cluster_boot(u, "mk60s_c"),
        "settle_c": cluster_boot(u, "settle_c"),
        "ledger_ladder": {"k1_bar_90s": 0.176, "settlement": 0.254, "delta": 0.078},
    }

    # ---- secondary: per series -------------------------------------------------------
    per_ser = {s: cluster_boot(u[u.series == s], "settle_c") for s in SERIES_8}
    rep["S2_per_series"] = dict(
        cells=per_ser,
        n_positive=int(sum(1 for v in per_ser.values() if v["mean"] and v["mean"] > 0)),
        ledger_n_positive="7 of 8 (HYPE -0.045)",
    )

    # ---- secondary: price band -------------------------------------------------------
    bands = [(15, 30), (30, 45), (45, 55), (55, 70), (70, 85)]
    rep["S3_price_band"] = {
        f"{a}-{b}c": cluster_boot(u[u.px_c.between(a, b)], "settle_c") for a, b in bands
    }

    # ---- secondary: BTC, descriptor only ---------------------------------------------
    rep["S4_btc_descriptor"] = cluster_boot(
        btc[btc.px_c.between(*MID_BAND)], "settle_c")

    # ---- D1/D2: POST-HOC diagnostics ------------------------------------------------
    # Declared post-hoc and labelled as such. They exist because P1 passed with an
    # internal contradiction the prereg's §7 anticipated: the per-market reducer read
    # +0.46 while the pooled-flat mean read +0.03 and ALL FIVE price bands read negative.
    # The mean of per-market means is not additive over row subsets, so that combination
    # is arithmetically possible — and it says the level is carried by low-row-count
    # markets. D1 asks which markets, D2 asks the same spread question on the horizon the
    # seat is actually scored on.
    sm = u.groupby("ticker", observed=True).s.mean().abs()
    u["abs_s"] = u.ticker.map(sm)
    one, two = u[u.abs_s > 0.20], u[u.abs_s <= 0.20]
    n_all = u.ticker.nunique()
    d1 = {}
    for lab, d in (("two_sided_abs_s_le_0.20", two), ("one_sided_abs_s_gt_0.20", one)):
        d1[lab] = {c: cluster_boot(d, c) for c in ("settle_c", "mk60s_c", "mk5s_c")}
        d1[lab]["share_of_markets"] = round(d.ticker.nunique() / n_all, 4)
        d1[lab]["share_of_fills"] = round(len(d) / len(u), 4)
        d1[lab]["contribution_to_pooled_settle"] = round(
            cluster_boot(d, "settle_c")["mean"] * d.ticker.nunique() / n_all, 4)
    d1["note"] = (
        "a one-sided fill set held to settlement is a directional bet on the binary, not "
        "maker capture (a-maker-with-a-crossing-exit-is-a-directional-bet); the "
        "equal-weight-per-market reducer up-weights exactly those markets because being "
        "one-sided and having few fills are the same condition here")
    rep["D1_two_sided_split_POSTHOC"] = d1

    # row-count deciles: the mechanism, in one monotone column
    gm = u.groupby("ticker", observed=True).agg(n=("settle_c", "size"),
                                                m=("settle_c", "mean"),
                                                s_mean=("s", "mean"))
    gm["dec"] = pd.qcut(gm.n, 10, labels=False, duplicates="drop")
    rep["D1b_rowcount_deciles_POSTHOC"] = (
        gm.groupby("dec").agg(markets=("m", "size"), rows_lo=("n", "min"),
                              rows_hi=("n", "max"), mean_settle=("m", "mean"),
                              mean_abs_s=("s_mean", lambda x: float(x.abs().mean())))
        .round(4).to_dict("index"))

    d2 = {}
    for col in ("mk5s_c", "mk60s_c", "settle_c"):
        cells, pts = {}, []
        for name, lo, hi, mid in SPREAD_BUCKETS:
            c = cluster_boot(u[u.spread_ticks.between(lo, hi)], col)
            cells[name] = c
            if c["mean"] is not None:
                pts.append((mid, c["mean"]))
        x = np.array([p[0] for p in pts]); y = np.array([p[1] for p in pts])
        d2[col] = dict(by_bucket=cells, slope=float(np.polyfit(x, y, 1)[0]))
    d2["note"] = (
        "the gradient the entire width-gate framing rests on is a 5-SECOND-MARKOUT "
        "phenomenon: tight, monotone and overwhelming at mk5s, half gone at mk60s, and "
        "absent at settlement, where the real print tape independently reads FLAT. A "
        "capture that does not survive its own horizon is a mid artifact, and the mid of "
        "a 10c-wide book is not a tradeable price "
        "(a-fifteen-minute-binarys-price-resolution-is-coarser-than-its-spread)")
    rep["D2_spread_gradient_by_horizon_POSTHOC"] = d2

    # ---- D3: POST-HOC, D1 x D2 -------------------------------------------------------
    # The cell that decides the headline. D2's narrow-book settlement number (+0.218) is
    # contaminated by D1's one-sided tail; crossing the two removes it. Needed because
    # the whole question is what a TWO-SIDED maker earns in the book the gate declines.
    d3 = {"by_bucket": {}, "room4_cut": {}}
    for name, lo, hi in (("2", 2, 2), ("3", 3, 3), ("4", 4, 4), ("5-6", 5, 6),
                         ("7+", 7, 10**9)):
        d = two[two.spread_ticks.between(lo, hi)]
        d3["by_bucket"][name] = {c: cluster_boot(d, c)
                                 for c in ("mk5s_c", "mk60s_c", "settle_c")}
    for name, m in (("declines_ticks_lt_4", two.spread_ticks < 4),
                    ("takes_ticks_ge_4", two.spread_ticks >= 4)):
        d = two[m]
        d3["room4_cut"][name] = {c: cluster_boot(d, c)
                                 for c in ("mk5s_c", "mk60s_c", "settle_c")}
        d3["room4_cut"][name]["share_of_fills"] = round(len(d) / len(two), 4)
    d3["ledger_is_flat_at"] = 0.25
    d3["note"] = (
        "the residual, localized: the print tape says EVERY width pays the same ~+0.25 "
        "c/ct, while the sim says the 2-tick book (62% of fills) LOSES. Those intervals "
        "do not overlap, so the instrument's disagreement with the venue is concentrated "
        "entirely in the narrow book — which is exactly the population the width gate "
        "exists to decline.")
    rep["D3_two_sided_by_spread_POSTHOC"] = d3

    # ---- C3: the circular-shift null -------------------------------------------------
    null = circular_null(u)
    rep["controls"]["C3_circular_null"] = dict(
        null=null, real=p1,
        real_above_null=bool(p1["mean"] > null["mean"]),
        null_excludes_real=bool(not (null["lo"] <= p1["mean"] <= null["hi"])),
        ledger_null=0.674,
        note=("a level above zero but below its own null is a static price-level effect, "
              "not conditional skill"),
    )

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(rep, indent=2))

    # ---- console summary -------------------------------------------------------------
    def fmt(c):
        if c["mean"] is None:
            return "  (empty)"
        return (f"{c['mean']:+8.4f}  [{c['lo']:+.4f}, {c['hi']:+.4f}]  "
                f"n_mkt={c['n_markets']:>5d}  n={c['n_rows']:>7d}")

    print("=" * 78)
    print("CONTROLS")
    print(f"  C1 settlement : passed={rep['controls']['C1_settlement']['passed']} "
          f"nulls={n_null} mean(result_yes)={ry_mean:.4f}")
    print(f"  C2 identity   : passed={rep['controls']['C2_identity']['passed']} "
          f"max|dev|={dev:.2e}")
    print()
    print(f"UNIVERSE: {rep['universe']['rows']} rows / "
          f"{rep['universe']['markets']} markets, 8 non-BTC series, px in [15,85]")
    print()
    print("P1 — matched level, maker gross held to settlement (c/ct)")
    print("  sim, per-market  :", fmt(p1))
    print(f"  sim, print-wt    : {p1_printwt['mean']:+8.4f}")
    print(f"  real prints, pop : +0.2540  [+0.1180, +0.3860]")
    print(f"  real prints,touch: +0.3473  [+0.0415, +0.6501]")
    print(f"  VERDICT: {v1}")
    print(f"    overlaps population band = {rep['P1_level']['overlaps_population']}, "
          f"touch band = {rep['P1_level']['overlaps_touch']}")
    print()
    print("P2 — flat in spread?  (ledger: +0.249/+0.264/+0.252/+0.240/+0.244, FLAT)")
    for k, v in by_sp.items():
        print(f"  spread_ticks {k:>4s} :", fmt(v))
    print(f"  slope = {sl['slope']:+.5f} c/ct per tick  "
          f"[{sl['lo']:+.5f}, {sl['hi']:+.5f}]")
    print(f"  VERDICT: {v2}")
    print()
    print("S1 — horizon ladder, ONE identical fill set (ledger: 90s +0.176 -> settle +0.254)")
    for k in ("mk5s_c", "mk60s_c", "settle_c"):
        print(f"  {k:>9s} :", fmt(rep["S1_horizon_ladder"][k]))
    print()
    print("S2 — per series")
    for s, v in per_ser.items():
        print(f"  {s:>10s} :", fmt(v))
    print(f"  positive: {rep['S2_per_series']['n_positive']} of 8  "
          f"(ledger: 7 of 8)")
    print()
    print("S3 — price band")
    for k, v in rep["S3_price_band"].items():
        print(f"  {k:>8s} :", fmt(v))
    print()
    print("S4 — KXBTC15M (descriptor, excluded from gates)")
    print("           :", fmt(rep["S4_btc_descriptor"]))
    print()
    print("D1 — POST-HOC: who carries the settlement level? (|mean s| per market)")
    for lab in ("two_sided_abs_s_le_0.20", "one_sided_abs_s_gt_0.20"):
        c = rep["D1_two_sided_split_POSTHOC"][lab]
        print(f"  {lab}  ({c['share_of_markets']:.1%} of markets, "
              f"{c['share_of_fills']:.1%} of fills) "
              f"contributes {c['contribution_to_pooled_settle']:+.4f}")
        for col in ("settle_c", "mk60s_c", "mk5s_c"):
            print(f"      {col:>9s} :", fmt(c[col]))
    print()
    print("D2 — POST-HOC: the spread gradient, by horizon, on identical rows")
    for col in ("mk5s_c", "mk60s_c", "settle_c"):
        print(f"  -- {col}  slope {d2[col]['slope']:+.5f} c/ct per tick")
        for name, c in d2[col]["by_bucket"].items():
            print(f"     ticks {name:>4s} :", fmt(c))
    print()
    print("C3 — circular-shift null (turnover held fixed; ledger null +0.674)")
    print("  null     :", fmt(null))
    print("  real     :", fmt(p1))
    print(f"  real above its own null: {rep['controls']['C3_circular_null']['real_above_null']}")
    print("=" * 78)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
