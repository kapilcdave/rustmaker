"""Toxic-flow table for Kalshi 15M markets (crypto or commodities), from a kalshi-mm15 probe tape.

Every print is a maker fill for somebody. For each one:
    s = +1 if the taker bought YES (the maker SOLD yes at P), -1 if the taker sold YES
    markout_h  = s * (P - mid(t + h))        cents per contract, maker's view
    settle     = s * (P - 100 * result_yes)  cents per contract, held to settlement
Features are read at t - LAG, where LAG is our measured reaction time (feed 6.2 ms + order into
book 4.4-5.4 ms), because a feature we could not have acted on in time is worthless to a quoter.

Usage: python3 tox.py tape.csv.gz
"""
import json
import sys
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

LAG_US = 11_000
BURST_US = 50_000
HORIZONS_S = [1, 5, 60]
REST = "https://external-api.kalshi.com/trade-api/v2"


def close_utc(ticker):
    # KXGOLD15M-26SEP240215-15: the embedded time is the close, in US Eastern (DST-aware).
    stamp = ticker.split("-")[1]
    t = datetime.strptime(stamp, "%y%b%d%H%M").replace(tzinfo=ZoneInfo("America/New_York"))
    return int(t.astimezone(timezone.utc).timestamp() * 1e6)


def results(tickers):
    out = {}
    for t in tickers:
        try:
            with urllib.request.urlopen(f"{REST}/markets/{t}", timeout=10) as r:
                out[t] = json.load(r)["market"].get("result", "")
        except Exception as e:  # noqa: BLE001
            print(f"result {t}: {e}", file=sys.stderr)
            out[t] = ""
    return out


def load(path):
    df = pd.read_csv(path, dtype={"a": str}, low_memory=False)
    b = df[df.kind == "B"].copy()
    for c in "abcd":
        b[c] = pd.to_numeric(b[c])
    b = b.rename(columns={"a": "bid", "b": "bidsz", "c": "ask", "d": "asksz", "venue_ms": "vt"})
    t = df[df.kind == "T"].copy()
    t = t.rename(columns={"a": "side", "b": "price", "c": "count", "venue_ms": "vt"})
    t["price"] = pd.to_numeric(t["price"]) / 100.0  # 1e-4 dollars -> cents
    t["count"] = pd.to_numeric(t["count"])
    return b, t


def per_ticker(b, t):
    rows = []
    for ticker, tt in t.groupby("ticker", sort=False):
        bb = b[b.ticker == ticker].sort_values("vt")
        bb = bb[(bb.bid > 0) & (bb.ask > 0)]
        if len(bb) < 10:
            continue
        bvt = bb.vt.to_numpy()
        mid = ((bb.bid + bb.ask) / 200.0).to_numpy()  # cents
        spread = ((bb.ask - bb.bid) / 100.0).to_numpy()
        bsz, asz = bb.bidsz.to_numpy(), bb.asksz.to_numpy()

        def at(x):
            i = np.searchsorted(bvt, x, side="right") - 1
            return np.clip(i, 0, len(bvt) - 1), i >= 0

        tt = tt.sort_values(["vt", "recv_us"]).copy()
        vt = tt.vt.to_numpy()
        s = np.where(tt.side.to_numpy() == "yes", 1.0, -1.0)
        P = tt.price.to_numpy()
        # Taker orders: same side + same venue ms = one order walking several resting orders.
        new_order = np.r_[True, (s[1:] != s[:-1]) | (vt[1:] != vt[:-1])]
        # Bursts: same-side orders within 50 ms.
        gap = np.r_[np.inf, np.diff(vt)]
        new_burst = new_order & np.r_[True, (s[1:] != s[:-1]) | (gap[1:] > BURST_US)]
        burst_id = np.cumsum(new_burst)
        order_id = np.cumsum(new_order)
        order_idx_in_burst = pd.Series(order_id).groupby(burst_id).transform(lambda x: x - x.iloc[0]).to_numpy()
        head_price = pd.Series(P).groupby(burst_id).transform("first").to_numpy()

        i_lag, ok = at(vt - LAG_US)
        i_1s, _ = at(vt - LAG_US - 1_000_000)
        i_5s, _ = at(vt - LAG_US - 5_000_000)
        mid_lag = mid[i_lag]
        imb = bsz[i_lag] / np.maximum(bsz[i_lag] + asz[i_lag], 1)
        # Same-side prints in the second before t - LAG (flow we could have seen).
        same_side_1s = np.zeros(len(vt))
        for side in (1.0, -1.0):
            m = s == side
            v = vt[m]
            same_side_1s[m] = np.searchsorted(v, v - LAG_US, side="right") - np.searchsorted(v, v - LAG_US - 1_000_000, side="right")
        d = pd.DataFrame({
            "ticker": ticker,
            "series": ticker.split("-")[0],
            "vt": vt,
            "s": s,
            "P": P,
            "count": tt["count"].to_numpy(),
            "ok": ok,
            "is_head": order_idx_in_burst == 0,
            "same_price_as_head": P == head_price,
            "burst_order_idx": order_idx_in_burst,
            "spread_lag": spread[i_lag],
            "mid_lag": mid_lag,
            # momentum in the taker's direction: + means the mid was already moving their way
            "mom1s_taker": s * (mid_lag - mid[i_1s]),
            "mom5s_taker": s * (mid_lag - mid[i_5s]),
            # book imbalance on the side the taker is hitting: s=+1 hits ask, so ask-thin is (imb high)
            "imb_taker": np.where(s > 0, imb, 1 - imb),
            "same_side_1s": same_side_1s,
            "ttc_s": (close_utc(ticker) - vt) / 1e6,
        })
        for h in HORIZONS_S:
            j, _ = at(vt + h * 1_000_000)
            d[f"mk{h}s"] = s * (P - mid[j])
        rows.append(d)
    return pd.concat(rows, ignore_index=True)


def cluster_se(x, c):
    """SE of a contract-weighted mean, clustered by market (all fills in a market share one
    settlement, so the market is the unit of evidence)."""
    x = x[np.isfinite(x[c])]
    k = x.groupby("ticker").apply(lambda y: pd.Series({"Y": (y[c] * y["count"]).sum(), "W": y["count"].sum()}), include_groups=False)
    n = len(k)
    if n < 2 or k.W.sum() <= 0:
        return np.nan
    m = k.Y.sum() / k.W.sum()
    return float(np.sqrt(n / (n - 1) * ((k.Y - m * k.W) ** 2).sum()) / k.W.sum())


def table(d, by, cols, label):
    g = d.groupby(by, observed=True)
    out = pd.DataFrame({"prints": g.size(), "ct": g["count"].sum()})
    for c in cols:
        out[c] = g.apply(lambda x: np.average(x[c], weights=x["count"]) if x["count"].sum() > 0 else np.nan, include_groups=False)
    out["ct_share"] = out.ct / out.ct.sum()
    out["mkts"] = g["ticker"].nunique()
    for c in ["mk5s", "settle"]:
        out[f"{c}_se"] = g.apply(lambda x: cluster_se(x, c), include_groups=False)
    print(f"\n== {label} (contract-weighted maker c/ct) ==")
    print(out.round(3).to_string())


def main():
    b, t = load(sys.argv[1])
    d = per_ticker(b, t)
    d = d[d.ok & d.P.between(1, 99)]
    res = results(sorted(d.ticker.unique()))
    d["result"] = d.ticker.map(res)
    d["settle"] = np.where(d.result == "yes", d.s * (d.P - 100), np.where(d.result == "no", d.s * d.P, np.nan))
    d = d[d.ttc_s > 0]
    t_mid = d.vt.median()
    d["half"] = np.where(d.vt < t_mid, "H1", "H2")
    cols = [f"mk{h}s" for h in HORIZONS_S] + ["settle"]
    print(f"prints {len(d):,}  markets {d.ticker.nunique()}  settled {(d.result != '').mean():.1%} of prints")
    d["band"] = pd.cut(d.mid_lag, [0, 5, 15, 85, 95, 100], labels=["0-5", "5-15", "15-85", "85-95", "95-100"])
    table(d, ["half"], cols, "all prints by half")
    table(d, ["series"], cols, "by series")
    table(d, ["band"], cols, "by mid band at t-LAG")
    mb = d[d.band == "15-85"].copy()
    mb["pos"] = np.select([mb.is_head, mb.same_price_as_head], ["head", "cont@head_px"], "cont_deeper")
    table(mb, ["pos", "half"], cols, "mid-band: burst position (head is actionable only by being already posted)")
    mb["mom"] = pd.cut(mb.mom1s_taker, [-100, -0.25, 0.25, 100], labels=["against", "flat", "with"])
    table(mb, ["mom", "half"], cols, "mid-band: 1s mid momentum in taker's direction, seen at t-LAG")
    mb["flow"] = pd.cut(mb.same_side_1s, [-1, 0, 2, 10, 1e9], labels=["0", "1-2", "3-10", ">10"])
    table(mb, ["flow", "half"], cols, "mid-band: same-side prints in prior 1s, seen at t-LAG")
    mb["imbq"] = pd.qcut(mb.imb_taker, 5, labels=["q1 thick", "q2", "q3", "q4", "q5 thin"], duplicates="drop")
    table(mb, ["imbq", "half"], cols, "mid-band: size on hit side vs other side at t-LAG")
    mb["spr"] = pd.cut(mb.spread_lag, [0, 1, 2, 3, 5, 100], labels=["<=1", "1-2", "2-3", "3-5", ">5"])
    table(mb, ["spr", "half"], cols, "mid-band: spread at t-LAG (c)")
    mb["ttc"] = pd.cut(mb.ttc_s, [0, 60, 180, 420, 900], labels=["<1m", "1-3m", "3-7m", "7-15m"])
    table(mb, ["ttc", "half"], cols, "mid-band: time to close")
    d.to_parquet(sys.argv[1].replace(".csv.gz", ".prints.parquet"))


if __name__ == "__main__":
    main()
