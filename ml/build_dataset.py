"""Build the GLiNER gate dataset from kalshi-mm15 probe tapes.

    python3 ml/build_dataset.py data/box/tape_*.csv.gz --out ml/data/v1

Writes train/val/test JSONL in GLiNER2 classification format, the same rows as parquet (for the
reproduction arm), and a manifest. The split is CHRONOLOGICAL at a market boundary: every row of a
market lands in exactly one fold, and no fold sees a market that closes after a later fold's first.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tox import close_utc, load  # noqa: E402

from features import LABELS, NUMERIC, TASK, market_rows, serialize  # noqa: E402

CACHE = ROOT / "data" / "settle_cache.json"
REST = "https://external-api.kalshi.com/trade-api/v2"


def results(tickers, cache_path=CACHE):
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    missing = [t for t in tickers if t not in cache]
    for i, t in enumerate(missing):
        try:
            with urllib.request.urlopen(f"{REST}/markets/{t}", timeout=10) as r:
                cache[t] = json.load(r)["market"].get("result", "")
        except Exception as e:  # noqa: BLE001
            print(f"result {t}: {e}", file=sys.stderr)
            cache[t] = ""
        if i % 50 == 0:
            cache_path.write_text(json.dumps(cache))
    cache_path.write_text(json.dumps(cache))
    return {t: 1.0 if cache.get(t) == "yes" else 0.0 if cache.get(t) == "no" else np.nan
            for t in tickers}


def build(tapes, mark):
    frames = []
    for path in tapes:
        b, t = load(path)
        tickers = sorted(set(b.ticker.unique()) & set(t.ticker.unique()))
        res = results(tickers)
        for tk in tickers:
            d = market_rows(b[b.ticker == tk], t[t.ticker == tk], close_utc(tk), res[tk], tk,
                            mark=mark)
            if d is not None and len(d):
                d["tape"] = Path(path).name
                frames.append(d)
        print(f"{Path(path).name}: {len(tickers)} markets", file=sys.stderr)
    if not frames:
        sys.exit("no rows built")
    return pd.concat(frames, ignore_index=True).sort_values("vt").reset_index(drop=True)


def chrono_split(d, train=0.60, val=0.15):
    closes = {tk: close_utc(tk) for tk in d.ticker.unique()}
    order = sorted(closes, key=closes.get)
    n = len(order)
    i, j = int(n * train), int(n * (train + val))
    fold = {tk: ("train" if k < i else "val" if k < j else "test") for k, tk in enumerate(order)}
    d["fold"] = d.ticker.map(fold)
    return d


def to_jsonl(d, path, prompt):
    with open(path, "w") as f:
        for r in d.itertuples():
            f.write(json.dumps({
                "input": serialize(r),
                "output": {"classifications": [{
                    "task": TASK, "labels": LABELS, "true_label": [r.label], "prompt": prompt,
                }]},
            }) + "\n")


PROMPT = ("You are a market maker on a 15-minute Kalshi crypto binary. Given the book snapshot, "
          "choose the action with positive expected net P&L after fees.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tapes", nargs="+")
    ap.add_argument("--out", default="ml/data/v1")
    ap.add_argument("--mark", default="mk5s", choices=["settle", "mk60s", "mk5s"],
                    help="which P&L the entry label is read off, and therefore what the arm table "
                         "must score. Measured on the 117 markets in this tree, the paired "
                         "gate-minus-always SD per market is 61 c on mk5s, 229 c on mk60s and 732 c "
                         "at settlement: the mark moves the tape needed for a t=2 test by 140x, "
                         "because holding lots to settlement adds directional variance the quoting "
                         "decision does not control.")
    ap.add_argument("--exit-per-lot", type=int, default=4,
                    help="exit rows per lot written to the JSONL, evenly spaced over the lot's "
                         "life. The parquet always keeps the full grid, because the policy "
                         "evaluation needs every row to find the first exit a policy calls. "
                         "This ratio is a training-weight choice, not a fact about the market: at "
                         "the full grid exit rows outnumber entries 100:1 and the model becomes an "
                         "exit model that never enters.")
    ap.add_argument("--balance", action="store_true",
                    help="downsample HOLD in the TRAIN fold only, to the next-largest class")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    d = chrono_split(build(a.tapes, a.mark))
    if a.mark != "settle":
        # a cross-out-vs-carry label IS a settlement statement; keeping exit rows under a markout
        # mark would score one thing and train another
        d = d[d.kind == "entry"].reset_index(drop=True)
    d.to_parquet(out / "rows.parquet", index=False)

    manifest = {"rows": len(d), "markets": int(d.ticker.nunique()), "tapes": a.tapes,
                "mark": a.mark, "kinds": d.kind.value_counts().to_dict(),
                "exit_per_lot_in_jsonl": a.exit_per_lot,
                "numeric_features": NUMERIC, "labels": LABELS, "folds": {}}
    for fold, g in d.groupby("fold"):
        tr = g
        if a.exit_per_lot > 0:
            ex = g[g.kind == "exit"]
            keep = ex.groupby("lot_id", sort=False, group_keys=False).apply(
                lambda x: x.iloc[np.linspace(0, len(x) - 1, min(a.exit_per_lot, len(x))).astype(int)]
            ) if len(ex) else ex
            tr = pd.concat([g[g.kind == "entry"], keep]).sort_values("vt")
        if a.balance and fold == "train":
            vc = g.label.value_counts()
            cap = int(vc.drop("HOLD", errors="ignore").max()) if len(vc) > 1 else len(g)
            hold = g[g.label == "HOLD"].sample(min(cap, (g.label == "HOLD").sum()), random_state=0)
            tr = pd.concat([g[g.label != "HOLD"], hold]).sort_values("vt")
        to_jsonl(tr, out / f"{fold}.jsonl", PROMPT)
        manifest["folds"][fold] = {
            "rows_jsonl": len(tr), "rows_parquet": len(g),
            "rows": len(tr), "markets": int(tr.ticker.nunique()),
            "first_close": int(min(close_utc(t) for t in tr.ticker.unique())),
            "last_close": int(max(close_utc(t) for t in tr.ticker.unique())),
            "labels": tr.label.value_counts().to_dict(),
            "net_c_per_label": tr.groupby("label").net_c.mean().round(3).to_dict(),
        }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    print(json.dumps(manifest, indent=2, default=str))


if __name__ == "__main__":
    main()
