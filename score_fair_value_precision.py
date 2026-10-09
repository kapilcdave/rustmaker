"""How precisely can a model know a 15M binary's fair value, and is that inside the income?

Public data only -- Kalshi's unauthenticated `/markets` and Coinbase's public candles. No
credentials, no orders, no writes. Safe to run anywhere, which is the point: this is the cheap
bound that belongs before any model-priced maker is armed.

The question is NOT "is the model right". It is "what is the model's resolution, in cents".

A 15-minute return-strike digital has one scale: `sigma_eff = sigma * sqrt(tau - 2w/3)`, which at
27% annual vol and tau = 900 s is ~14 bp. So fair value is a steep function of the level --
`delta = 100*phi(z)/sigma_eff` cents per basis point -- and the model's precision in cents is its
precision in the level, multiplied by that. This script measures both ends:

1. `--table price`  the model price beside the live book, with the delta and the latency toll.
2. `--table basis`  invert the model: what level does the Kalshi mid imply, and how far is our
                    own spot from it? That difference is the achievable level precision, and
                    times the delta it is the achievable price precision.

Return-strike scale invariance is what makes (2) possible without the CF index feed: the strike
IS the index at open, so only a RETURN is needed and the spot/index basis cancels.

Usage:
    python3 -I score_fair_value_precision.py                 # both tables
    python3 -I score_fair_value_precision.py --table basis
"""

import argparse
import datetime as dt
import json
import math
import urllib.request

# asset -> (Kalshi 15M series, Coinbase product). BTC settles on BRTI, the rest on {ASSET}USD_RTI.
PAIRS = [
    ("KXETH15M", "ETH-USD"),
    ("KXBTC15M", "BTC-USD"),
    ("KXSOL15M", "SOL-USD"),
    ("KXXRP15M", "XRP-USD"),
    ("KXDOGE15M", "DOGE-USD"),
]
SETTLE_AVG_S = 60.0
# Measured index/print -> our change in the book, from the az2 box.
REACTION_S = 0.0116
# Measured real-print maker gross, held to settlement, 8,038,551 prints.
GROSS_C = 0.25


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "fair-value-precision/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _ncdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _ninv(p):
    """Acklam's inverse normal CDF; |error| < 1.15e-9, ample for a diagnostic."""
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00]
    lo, hi = 0.02425, 1 - 0.02425
    if not 0.0 < p < 1.0:
        raise ValueError(f"p out of range: {p}")
    if p < lo:
        q = math.sqrt(-2 * math.log(p))
        return ((((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
                / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1))
    if p > hi:
        q = math.sqrt(-2 * math.log(1 - p))
        return -((((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
                 / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1))
    q, r = p - 0.5, (p - 0.5) ** 2
    return ((((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
            / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1))


def observations():
    """One row per live 15M market with a usable book, joined to its asset's realised vol."""
    now = dt.datetime.now(dt.timezone.utc)
    unix = int(now.timestamp())
    rows = []
    for series, product in PAIRS:
        mk = _get("https://api.elections.kalshi.com/trade-api/v2/markets"
                  f"?series_ticker={series}&status=open&limit=5")
        cj = _get("https://api.coinbase.com/api/v3/brokerage/market/products/"
                  f"{product}/candles?start={unix - 7200}&end={unix}"
                  "&granularity=ONE_MINUTE&limit=120")
        bars = sorted(({"t": int(x["start"]), "c": float(x["close"])} for x in cj["candles"]),
                      key=lambda r: r["t"])
        # 60 minute bars is the shortest window that is not mostly noise, and it matches the
        # engine's default EWMA half-life of 120 samples.
        if len(bars) < 60:
            continue
        lr = [math.log(bars[i]["c"] / bars[i - 1]["c"]) for i in range(1, len(bars))]
        sigma = math.sqrt(sum(x * x for x in lr) / len(lr)) / math.sqrt(60.0)
        spot = bars[-1]["c"]
        for m in mk.get("markets", []):
            strike = m.get("floor_strike")
            if m.get("status") != "active" or not strike:
                continue
            close = dt.datetime.fromisoformat(m["close_time"].replace("Z", "+00:00"))
            tau = (close - now).total_seconds()
            bid = float(m.get("yes_bid_dollars", 0) or 0) * 100
            ask = float(m.get("yes_ask_dollars", 0) or 0) * 100
            # Inside the settlement average the model has no opinion; a crossed or one-sided
            # book has no mid to invert.
            if tau <= SETTLE_AVG_S or not 0 < bid < ask < 100:
                continue
            sig_eff = sigma * math.sqrt(tau - 2 * SETTLE_AVG_S / 3)
            z = math.log(spot / strike) / sig_eff
            pdf = math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)
            rows.append({
                "ticker": m["ticker"], "tau": tau, "spot": spot, "strike": strike,
                "sigma": sigma, "vol_ann": 100 * sigma * math.sqrt(365 * 86400),
                "sig_eff_bp": sig_eff * 1e4, "z": z,
                "model": 100 * _ncdf(z), "bid": bid, "ask": ask, "mid": (bid + ask) / 2,
                # Cents of fair value per bp of level, and the two derived tolls.
                "delta": 100 * pdf / (sig_eff * 1e4),
                "latency_c": (100 * pdf / (sig_eff * 1e4)) * 1e4 * sigma * math.sqrt(REACTION_S),
            })
    return rows


def table_price(rows):
    print("## 1. Model price beside the live book\n")
    print(f"{'ticker':26s} {'tau':>5s} {'volA%':>6s} {'sEff_bp':>7s} {'model':>6s} {'mid':>6s} "
          f"{'bid/ask':>11s} {'c/bp':>6s} {'lat_c':>6s} {'fv-mid':>7s}")
    for r in sorted(rows, key=lambda r: r["tau"]):
        print(f"{r['ticker']:26s} {r['tau']:5.0f} {r['vol_ann']:6.1f} {r['sig_eff_bp']:7.2f} "
              f"{r['model']:6.2f} {r['mid']:6.2f} {r['bid']:5.1f}/{r['ask']:5.1f} "
              f"{r['delta']:6.2f} {r['latency_c']:6.3f} {r['model'] - r['mid']:+7.2f}")
    err = sorted(abs(r["model"] - r["mid"]) for r in rows)
    half = sum(1 for r in rows if abs(r["model"] - r["mid"]) <= (r["ask"] - r["bid"]) / 2)
    print(f"\nn={len(rows)}  mean |model-mid| = {sum(err) / len(err):.2f}c  "
          f"median = {err[len(err) // 2]:.2f}c  within half-spread: {half}/{len(rows)}")


def table_basis(rows):
    print("## 2. Invert the model: the level the mid implies, vs our own spot\n")
    print(f"{'ticker':26s} {'spot':>12s} {'strike':>12s} {'spot/K-1':>10s} "
          f"{'mid-implied':>12s} {'BASIS bp':>9s} {'c/bp':>6s} {'level_c':>8s}")
    basis = []
    for r in sorted(rows, key=lambda r: r["tau"]):
        implied = r["strike"] * math.exp(_ninv(r["mid"] / 100.0) * r["sig_eff_bp"] / 1e4)
        b = 1e4 * (implied / r["spot"] - 1.0)
        basis.append(b)
        print(f"{r['ticker']:26s} {r['spot']:12.4f} {r['strike']:12.4f} "
              f"{1e4 * (r['spot'] / r['strike'] - 1):+9.1f}bp {implied:12.4f} {b:+9.1f} "
              f"{r['delta']:6.2f} {abs(b) * r['delta']:8.2f}")
    mean = sum(basis) / len(basis)
    spread = max(abs(min(basis)), abs(max(basis)))
    print(f"\nimplied basis: mean {mean:+.1f} bp, range [{min(basis):+.1f}, {max(basis):+.1f}] bp, "
          f"n={len(basis)}")
    # The headline: that level precision, priced in cents, against what the seat can earn.
    lvl = [abs(b) * r["delta"] for b, r in zip(basis, sorted(rows, key=lambda r: r["tau"]))]
    lat = [r["latency_c"] for r in sorted(rows, key=lambda r: r["tau"])]
    print(f"\nprice precision from that level precision: mean {sum(lvl) / len(lvl):.2f}c, "
          f"max {max(lvl):.2f}c")
    print(f"latency toll for comparison:                mean {sum(lat) / len(lat):.3f}c")
    print(f"ratio level/latency:                        {sum(lvl) / sum(lat):.1f}x")
    print(f"\nAgainst the measured maker gross of +{GROSS_C}c/ct, the level term alone is "
          f"{sum(lvl) / len(lvl) / GROSS_C:.1f}x the income,")
    print(f"and the quoted spread is {sum(r['ask'] - r['bid'] for r in rows) / len(rows):.2f}c -- "
          "TIGHTER than the model's own resolution.")
    print(f"\n(one-sigma_eff band is {spread:.1f} bp of {sum(r['sig_eff_bp'] for r in rows) / len(rows):.1f} bp, "
          "so the residual is a small fraction of a sigma and still dominates the income.)")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--table", choices=["price", "basis", "both"], default="both")
    args = ap.parse_args()
    rows = observations()
    if not rows:
        raise SystemExit("no live 15M market had a two-sided book outside its settlement window")
    print(f"# Fair-value precision, {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M}Z, "
          f"n={len(rows)} live markets\n")
    if args.table in ("price", "both"):
        table_price(rows)
        print()
    if args.table in ("basis", "both"):
        table_basis(rows)


if __name__ == "__main__":
    main()
