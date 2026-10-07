"""Control arms on a built dataset, no GPU and no model. Run this BEFORE paying for training:
if the deterministic gate and the logistic arm do not separate here, the GLiNER arm has nothing
to beat and the label set is the thing to fix.

    python3 ml/baseline.py ml/data/v1
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from policy_eval import (arm_always, arm_bracket, arm_detgate, arm_logit,  # noqa: E402
                         arm_oracle, summarize)

import json
root = Path(sys.argv[1])
man = json.loads((root / "manifest.json").read_text())
d = pd.read_parquet(root / "rows.parquet")
print(f"mark = {man['mark']}  (labels and the arm table both read {man['mark']} P&L)")
tr, te = d[d.fold == "train"], d[d.fold == "test"].reset_index(drop=True)
print(f"train {len(tr):,} rows / {tr.ticker.nunique()} markets   "
      f"test {len(te):,} rows / {te.ticker.nunique()} markets")
arms = {"always": arm_always(te), "detgate": arm_detgate(te), "logit": arm_logit(tr, te),
        "oracle_entry": np.where(te.kind == "entry", te.label, "HOLD"),
        "oracle_exit": np.where(te.kind == "exit", te.label, arm_always(te)),
        "oracle_both": arm_oracle(te)}
if (te.kind == "exit").any():
    arms["bracket_2_3"] = arm_bracket(te, 2.0, 3.0)
print(summarize(te, arms).to_string())
print("\nlabel mix, test fold:")
print(te.groupby(["kind", "label"]).size().to_string())
