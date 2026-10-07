"""The sidecar and the dataset must speak the same language: if serve.text_of ever drifts from
features.serialize, the model sees sentences it was never trained on and the drift is silent.

    .venv/bin/python ml/test_serialize.py ml/data/v1
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import serialize  # noqa: E402
from serve import Snapshot, text_of  # noqa: E402

rows = pd.read_parquet(Path(sys.argv[1] if len(sys.argv) > 1 else "ml/data/v1") / "rows.parquet")
bad = 0
# both kinds must be covered: the exit clause carries three fields the entry clause does not
sample = pd.concat([g.sample(min(250, len(g)), random_state=0) for _, g in rows.groupby("kind")])
for r in sample.itertuples():
    cand = ("improved-ask" if (r.kind == "entry" and r.s > 0) else
            "improved-bid" if r.kind == "entry" else "flatten")
    s = Snapshot(ticker=r.ticker, series=r.series, ttc_s=r.ttc_s, mid_c=r.mid_c, bid_c=r.bid_c,
                 ask_c=r.ask_c, bidsz=int(r.bidsz), asksz=int(r.asksz),
                 spread_ticks=r.spread_ticks, mom1s_c=r.mom1s_c, mom5s_c=r.mom5s_c,
                 vwap60_c=r.vwap60_c, vwap_dev_c=r.vwap_dev_c, ma10_c=r.ma10_c, ma60_c=r.ma60_c,
                 sigma_c=r.sigma_c, tvol60=r.tvol60, flow60=r.flow60, as_resv_c=r.as_resv_c,
                 as_edge_c=r.as_edge_c,
                 position=int(r.pos_before), candidate=cand, candidate_px_c=r.px_c,
                 entry_px_c=r.entry_px_c, unreal_c=r.unreal_c, age_s=r.age_s)
    a, b = serialize(r), text_of(s)
    if a != b:
        bad += 1
        if bad <= 3:
            print(f"MISMATCH\n  dataset: {a}\n  sidecar: {b}")
print(f"{'FAIL' if bad else 'ok'}: {bad} of {len(sample)} sampled rows differ "
      f"({sample.kind.value_counts().to_dict()})")
sys.exit(1 if bad else 0)
