"""Causal event-level maker screen over a kalshi-mm15 shadow tape.

This script never places orders. It compares the frozen GBM/book controls with
continuous and finite-state reward models trained only on earlier close windows.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


WINDOWS_US = np.array([10_000, 50_000, 250_000, 1_000_000], dtype=np.int64)
LATENCIES_US = [5_440, 10_000, 20_000, 50_000]
KEEP_FRACTIONS = [0.50, 0.75, 1.00]
RIDGE_LAMBDA = 20.0
MARKOV_LAMBDA = 100.0


def close_us(ticker: str) -> int:
    stamp = ticker.split("-")[1]
    value = datetime.strptime(stamp, "%y%b%d%H%M")
    return int(value.replace(tzinfo=ZoneInfo("America/New_York")).timestamp() * 1e6)


def numeric(frame: pd.DataFrame, names: str) -> pd.DataFrame:
    for name in names:
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    return frame


def last_at(times: np.ndarray, query: np.ndarray) -> np.ndarray:
    return np.searchsorted(times, query, side="right") - 1


def window_count(times: np.ndarray, query: np.ndarray, width: int) -> np.ndarray:
    right = np.searchsorted(times, query, side="right")
    left = np.searchsorted(times, query - width, side="right")
    return right - left


def cumulative_window(times: np.ndarray, cumulative: np.ndarray, query: np.ndarray, width: int) -> np.ndarray:
    right = np.searchsorted(times, query, side="right")
    left = np.searchsorted(times, query - width, side="right")
    return cumulative[right] - cumulative[left]


def make_spot_state(x: pd.DataFrame) -> dict[str, np.ndarray]:
    x = numeric(x.sort_values("recv_us").drop_duplicates("recv_us", keep="last").copy(), "a")
    times = x.recv_us.to_numpy(np.int64)
    prices = x.a.to_numpy(float)
    grid = np.arange(times[0] + 1_000_000, times[-1], 1_000_000, dtype=np.int64)
    index = last_at(times, grid)
    grid_price = prices[np.maximum(index, 0)]
    fresh = (index >= 0) & (grid - times[np.maximum(index, 0)] <= 2_000_000)
    returns2 = np.r_[np.nan, np.diff(np.log(grid_price)) ** 2]
    rv = pd.Series(returns2).rolling(60, min_periods=60).mean().to_numpy()
    vol_ok = pd.Series(fresh.astype(np.int8)).rolling(61, min_periods=61).min().to_numpy() == 1
    return {"times": times, "prices": prices, "grid": grid, "rv": rv, "vol_ok": vol_ok}


def trade_state(t: pd.DataFrame, side: str) -> tuple[np.ndarray, np.ndarray]:
    values = t[t.a == side].sort_values("recv_us")
    times = values.recv_us.to_numpy(np.int64)
    volume = pd.to_numeric(values.c, errors="coerce").fillna(0).to_numpy(float)
    return times, np.r_[0.0, np.cumsum(volume)]


def infer_results(books: pd.DataFrame) -> dict[str, float]:
    ordered = books.sort_values("recv_us")
    last = ordered.groupby("ticker", sort=False).tail(1).copy()
    last["mid"] = (last.a + last.c) / 200.0
    result = {}
    for row in last.itertuples():
        if row.mid <= 5.0:
            result[row.ticker] = 0.0
        elif row.mid >= 95.0:
            result[row.ticker] = 1.0
    return result


def extract_features(frame: pd.DataFrame, latency_us: int) -> pd.DataFrame:
    books = numeric(frame[frame.kind == "B"].copy(), "abcd")
    books = books[(books.a > 0) & (books.c > books.a) & (books.c < 10_000)]
    trades = frame[frame.kind == "T"].copy()
    fills = frame[(frame.kind == "F") & (frame.a == "base")].copy()
    fills = numeric(fills, "cd")
    results = infer_results(books)
    fills = fills[fills.ticker.isin(results)]

    book_groups = {key: value for key, value in books.groupby("ticker", sort=False)}
    trade_groups = {key: value for key, value in trades.groupby("ticker", sort=False)}
    spot_groups = {
        key: make_spot_state(value)
        for key, value in frame[frame.kind == "X"].groupby("ticker", sort=False)
        if len(value) > 60
    }
    rows = []
    for ticker, fill_group in fills.groupby("ticker", sort=False):
        book = book_groups.get(ticker)
        if book is None or len(book) < 10:
            continue
        series = ticker.split("-")[0]
        spot = spot_groups.get(series)
        if spot is None:
            continue
        trade = trade_groups.get(ticker, trades.iloc[0:0])
        yes_t, yes_v = trade_state(trade, "yes")
        no_t, no_v = trade_state(trade, "no")

        book = book.sort_values("recv_us")
        bt = book.recv_us.to_numpy(np.int64)
        bid = book.a.to_numpy(float) / 100.0
        bid_size = book.b.to_numpy(float) / 100.0
        ask = book.c.to_numpy(float) / 100.0
        ask_size = book.d.to_numpy(float) / 100.0
        mid = (bid + ask) / 2.0

        fill_group = fill_group.sort_values(["venue_ms", "recv_us"])
        venue = fill_group.venue_ms.to_numpy(np.int64)
        cutoff = venue - latency_us
        index = last_at(bt, cutoff)
        valid = index >= 0
        index = np.maximum(index, 0)
        maker_side = np.where(fill_group.b.to_numpy() == "ask", 1.0, -1.0)
        quote = np.where(maker_side > 0, ask[index], bid[index])
        hit_size = np.where(maker_side > 0, ask_size[index], bid_size[index])
        opposite_size = np.where(maker_side > 0, bid_size[index], ask_size[index])
        total_size = np.maximum(hit_size + opposite_size, 1e-9)

        values: dict[str, np.ndarray] = {
            "ticker": fill_group.ticker.to_numpy(),
            "series": np.repeat(series, len(fill_group)),
            "close_us": np.repeat(close_us(ticker), len(fill_group)),
            "recv_us": fill_group.recv_us.to_numpy(np.int64),
            "venue_us": venue,
            "side": maker_side,
            "price": fill_group.c.to_numpy(float) / 100.0,
            "count": fill_group.d.to_numpy(float),
            "spread": ask[index] - bid[index],
            "mid": mid[index],
            "log_hit_depth": np.log1p(hit_size),
            "log_opposite_depth": np.log1p(opposite_size),
            "imbalance_toxic": maker_side * (bid_size[index] - ask_size[index]) / total_size,
            "ttc_s": (close_us(ticker) - cutoff) / 1e6,
        }

        for window in WINDOWS_US:
            label = str(window // 1_000)
            past = last_at(bt, cutoff - window)
            valid &= past >= 0
            past = np.maximum(past, 0)
            values[f"mom_{label}ms"] = maker_side * (mid[index] - mid[past])
            past_hit = np.where(maker_side > 0, ask_size[past], bid_size[past])
            same_touch = np.where(maker_side > 0, ask[index] == ask[past], bid[index] == bid[past])
            values[f"depletion_{label}ms"] = np.where(
                same_touch, np.log((past_hit + 1.0) / (hit_size + 1.0)), 0.0
            )
            values[f"book_updates_{label}ms"] = window_count(bt, cutoff, int(window)).astype(float)

            same_t = yes_t if maker_side[0] > 0 else no_t
            same_v = yes_v if maker_side[0] > 0 else no_v
            opposite_t = no_t if maker_side[0] > 0 else yes_t
            opposite_v = no_v if maker_side[0] > 0 else yes_v
            if np.any(maker_side != maker_side[0]):
                same_n = np.where(
                    maker_side > 0,
                    window_count(yes_t, cutoff, int(window)),
                    window_count(no_t, cutoff, int(window)),
                )
                opposite_n = np.where(
                    maker_side > 0,
                    window_count(no_t, cutoff, int(window)),
                    window_count(yes_t, cutoff, int(window)),
                )
                same_volume = np.where(
                    maker_side > 0,
                    cumulative_window(yes_t, yes_v, cutoff, int(window)),
                    cumulative_window(no_t, no_v, cutoff, int(window)),
                )
                opposite_volume = np.where(
                    maker_side > 0,
                    cumulative_window(no_t, no_v, cutoff, int(window)),
                    cumulative_window(yes_t, yes_v, cutoff, int(window)),
                )
            else:
                same_n = window_count(same_t, cutoff, int(window))
                opposite_n = window_count(opposite_t, cutoff, int(window))
                same_volume = cumulative_window(same_t, same_v, cutoff, int(window))
                opposite_volume = cumulative_window(opposite_t, opposite_v, cutoff, int(window))
            values[f"same_trades_{label}ms"] = same_n.astype(float)
            values[f"opposite_trades_{label}ms"] = opposite_n.astype(float)
            values[f"log_same_volume_{label}ms"] = np.log1p(same_volume)
            values[f"flow_imbalance_{label}ms"] = (
                (same_volume - opposite_volume) / np.maximum(same_volume + opposite_volume, 1.0)
            )

            sx = last_at(spot["times"], cutoff)
            sp = last_at(spot["times"], cutoff - window)
            spot_valid = (sx >= 0) & (sp >= 0)
            sx = np.maximum(sx, 0)
            sp = np.maximum(sp, 0)
            spot_valid &= (cutoff - spot["times"][sx] <= 2_000_000)
            spot_valid &= (cutoff - window - spot["times"][sp] <= 2_000_000)
            values[f"spot_{label}ms_bps"] = np.where(
                spot_valid,
                maker_side * np.log(spot["prices"][sx] / spot["prices"][sp]) * 10_000.0,
                np.nan,
            )

        anchor_index = last_at(bt, cutoff - 1_000_000)
        anchor_index = np.maximum(anchor_index, 0)
        anchor = np.clip(mid[anchor_index] / 100.0, 0.001, 0.999)
        spot_now = last_at(spot["times"], cutoff)
        spot_then = last_at(spot["times"], cutoff - 1_000_000)
        grid_index = last_at(spot["grid"], cutoff)
        gbm_valid = (spot_now >= 0) & (spot_then >= 0) & (grid_index >= 0)
        spot_now = np.maximum(spot_now, 0)
        spot_then = np.maximum(spot_then, 0)
        grid_index = np.maximum(grid_index, 0)
        gbm_valid &= spot["vol_ok"][grid_index] & np.isfinite(spot["rv"][grid_index])
        variance = np.maximum(spot["rv"][grid_index] * np.maximum(values["ttc_s"], 1.0), 1e-12)
        z = np.log(anchor / (1.0 - anchor)) + 1.6 * np.log(
            spot["prices"][spot_now] / spot["prices"][spot_then]
        ) / np.sqrt(variance)
        fair = 100.0 / (1.0 + np.exp(-np.clip(z, -30.0, 30.0)))
        values["book_edge"] = maker_side * (quote - 100.0 * anchor)
        values["gbm_edge"] = np.where(gbm_valid, maker_side * (quote - fair), np.nan)
        valid &= (values["ttc_s"] > 120.0) & (values["mid"] >= 15.0) & (values["mid"] <= 85.0)

        for horizon in (1, 5):
            target = fill_group.recv_us.to_numpy(np.int64) + horizon * 1_000_000
            future = last_at(bt, target)
            future_valid = (future >= 0) & (target <= bt[-1])
            future = np.maximum(future, 0)
            future_valid &= bt[future] > fill_group.recv_us.to_numpy(np.int64)
            values[f"mk{horizon}"] = maker_side * (values["price"] - mid[future])
            if horizon == 5:
                valid &= future_valid
        settlement = results[ticker] * 100.0
        values["settle"] = maker_side * (values["price"] - settlement)
        market_frame = pd.DataFrame(values)
        rows.append(market_frame[valid])
    if not rows:
        raise ValueError("no eligible fills")
    return pd.concat(rows, ignore_index=True)


CONTINUOUS_FEATURES = [
    "spread", "mid", "log_hit_depth", "log_opposite_depth", "imbalance_toxic", "ttc_s",
    "book_edge", "gbm_edge",
] + [
    f"{prefix}_{window}ms{suffix}"
    for window in (10, 50, 250, 1000)
    for prefix, suffix in (
        ("mom", ""), ("depletion", ""), ("book_updates", ""),
        ("same_trades", ""), ("opposite_trades", ""),
        ("log_same_volume", ""), ("flow_imbalance", ""), ("spot", "_bps"),
    )
]


@dataclass
class RidgeModel:
    names: list[str]
    mean: np.ndarray
    scale: np.ndarray
    beta: np.ndarray

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        matrix = frame[self.names].to_numpy(float)
        matrix = np.nan_to_num((matrix - self.mean) / self.scale)
        design = np.c_[np.ones(len(matrix)), matrix]
        return design @ self.beta


def fit_ridge(frame: pd.DataFrame, target: str, penalty: float) -> RidgeModel:
    asset = pd.get_dummies(frame.series, prefix="asset", dtype=float)
    work = pd.concat([frame[CONTINUOUS_FEATURES].reset_index(drop=True), asset.reset_index(drop=True)], axis=1)
    names = list(work.columns)
    matrix = work.to_numpy(float)
    finite = np.isfinite(matrix)
    count = finite.sum(axis=0)
    mean = np.divide(np.nansum(matrix, axis=0), count, out=np.zeros(matrix.shape[1]), where=count > 0)
    centered = np.where(finite, matrix - mean, 0.0)
    scale = np.sqrt(np.divide(np.square(centered).sum(axis=0), count, out=np.ones(matrix.shape[1]), where=count > 0))
    scale[~np.isfinite(scale) | (scale < 1e-9)] = 1.0
    matrix = np.nan_to_num((matrix - mean) / scale)
    design = np.c_[np.ones(len(matrix)), matrix]
    weight = np.sqrt(frame["count"].to_numpy(float))
    weighted = design * weight[:, None]
    target_values = frame[target].to_numpy(float) * weight
    regularizer = np.eye(design.shape[1]) * penalty
    regularizer[0, 0] = 0.0
    beta = np.linalg.solve(weighted.T @ weighted + regularizer, weighted.T @ target_values)
    return RidgeModel(names, mean, scale, beta)


def add_asset_columns(train: pd.DataFrame, other: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    assets = sorted(set(train.series))
    train_work = train.copy()
    other_work = other.copy()
    for asset in assets:
        name = f"asset_{asset}"
        train_work[name] = (train_work.series == asset).astype(float)
        other_work[name] = (other_work.series == asset).astype(float)
    return train_work, other_work


def markov_design(frame: pd.DataFrame) -> pd.DataFrame:
    specifications = {
        "spread_state": ("spread", [-np.inf, 1.0, 2.0, 4.0, np.inf]),
        "imbalance_state": ("imbalance_toxic", [-np.inf, -0.35, -0.10, 0.10, 0.35, np.inf]),
        "momentum_state": ("mom_50ms", [-np.inf, -0.50, -0.05, 0.05, 0.50, np.inf]),
        "flow_state": ("flow_imbalance_250ms", [-np.inf, -0.50, -0.10, 0.10, 0.50, np.inf]),
        "queue_state": ("depletion_50ms", [-np.inf, -0.50, -0.10, 0.10, 0.50, np.inf]),
        "spot_state": ("spot_250ms_bps", [-np.inf, -2.0, -0.50, 0.50, 2.0, np.inf]),
        "time_state": ("ttc_s", [-np.inf, 180.0, 420.0, 660.0, np.inf]),
    }
    states = pd.DataFrame(index=frame.index)
    for name, (column, bins) in specifications.items():
        states[name] = pd.cut(frame[column], bins=bins, labels=False, include_lowest=True).fillna(-1).astype(int)
    for left, right in (("imbalance_state", "momentum_state"), ("flow_state", "queue_state"), ("spot_state", "momentum_state")):
        states[f"{left}_x_{right}"] = states[left].astype(str) + ":" + states[right].astype(str)
    states["asset"] = frame.series.to_numpy()
    return pd.get_dummies(states.astype(str), prefix=states.columns, dtype=float)


@dataclass
class MatrixRidge:
    columns: list[str]
    beta: np.ndarray

    def predict(self, matrix: pd.DataFrame) -> np.ndarray:
        aligned = matrix.reindex(columns=self.columns, fill_value=0.0).to_numpy(float)
        return np.c_[np.ones(len(aligned)), aligned] @ self.beta


def fit_matrix_ridge(matrix: pd.DataFrame, frame: pd.DataFrame, penalty: float) -> MatrixRidge:
    design = np.c_[np.ones(len(matrix)), matrix.to_numpy(float)]
    weight = np.sqrt(frame["count"].to_numpy(float))
    weighted = design * weight[:, None]
    target = frame.mk5.to_numpy(float) * weight
    regularizer = np.eye(design.shape[1]) * penalty
    regularizer[0, 0] = 0.0
    beta = np.linalg.solve(weighted.T @ weighted + regularizer, weighted.T @ target)
    return MatrixRidge(list(matrix.columns), beta)


def weighted_mean_se(frame: pd.DataFrame, column: str) -> tuple[float, float]:
    if frame.empty:
        return math.nan, math.nan
    grouped = frame.assign(y=frame[column] * frame["count"]).groupby("ticker", sort=False).agg(
        y=("y", "sum"), weight=("count", "sum")
    )
    total_weight = grouped.weight.sum()
    mean = grouped.y.sum() / total_weight
    if len(grouped) < 2:
        return float(mean), math.nan
    se = math.sqrt(len(grouped) / (len(grouped) - 1) * np.square(grouped.y - mean * grouped.weight).sum()) / total_weight
    return float(mean), float(se)


def policy_metrics(universe: pd.DataFrame, mask: np.ndarray) -> dict[str, float | int | bool]:
    selected = universe[mask]
    mk1, mk1_se = weighted_mean_se(selected, "mk1")
    mk5, mk5_se = weighted_mean_se(selected, "mk5")
    settle, settle_se = weighted_mean_se(selected, "settle")
    pnl_by_market = selected.assign(pnl=selected.settle * selected["count"]).groupby("ticker").pnl.sum()
    all_markets = pd.Index(universe.ticker.unique())
    pnl_by_market = pnl_by_market.reindex(all_markets, fill_value=0.0)
    c_per_market = float(pnl_by_market.mean())
    c_per_market_se = float(pnl_by_market.std(ddof=1) / math.sqrt(len(pnl_by_market))) if len(pnl_by_market) > 1 else math.nan
    contracts = float(selected["count"].sum())
    return {
        "fills": int(len(selected)), "contracts": contracts, "markets": int(selected.ticker.nunique()),
        "keep_share": float(contracts / universe["count"].sum()),
        "mk1_c_per_ct": mk1, "mk1_se": mk1_se, "mk5_c_per_ct": mk5, "mk5_se": mk5_se,
        "settle_c_per_ct": settle, "settle_se": settle_se,
        "settle_c_per_market": c_per_market, "settle_c_per_market_se": c_per_market_se,
        "settle_total_dollars": float((selected.settle * selected["count"]).sum() / 100.0),
    }


def choose_fraction(validation: pd.DataFrame, prediction: np.ndarray) -> tuple[float, float, list[dict]]:
    candidates = []
    for fraction in KEEP_FRACTIONS:
        threshold = -math.inf if fraction >= 1.0 else float(np.quantile(prediction, 1.0 - fraction))
        mask = prediction >= threshold
        metrics = policy_metrics(validation, mask)
        candidates.append({"fraction": fraction, "threshold": threshold, **metrics})
    best = max(candidates, key=lambda row: (row["mk5_c_per_ct"], row["fraction"]))
    return float(best["fraction"]), float(best["threshold"]), candidates


def split_frame(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    windows = np.sort(frame.close_us.unique())
    train_end = max(1, len(windows) // 2)
    validation_end = max(train_end + 1, train_end + len(windows) // 4)
    train_windows = set(windows[:train_end])
    validation_windows = set(windows[train_end:validation_end])
    test_windows = set(windows[validation_end:])
    return (
        frame[frame.close_us.isin(train_windows)].copy(),
        frame[frame.close_us.isin(validation_windows)].copy(),
        frame[frame.close_us.isin(test_windows)].copy(),
        {"windows": len(windows), "train": len(train_windows), "validation": len(validation_windows), "test": len(test_windows)},
    )


def evaluate_latency(frame: pd.DataFrame, latency_us: int) -> dict:
    train, validation, test, split = split_frame(frame)
    ridge_train, ridge_validation = add_asset_columns(train, validation)
    _, ridge_test = add_asset_columns(train, test)
    ridge_model = fit_ridge(ridge_train, "mk5", RIDGE_LAMBDA)
    ridge_validation_prediction = ridge_model.predict(ridge_validation)
    ridge_test_prediction = ridge_model.predict(ridge_test)

    markov_train = markov_design(train)
    markov_validation = markov_design(validation)
    markov_test = markov_design(test)
    markov_model = fit_matrix_ridge(markov_train, train, MARKOV_LAMBDA)
    markov_validation_prediction = markov_model.predict(markov_validation)
    markov_test_prediction = markov_model.predict(markov_test)

    policies: dict[str, np.ndarray] = {
        "all": np.ones(len(test), dtype=bool),
        "book_edge_ge_0": test.book_edge.to_numpy() >= 0.0,
        "gbm_edge_ge_0": np.isfinite(test.gbm_edge.to_numpy()) & (test.gbm_edge.to_numpy() >= 0.0),
    }
    tuning = {}
    for name, validation_prediction, test_prediction in (
        ("ridge", ridge_validation_prediction, ridge_test_prediction),
        ("markov", markov_validation_prediction, markov_test_prediction),
    ):
        fraction, threshold, candidates = choose_fraction(validation, validation_prediction)
        policies[name] = test_prediction >= threshold
        tuning[name] = {"fraction": fraction, "threshold": threshold, "candidates": candidates}

    metrics = {name: policy_metrics(test, mask) for name, mask in policies.items()}
    by_asset = {
        name: {
            asset: policy_metrics(test[test.series == asset], mask[test.series.to_numpy() == asset])
            for asset in sorted(test.series.unique())
        }
        for name, mask in policies.items()
    }
    for name in ("ridge", "markov"):
        row = metrics[name]
        row["primary_pass"] = bool(
            row["keep_share"] >= 0.50 and row["markets"] >= 50
            and row["mk5_c_per_ct"] - 2.0 * row["mk5_se"] > 0.0
            and row["settle_c_per_market"] - 2.0 * row["settle_c_per_market_se"] > 0.0
        )
    return {
        "latency_us": latency_us, "split": split,
        "train_fills": len(train), "validation_fills": len(validation), "test_fills": len(test),
        "tuning": tuning, "test": metrics, "test_by_asset": by_asset,
    }


def self_test() -> None:
    times = np.array([10, 20, 30], dtype=np.int64)
    query = np.array([9, 10, 19, 30], dtype=np.int64)
    assert last_at(times, query).tolist() == [-1, 0, 0, 2]
    assert window_count(times, np.array([30]), 15).tolist() == [2]
    cumulative = np.array([0.0, 1.0, 3.0, 6.0])
    assert cumulative_window(times, cumulative, np.array([30]), 15).tolist() == [5.0]
    print("self-test passed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("tape", nargs="?", default="data/box/shadow_spot/tape.csv.gz")
    parser.add_argument("--out", default="data/event-markov-20260927.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    frame = pd.read_csv(
        args.tape,
        dtype={"kind": "category", "ticker": "string", "a": "string", "b": "string", "c": "string", "d": "string"},
        low_memory=False,
    )
    report = {
        "prereg": "PREREG_event_markov_20260927.md",
        "tape": args.tape,
        "latencies": [],
    }
    for latency_us in LATENCIES_US:
        features = extract_features(frame, latency_us)
        print(f"latency={latency_us / 1000:.2f}ms eligible={len(features):,}", flush=True)
        latency_report = evaluate_latency(features, latency_us)
        latency_report["asset_walkforward"] = {}
        for asset in sorted(features.series.unique()):
            asset_features = features[features.series == asset]
            if asset_features.close_us.nunique() < 8:
                continue
            asset_report = evaluate_latency(asset_features, latency_us)
            latency_report["asset_walkforward"][asset] = {
                key: asset_report[key]
                for key in ("split", "train_fills", "validation_fills", "test_fills", "tuning", "test")
            }
        report["latencies"].append(latency_report)
    report["prospective_shadow_pass"] = any(
        all(
            next(row for row in report["latencies"] if row["latency_us"] == latency)["test"][model]["primary_pass"]
            for latency in (5_440, 10_000)
        )
        for model in ("ridge", "markov")
    )
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, allow_nan=True) + "\n")
    for latency in report["latencies"]:
        print(f"\n== {latency['latency_us'] / 1000:.2f} ms test ==")
        for name, row in latency["test"].items():
            print(
                f"{name:16} keep={row['keep_share']:.1%} mk5={row['mk5_c_per_ct']:+.4f}±{row['mk5_se']:.4f} "
                f"settle={row['settle_c_per_ct']:+.4f}±{row['settle_se']:.4f} "
                f"c/mkt={row['settle_c_per_market']:+.3f}±{row['settle_c_per_market_se']:.3f}"
            )
    print(f"\nwrote {output}")


if __name__ == "__main__":
    main()
