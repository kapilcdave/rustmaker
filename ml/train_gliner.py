"""LoRA fine-tune GLiNER2.5 on the gate dataset, then score the policy OOS against the controls.

Runs on the GCP training VM (T4 is enough for a 194M encoder with LoRA):

    python3 ml/train_gliner.py --data ml/data/v1 --out ml/runs/v1 --epochs 6

Writes the adapter to <out>/adapter/final, predictions to <out>/test_pred.csv, and the arm table to
<out>/report.txt. The arm table is the deliverable: accuracy is not, because a policy that is right
about cheap rows and wrong about dear ones can be accurate and still lose.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import LABELS, TASK, serialize  # noqa: E402
from policy_eval import (arm_always, arm_bracket, arm_detgate, arm_logit,  # noqa: E402
                         summarize)

BASE = "fastino/gliner2.5-base-v1"      # 194M; -small-v1 (74M) if CPU latency binds at serve time


def predict(model, texts, batch_size=32, threshold=0.5):
    out = model.batch_classify_text(
        texts, {TASK: LABELS}, batch_size=batch_size, threshold=threshold,
        include_confidence=True,
    )
    labels, conf = [], []
    for r in out:
        v = (r or {}).get(TASK)
        if isinstance(v, list):
            v = v[0] if v else None
        if isinstance(v, dict):
            labels.append(v.get("label", "HOLD")); conf.append(float(v.get("confidence", 0.0)))
        elif isinstance(v, str):
            labels.append(v); conf.append(float("nan"))
        else:
            labels.append("HOLD"); conf.append(0.0)       # abstain when the head says nothing
    return np.array(labels), np.array(conf)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="ml/data/v1")
    ap.add_argument("--out", default="ml/runs/v1")
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--epochs", type=int, default=6)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--lora-r", type=int, default=8)
    ap.add_argument("--min-conf", type=float, default=0.0,
                    help="below this confidence the policy abstains (HOLD)")
    ap.add_argument("--skip-train", action="store_true", help="evaluate an existing adapter")
    ap.add_argument("--adapter", default="",
                    help="adapter dir to evaluate with --skip-train; default <out>/adapter/final. "
                         "Name a per-epoch checkpoint here when a run was cut short.")
    a = ap.parse_args()

    data, out = Path(a.data), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    adapter = out / "adapter"

    from gliner2 import AutoExtractor
    model = AutoExtractor.from_pretrained(a.base)

    if not a.skip_train:
        from gliner2.training.trainer import ExtractorTrainer, TrainingConfig
        cfg = TrainingConfig(
            output_dir=str(adapter), experiment_name="kalshi_mm15_gate",
            num_epochs=a.epochs, batch_size=a.batch_size, gradient_accumulation_steps=2,
            encoder_lr=1e-5, task_lr=5e-4,
            use_lora=True, lora_r=a.lora_r, lora_alpha=float(2 * a.lora_r), lora_dropout=0.0,
            lora_target_modules=["encoder"], save_adapter_only=True,
            eval_strategy="epoch", logging_steps=50,
            # fp16 is a CUDA path: the advertised ./ml/gcp.sh train-cpu (and any local smoke run)
            # aborts on a box with no GPU, so key it off the device rather than hardcoding it
            fp16=__import__("torch").cuda.is_available(),
        )
        ExtractorTrainer(model=model, config=cfg).train(
            train_data=str(data / "train.jsonl"), eval_data=str(data / "val.jsonl"),
        )
    else:
        # only when evaluating an existing run: after training the adapter is already attached, and
        # loading it again stacks a second one on the same model
        model.load_adapter(a.adapter or str(adapter / "final"))

    man = json.loads((data / "manifest.json").read_text())
    d = pd.read_parquet(data / "rows.parquet")
    tr = d[d.fold == "train"]
    te = d[d.fold == "test"].reset_index(drop=True)
    texts = [serialize(r) for r in te.itertuples()]
    pred, conf = predict(model, texts)
    pred = np.where(conf >= a.min_conf, pred, "HOLD")
    te.assign(pred=pred, conf=conf).to_csv(out / "test_pred.csv", index=False)

    # room4 is the bar, not `always` and not the logistic arm: measured 2026-10-02, the 14-feature
    # logit IS this one-line rule (paired +3.7 +- 4.1 c/market, t=0.91), and it is deterministic
    # enough to live in penny.py's 11 ms path, which the 56-80 ms sidecar cannot.
    side = np.where(te.s < 0, "BUY_YES", "BUY_NO")
    room4 = np.where((te.kind == "entry") & (te.spread_ticks >= 4), side, "HOLD")
    arms = {"always": arm_always(te), "detgate": arm_detgate(te), "room4": room4,
            "logit": arm_logit(tr, te), "gliner": pred}
    if (te.kind == "exit").any():
        arms["bracket_2_3"] = arm_bracket(te, 2.0, 3.0)
    tbl = summarize(te, arms)
    from policy_eval import per_market_net
    net = {k: per_market_net(te, v) for k, v in arms.items()}
    paired = []
    for b in ("room4", "logit", "detgate"):
        x = (net["gliner"] - net[b]).dropna()
        se = x.std(ddof=1) / np.sqrt(len(x))
        paired.append(f"  gliner - {b:<8} {x.mean():+8.2f} +- {se:5.2f}  t={x.mean() / se:+5.2f}")
    acc = {name: float((p == te.label.to_numpy()).mean()) for name, p in arms.items()}
    report = (
        f"base {a.base}  lora_r {a.lora_r}  epochs {a.epochs}  mark {man['mark']}\n"
        f"adapter {a.adapter or str(adapter / 'final')}\n"
        f"test fold: {len(te):,} rows / {te.ticker.nunique()} markets "
        f"(closes {te.ticker.min()} .. {te.ticker.max()})\n\n"
        f"{tbl.to_string()}\n\npaired differences per market (the bar is room4):\n"
        + "\n".join(paired) + "\n\n"
        f"row accuracy: {json.dumps(acc, indent=2)}\n\n"
        "confusion (gliner):\n"
        f"{pd.crosstab(te.label, pred).to_string()}\n"
    )
    (out / "report.txt").write_text(report)
    print(report)


if __name__ == "__main__":
    main()
