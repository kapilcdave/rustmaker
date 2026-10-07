#!/usr/bin/env python3
"""Score PREREG_open_band.md on every live penny journal we hold (2026-09-24 -> 10-05).

The prereg was frozen 2026-09-30 09:20Z and asks for 300 settled markets under the
`--open-min-c 10 --open-max-c 90` band. It was never scored. Nothing here places an order.

P&L per market = cash + position * settlement, from the journal's own `fill` rows and the
PUBLIC `/markets/{ticker}` result. Fees are zero on 15M crypto maker fills (fee_cost
0.000000 on every row). The instrument is the one `leftover_portfolio.py` used, and it is
re-reconciled here against three runs whose venue ledgers are known, because a journal
reconstruction has named the wrong thing before (the-venues-own-ledger-beats-a-journal-
reconstruction): a sum that reconciles does not validate a decomposition, so the
reconciliation is printed as a gate, not as a footnote.

Usage: python3 score_open_band.py
"""
import gzip
import json
import math
from collections import defaultdict, Counter

import numpy as np

FILLS = "data/leftover/band_fills.gz"
META = "data/leftover/band_meta.json"
RESULTS = "data/leftover/results.json"

CRYPTO = ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M",
          "KXHYPE15M", "KXBNB15M", "KXZEC15M", "KXNEAR15M")

# Venue-ledger truth for three runs, from the live write-ups. The replay must land on these.
VENUE = {"live_1790327908469.jsonl.gz": ("run5", 5.06),
         "live_1790417285588.jsonl.gz": ("run9b", -3.05),
         "live_1790635109218.jsonl.gz": ("run12", 9.86)}

# The band flips on 2026-09-30 09:07:53Z; clip drops 2 -> 1 at 20:49Z; penny room 4 -> 3 on
# two 10-01/10-02 runs. Cohorts are read off each journal's own `start` row, never a name.
FOREIGN_PNL = {'live_1790635109218.jsonl.gz': -0.96, 'live_1790801344013.jsonl.gz': -9.5294, 'live_1790912417887.jsonl.gz': -0.001}

HOLDOUT_US = 1790750665797 * 1000  # 2026-09-30 06:44Z: the DOGE/SOL holdout boundary


def cohort(cfg, clip):
    if cfg is None or not cfg.get("series"):
        return None
    banded = cfg.get("open_lo_c") == 10.0 and cfg.get("open_hi_c") == 90.0
    pr = cfg.get("penny_room")
    if pr is None:
        return "pre_capped"          # before the round-net era: a different seat
    if pr != 4:
        return f"band_pr{pr}" if banded else f"noband_pr{pr}"
    return f"{'band' if banded else 'noband'}_c{clip}"


def load():
    """⚠ The `fill` channel is ACCOUNT-WIDE, not order-scoped: another rig's fills land in this
    engine's journal. Found 2026-10-07 — one 30.74-contract TAKER fill in KXMLBTOTAL inside the
    09-30 20:49Z run carried -$9.53, which is 81% of that whole cohort and belongs to the sports
    runner. Our seat is `post_only`, so `is_taker` is the discriminator, and the configured
    `--series` list is the second. Without both filters a journal replay scores other strategies.
    """
    res = json.load(open(RESULTS))
    meta = json.load(open(META))
    # clip is not journalled. max_round_net_fp is: every launch used round-net == 2*clip.
    RNET_CLIP = {200: 1, 400: 2}
    ctmode = defaultdict(Counter)
    rows = []
    raw = []
    foreign = defaultdict(lambda: [0, 0.0])
    for line in gzip.open(FILLS, "rt"):
        j, rest = line.split(" ", 1)
        v = json.loads(rest)["v"]
        ct = float(v["count_fp"])
        cfg = (meta.get(j) or {}).get("cfg") or {}
        ours = (not v.get("is_taker", False)) and v["market_ticker"].split("-")[0] in set(cfg.get("series") or [])
        if not ours:
            foreign[j][0] += 1
            foreign[j][1] += ct
            continue
        ctmode[j][ct] += 1
        raw.append((j, v, ct))
    clip = {}
    for j in ctmode:
        rn = ((meta.get(j) or {}).get("cfg") or {}).get("max_round_net_fp")
        clip[j] = RNET_CLIP.get(rn) or int(round(ctmode[j].most_common(1)[0][0])) or 1
    if foreign:
        print("  foreign/taker fills dropped (not this seat):")
        for j, (n, c) in sorted(foreign.items()):
            print(f"    {j[5:18]}  {n} fills, {c:.2f} ct")
    for j, v, ct in raw:
        tk = v["market_ticker"]
        r = res.get(tk)
        if not r or r.get("result") not in ("yes", "no"):
            continue
        series = tk.split("-")[0]
        rows.append({
            "journal": j, "ticker": tk, "series": series,
            "round": tk.split("-")[1] if "-" in tk else "?",
            "ts_us": v["ts_ms"] * 1000,
            "sgn": 1.0 if v["book_side"] == "bid" else -1.0,
            "px": float(v["yes_price_dollars"]) * 100.0,
            "ct": ct,
            "y": 100.0 if r["result"] == "yes" else 0.0,
            "cohort": cohort((meta.get(j) or {}).get("cfg"), clip.get(j, 1)),
            "clip": clip.get(j, 1),
        })
    rows.sort(key=lambda x: x["ts_us"])
    return rows, meta, clip


def per_market(rows):
    """cash + pos * settlement, per (journal, ticker). A market quoted by two runs is two units."""
    cash = defaultdict(float)
    pos = defaultdict(float)
    ct = defaultdict(float)
    info = {}
    for f in rows:
        k = (f["journal"], f["ticker"])
        cash[k] -= f["sgn"] * f["px"] * f["ct"]
        pos[k] += f["sgn"] * f["ct"]
        ct[k] += f["ct"]
        info[k] = f
    out = []
    for k, f in info.items():
        out.append({"key": k, "pnl_c": cash[k] + pos[k] * f["y"], "ct": ct[k],
                    "series": f["series"], "round": f["round"], "cohort": f["cohort"],
                    "clip": f["clip"], "journal": f["journal"], "ts_us": f["ts_us"],
                    "leftover": abs(pos[k]) > 1e-9})
    out.sort(key=lambda m: m["ts_us"])
    return out


def cluster_se(vals, keys):
    """Cluster-robust SE of a mean: sum within cluster, then SE over cluster sums."""
    g = defaultdict(float)
    for v, k in zip(vals, keys):
        g[k] += v
    n = len(vals)
    m = len(g)
    if m < 2:
        return float("nan")
    s = np.array(list(g.values()))
    return math.sqrt(s.var(ddof=1) * m) / n


def describe(mkts, label, ind=""):
    if not mkts:
        print(f"{ind}{label:26} n=0")
        return None
    p = np.array([m["pnl_c"] for m in mkts])
    c = np.array([m["ct"] for m in mkts])
    n = len(p)
    mean = p.mean()
    se_mkt = p.std(ddof=1) / math.sqrt(n)
    se_rnd = cluster_se(p, [m["round"] for m in mkts])
    se_day = cluster_se(p, [m["round"][:7] for m in mkts])
    half = n // 2
    h1, h2 = p[:half].mean(), p[half:].mean()
    cpc = p.sum() / c.sum()
    print(f"{ind}{label:26} n={n:5d} ct={int(c.sum()):6d} tot={p.sum()/100:+8.2f}$ "
          f"c/mkt={mean:+7.3f} se_mkt={se_mkt:5.3f} (lo95 {mean-1.96*se_mkt:+7.3f}) "
          f"se_rnd={se_rnd:5.3f} (lo95 {mean-1.96*se_rnd:+7.3f}) se_day={se_day:5.3f} "
          f"c/ct={cpc:+6.3f} H1={h1:+7.3f} H2={h2:+7.3f}")
    return {"n": n, "mean": mean, "se_mkt": se_mkt, "se_rnd": se_rnd, "se_day": se_day,
            "h1": h1, "h2": h2, "cpc": cpc, "tot": p.sum(), "p": p}


def main():
    rows, meta, clip = load()
    mkts = per_market(rows)

    print("=" * 118)
    print("GATE 0 - instrument reconciliation against the venue ledger (must match before any new number is read)")
    print("=" * 118)
    by_j = defaultdict(list)
    for m in mkts:
        by_j[m["journal"]].append(m)
    # The published venue figures are a shard-2 BALANCE DELTA, i.e. account-level: they include
    # any other rig trading the same shard. So the account-level replay is what must match them,
    # and the seat-only replay is reported beside it. Run 12's "own ledger +9.86" was itself
    # contaminated by a foreign -0.96 gold taker fill; its seat number is +10.82.
    ok = True
    for j, (name, truth) in VENUE.items():
        seat = sum(x["pnl_c"] for x in by_j.get(j, [])) / 100.0
        acct = seat + FOREIGN_PNL.get(j, 0.0)
        d = acct - truth
        flag = "OK" if abs(d) < 0.05 else "MISMATCH"
        ok &= abs(d) < 0.05
        print(f"  {name:6} {j}  account-level replay {acct:+7.2f}$  venue {truth:+7.2f}$  "
              f"diff {d:+5.2f}  {flag}   | seat-only {seat:+7.2f}$")
    print(f"  -> instrument {'VALIDATED' if ok else 'NOT VALIDATED'}")

    print()
    print("=" * 118)
    print("Cohorts as read off each journal's own start row")
    print("=" * 118)
    co = defaultdict(list)
    for m in mkts:
        co[m["cohort"]].append(m)
    for k in sorted(co, key=lambda x: str(x)):
        js = sorted({m["journal"][5:18] for m in co[k]})
        print(f"  {str(k):14} mkts={len(co[k]):5d} clips={sorted({m['clip'] for m in co[k]})} runs={len(js)}")

    def crypto(ms):
        return [m for m in ms if m["series"] in CRYPTO]

    band = crypto(co.get("band_c1", []) + co.get("band_c2", []))
    print()
    print("=" * 118)
    print("PRIMARY - PREREG_open_band: settled crypto markets under the 10-90c band, held to settlement")
    print("=" * 118)
    print(f"  prereg trigger: 300 settled markets under the band.  have: {len(band)}")
    prim = describe(band, "BAND pooled (primary)", "  ")
    describe(crypto(co.get("band_c1", [])), "  band, clip 1", "  ")
    describe(crypto(co.get("band_c2", [])), "  band, clip 2", "  ")
    print()
    print("  comparison arms named by the prereg (per CONTRACT, clip differs):")
    describe(crypto(co.get("noband_c2", [])), "  no-band clip 2 (06:44Z)", "  ")
    describe(crypto(co.get("noband_c1", [])), "  no-band clip 1 (capped)", "  ")
    print()
    print("  not part of the band population (different rule / instrument):")
    describe(crypto(co.get("band_pr3", [])), "  band, penny room 3", "  ")
    describe(crypto(co.get("pre_capped", [])), "  pre-round-net era", "  ")
    describe([m for m in co.get("band_c1", []) if m["series"] not in CRYPTO],
             "  band, commodities", "  ")

    if prim:
        print()
        print("=" * 118)
        print("Concentration: minus-best-k, against a normal with the same mean and sd")
        print("=" * 118)
        p = np.sort(prim["p"])[::-1]
        tot = p.sum()
        for k in (0, 5, 10, 20):
            left = tot - p[:k].sum()
            # a normal with the same mean/sd, same n, loses its own top k in expectation
            mu, sd, n = prim["mean"], prim["p"].std(ddof=1), prim["n"]
            draws = np.random.default_rng(7).normal(mu, sd, (400, n))
            norm = np.median([d.sum() - np.sort(d)[::-1][:k].sum() for d in draws])
            print(f"  minus best {k:2d}: {left/100:+8.2f}$   normal(same mean,sd) median {norm/100:+8.2f}$")

        print()
        print("=" * 118)
        print("DOGE/SOL holdout (prereg: fills after 2026-09-30 06:44Z only; drop only if both <0 c/ct")
        print("                  AND pooled <0 at one-sided p<0.10, market-clustered)")
        print("=" * 118)
        hold = [m for m in crypto(mkts) if m["ts_us"] >= HOLDOUT_US]
        for s in ("KXDOGE15M", "KXSOL15M"):
            describe([m for m in hold if m["series"] == s], f"  {s}", "  ")
        ds = [m for m in hold if m["series"] in ("KXDOGE15M", "KXSOL15M")]
        d = describe(ds, "  DOGE+SOL pooled", "  ")
        if d:
            t = d["mean"] / d["se_mkt"]
            from statistics import NormalDist
            print(f"    one-sided p(mean<0) = {NormalDist().cdf(t):.4f}  -> "
                  f"{'DROP' if (d['mean'] < 0 and NormalDist().cdf(t) < 0.10) else 'KEEP'}")

        print()
        print("=" * 118)
        print("Per-series, band population (reported, NOT a selection instrument)")
        print("=" * 118)
        for s in sorted({m["series"] for m in band}):
            describe([m for m in band if m["series"] == s], f"  {s}", "  ")

        print()
        print("=" * 118)
        print("Mechanism split: pairs (flat at close) vs leftovers (held to settlement)")
        print("=" * 118)
        describe([m for m in band if not m["leftover"]], "  flat at close (paired)", "  ")
        describe([m for m in band if m["leftover"]], "  leftover held to settle", "  ")


if __name__ == "__main__":
    main()
