"""CPU inference sidecar: the Rust engine POSTs a snapshot, this returns an action.

The model only ever proposes. Every hard limit below is deterministic Python the model cannot
influence: size, maximum entry price, per-market and total exposure, a time-to-close floor, a
minimum confidence, and a kill switch file. A proposal that breaks any of them comes back as HOLD
with the reason, so the engine never has to trust the model's arithmetic.

    uvicorn ml.serve:app --host 127.0.0.1 --port 8008

Binds loopback only and holds no venue credentials: it never talks to Kalshi, so a bug here cannot
place an order, and the trading credential stays in the engine. Set GATE_TOKEN to require a shared
token on every request.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent))
# one definition of the label set and of the candidate wording, shared with the dataset builder
from features import EXIT_ACTIONS, LABELS, TASK, candidate_clause  # noqa: E402

BASE = os.environ.get("GATE_BASE", "fastino/gliner2.5-small-v1")
ADAPTER = os.environ.get("GATE_ADAPTER", "")
TOKEN = os.environ.get("GATE_TOKEN", "")
KILL = Path(os.environ.get("GATE_KILL_FILE", "/tmp/gate.kill"))
SHADOW = Path(os.environ.get("GATE_SHADOW_LOG", "ml/runs/shadow.jsonl"))

MAX_SIZE = int(os.environ.get("GATE_MAX_SIZE", "1"))
MAX_ENTRY_C = float(os.environ.get("GATE_MAX_ENTRY_C", "90"))
MIN_ENTRY_C = float(os.environ.get("GATE_MIN_ENTRY_C", "3"))
MAX_POS = int(os.environ.get("GATE_MAX_POS", "10"))            # contracts per market
MAX_TOTAL = int(os.environ.get("GATE_MAX_TOTAL", "50"))        # contracts across markets
MIN_TTC_S = float(os.environ.get("GATE_MIN_TTC_S", "120"))     # no new entries inside this
MIN_CONF = float(os.environ.get("GATE_MIN_CONF", "0.60"))
# Hard brackets. These fire whatever the model says, and they fire even when the model is down:
# the engine should treat a sidecar timeout as STOP_LOSS-if-past-the-stop, HOLD otherwise.
TAKE_PROFIT_C = float(os.environ.get("GATE_TAKE_PROFIT_C", "4"))    # unrealized c per contract
STOP_LOSS_C = float(os.environ.get("GATE_STOP_LOSS_C", "6"))        # unrealized c per contract
MAX_HOLD_S = float(os.environ.get("GATE_MAX_HOLD_S", "600"))        # time stop
SHADOW_ONLY = os.environ.get("GATE_SHADOW_ONLY", "1") == "1"

app = FastAPI(title="kalshi-mm15 gate")
_model = None
_exposure: dict[str, int] = {}


def model():
    global _model
    if _model is None:
        from gliner2 import AutoExtractor
        m = AutoExtractor.from_pretrained(BASE)
        if ADAPTER:
            m.load_adapter(ADAPTER)
        _model = m
    return _model


class Snapshot(BaseModel):
    ticker: str
    series: str
    ttc_s: float
    mid_c: float
    bid_c: float
    ask_c: float
    bidsz: int
    asksz: int
    spread_ticks: float
    mom1s_c: float = 0.0
    mom5s_c: float = 0.0
    # v2 tape context. The engine computes these from the same trailing windows features.py uses
    # (60 s unless named otherwise); defaults make a v1 caller still parse, but a v1 caller then
    # sends sentences the v2 adapter was not trained on -- ml/test_serialize.py is the check.
    vwap60_c: float = 0.0
    vwap_dev_c: float = 0.0
    ma10_c: float = 0.0
    ma60_c: float = 0.0
    sigma_c: float = 0.0
    tvol60: float = 0.0
    flow60: float = 0.0
    as_resv_c: float = 0.0
    as_edge_c: float = 0.0
    position: int = 0
    candidate: Literal["improved-bid", "improved-ask", "flatten"]
    candidate_px_c: float
    total_position: int = 0
    # required on a flatten candidate: the lot being considered
    entry_px_c: Optional[float] = None
    unreal_c: Optional[float] = None
    age_s: Optional[float] = None


class Decision(BaseModel):
    action: str
    size: int
    confidence: Optional[float] = None
    reasons: list[str] = Field(default_factory=list)
    shadow_only: bool = SHADOW_ONLY
    latency_ms: float = 0.0


def text_of(s: Snapshot) -> str:
    """Must stay byte-identical to features.serialize, or the model sees a language it never saw in
    training. ml/test_serialize.py asserts it on real rows; run it after touching either side."""
    imb = s.bidsz / max(s.bidsz + s.asksz, 1)
    kind = "entry" if s.candidate in ("improved-bid", "improved-ask") else "exit"
    cl = candidate_clause(kind, s.position, s.candidate_px_c, s.entry_px_c, s.unreal_c, s.age_s,
                          improved_side=s.candidate)
    return (
        f"series {s.series} | ttc {s.ttc_s:.0f}s | mid {s.mid_c:.2f}c | "
        f"bid {s.bid_c:.2f}c x{s.bidsz} | ask {s.ask_c:.2f}c x{s.asksz} | "
        f"spread {s.spread_ticks:.0f} ticks | imbalance {imb:.2f} | "
        f"mom1s {s.mom1s_c:+.2f}c | mom5s {s.mom5s_c:+.2f}c | "
        f"vwap60 {s.vwap60_c:.2f}c | vwap-dev {s.vwap_dev_c:+.2f}c | "
        f"ma10 {s.ma10_c:.2f}c | ma60 {s.ma60_c:.2f}c | "
        f"sigma {s.sigma_c:.3f}c/sqrt-s | takervol60 {s.tvol60:.0f} | flow60 {s.flow60:+.2f} | "
        f"reservation {s.as_resv_c:.2f}c | quote-vs-reservation {s.as_edge_c:+.2f}c | "
        f"position {s.position:+.0f} | {cl}"
    )


def bracket(s: Snapshot) -> tuple[str, list[str]] | None:
    """Deterministic exit, ahead of the model. Returns None when no bracket is hit."""
    if s.candidate != "flatten" or s.position == 0 or s.unreal_c is None:
        return None
    if s.unreal_c >= TAKE_PROFIT_C:
        return "TAKE_PROFIT", ["bracket_take_profit"]
    if s.unreal_c <= -STOP_LOSS_C:
        return "STOP_LOSS", ["bracket_stop_loss"]
    if s.age_s is not None and s.age_s >= MAX_HOLD_S:
        return ("TAKE_PROFIT" if s.unreal_c > 0 else "STOP_LOSS"), ["bracket_time_stop"]
    return None


def risk_check(s: Snapshot, action: str) -> tuple[str, list[str]]:
    """Deterministic. Only ever downgrades an action to HOLD, except for a bracket, which can
    upgrade a HOLD to an exit: a hard stop must not need the model's agreement."""
    r: list[str] = []
    if KILL.exists():
        return ("STOP_LOSS", ["kill_switch_flatten"]) if s.position else ("HOLD", ["kill_switch"])
    if action in EXIT_ACTIONS:
        if s.position == 0:
            return "HOLD", ["no_position"]
        if s.unreal_c is not None:
            # the model may not mislabel which kind of exit this is; the sign of P&L decides
            action = "TAKE_PROFIT" if s.unreal_c > 0 else "STOP_LOSS"
        return action, r
    if action not in ("BUY_YES", "BUY_NO"):
        return "HOLD", r
    if (action == "BUY_YES") != (s.candidate == "improved-bid"):
        r.append("side_mismatch_with_candidate")
    if s.ttc_s < MIN_TTC_S:
        r.append("too_close_to_settlement")
    if not (MIN_ENTRY_C <= s.candidate_px_c <= MAX_ENTRY_C):
        r.append("entry_price_outside_band")
    if abs(s.position + (1 if action == "BUY_YES" else -1)) > MAX_POS:
        r.append("market_position_cap")
    if abs(s.total_position) + 1 > MAX_TOTAL:
        r.append("total_position_cap")
    if s.bid_c <= 0 or s.ask_c <= 0 or s.ask_c <= s.bid_c:
        r.append("crossed_or_empty_book")
    return ("HOLD" if r else action), r


@app.get("/health")
def health():
    return {"ok": True, "base": BASE, "adapter": ADAPTER or None,
            "shadow_only": SHADOW_ONLY, "killed": KILL.exists(),
            "brackets": {"take_profit_c": TAKE_PROFIT_C, "stop_loss_c": STOP_LOSS_C,
                         "max_hold_s": MAX_HOLD_S},
            "caps": {"size": MAX_SIZE, "pos": MAX_POS, "total": MAX_TOTAL,
                     "entry_band_c": [MIN_ENTRY_C, MAX_ENTRY_C], "min_ttc_s": MIN_TTC_S}}


@app.post("/decide", response_model=Decision)
def decide(s: Snapshot, x_gate_token: str = Header(default="")):
    if TOKEN and x_gate_token != TOKEN:
        raise HTTPException(401, "bad token")
    t0 = time.perf_counter()
    res = model().classify_text(text_of(s), {TASK: LABELS}, include_confidence=True)
    v = res.get(TASK)
    if isinstance(v, list):
        v = v[0] if v else None
    proposed = v.get("label", "HOLD") if isinstance(v, dict) else (v or "HOLD")
    conf = float(v.get("confidence", 0.0)) if isinstance(v, dict) else float("nan")

    reasons: list[str] = []
    if conf == conf and conf < MIN_CONF:
        proposed, reasons = "HOLD", ["low_confidence"]
    br = bracket(s)
    if br is not None:                       # a hard bracket pre-empts the model entirely
        action, more = br
    else:
        action, more = risk_check(s, proposed)
    reasons += more
    d = Decision(action=action, size=MAX_SIZE if action != "HOLD" else 0, confidence=conf,
                 reasons=reasons, latency_ms=(time.perf_counter() - t0) * 1e3)

    SHADOW.parent.mkdir(parents=True, exist_ok=True)
    with SHADOW.open("a") as f:
        f.write(json.dumps({"t": time.time(), "snapshot": s.model_dump(),
                            "proposed": proposed, **d.model_dump()}) + "\n")
    return d
