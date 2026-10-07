#!/usr/bin/env python3
"""Read-only capture of exact CF Benchmarks values and Kalshi 15m books.

This process has no order-entry code.  It subscribes to:

* ``cfbenchmarks_value`` for exact one-hertz values and the progressively
  written quarter-hour settlement average;
* ``cfbenchmarks_value_5hz`` for the higher-frequency path where available;
* ``orderbook_delta``, ``ticker`` and ``trade`` for matching 15-minute markets.

Every venue frame is written verbatim with a local receipt timestamp.  Market
metadata discovered over public GET endpoints is written into the same ledger,
so the capture is replayable without another network request.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import gzip
import json
import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import requests
import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


REST = "https://external-api.kalshi.com/trade-api/v2"
WS = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"

ASSET_TO_SERIES = {
    "ETH": "KXETH15M",
    "SOL": "KXSOL15M",
    "XRP": "KXXRP15M",
    "DOGE": "KXDOGE15M",
    "BNB": "KXBNB15M",
    "HYPE": "KXHYPE15M",
    "NEAR": "KXNEAR15M",
    "ZEC": "KXZEC15M",
}
ASSET_TO_INDEX = {
    "ETH": "ETHUSD_RTI",
    "SOL": "SOLUSD_RTI",
    "XRP": "XRPUSD_RTI",
    "DOGE": "DOGEUSD_RTI",
    "BNB": "BNBUSD_RTI",
    "HYPE": "HYPEUSD_RTI",
    "NEAR": "NEARUSD_RTI",
    "ZEC": "ZECUSD_RTI",
}
FIVE_HZ = {"ETHUSD_RTI", "SOLUSD_RTI", "XRPUSD_RTI", "DOGEUSD_RTI"}


def now_us() -> int:
    return time.time_ns() // 1_000


def parse_time_ms(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def _expand(value: str) -> str:
    return os.path.expandvars(os.path.expanduser(value.strip().strip("\"'")))


def read_env(path: Path) -> Dict[str, str]:
    """Read the repository's simple dotenv format, including multiline PEMs."""
    out: Dict[str, str] = {}
    lines = path.read_text().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip("\"'")
        if key == "KALSHI_PRIVATE_KEY" and "-----BEGIN" in value and "-----END" not in value:
            parts = [value]
            while i < len(lines):
                parts.append(lines[i].strip().strip("\"'"))
                if "-----END" in lines[i]:
                    i += 1
                    break
                i += 1
            value = "\n".join(parts)
        out[key] = value.replace("\\n", "\n")
    return out


@dataclass
class Auth:
    key_id: str
    private_key: Any

    @classmethod
    def load(cls, env_file: Optional[Path]) -> "Auth":
        values = dict(os.environ)
        if env_file is not None:
            values.update(read_env(env_file))
        key_id = values.get("KALSHI_API_KEY_ID") or values.get("KALSHI_API_KEY")
        if not key_id:
            raise RuntimeError("missing KALSHI_API_KEY_ID")
        pem = values.get("KALSHI_PRIVATE_KEY")
        if not pem:
            key_path = values.get("KALSHI_PRIVATE_KEY_PATH")
            if not key_path:
                raise RuntimeError("missing KALSHI_PRIVATE_KEY_PATH or KALSHI_PRIVATE_KEY")
            pem = Path(_expand(key_path)).read_text()
        key = serialization.load_pem_private_key(pem.encode(), password=None)
        return cls(key_id=key_id, private_key=key)

    def headers(self, method: str, path: str) -> Dict[str, str]:
        timestamp = str(int(time.time() * 1000))
        clean = path.split("?", 1)[0]
        message = f"{timestamp}{method.upper()}{clean}".encode()
        signature = self.private_key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
        }


class Ledger:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.handle = gzip.open(path, "at", encoding="utf-8", compresslevel=1)
        self.rows = 0
        self.last_flush = time.monotonic()

    def write(self, kind: str, **payload: Any) -> None:
        row = {"local_recv_us": now_us(), "kind": kind, **payload}
        self.handle.write(json.dumps(row, separators=(",", ":"), sort_keys=False) + "\n")
        self.rows += 1
        if self.rows % 1000 == 0 or time.monotonic() - self.last_flush >= 5:
            self.handle.flush()
            self.last_flush = time.monotonic()

    def close(self) -> None:
        self.handle.flush()
        self.handle.close()


def get_json(url: str, params: Dict[str, Any]) -> Dict[str, Any]:
    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()
    return response.json()


def discover_once(series: Iterable[str], horizon_ms: int = 120_000) -> Dict[str, Dict[str, Any]]:
    """Return active and imminently opening markets, keyed by ticker."""
    now = int(time.time() * 1000)
    found: Dict[str, Dict[str, Any]] = {}
    for series_ticker in series:
        for status, limit in (("open", 20), ("unopened", 100)):
            try:
                body = get_json(
                    f"{REST}/markets",
                    {"series_ticker": series_ticker, "status": status, "limit": limit},
                )
            except Exception as exc:
                print(f"discover {series_ticker} {status}: {exc}", file=sys.stderr)
                continue
            for market in body.get("markets", []):
                try:
                    open_ms = parse_time_ms(market["open_time"])
                    close_ms = parse_time_ms(market["close_time"])
                except (KeyError, TypeError, ValueError):
                    continue
                if close_ms < now - 30_000:
                    continue
                if open_ms > now + horizon_ms:
                    continue
                found[market["ticker"]] = market
    return found


def subscribe_cf(command_id: int, indices: list[str]) -> str:
    return json.dumps(
        {
            "id": command_id,
            "cmd": "subscribe",
            "params": {
                "channels": ["cfbenchmarks_value", "cfbenchmarks_value_5hz"],
                "index_ids": indices,
            },
        }
    )


def subscribe_markets(command_id: int, tickers: list[str]) -> str:
    return json.dumps(
        {
            "id": command_id,
            "cmd": "subscribe",
            "params": {
                "channels": ["orderbook_delta", "ticker", "trade"],
                "market_tickers": tickers,
                "use_yes_price": True,
            },
        }
    )


async def run(args: argparse.Namespace) -> None:
    assets = [a.upper() for a in args.assets]
    unknown = sorted(set(assets) - ASSET_TO_SERIES.keys())
    if unknown:
        raise SystemExit(f"unknown assets: {','.join(unknown)}")
    series = [ASSET_TO_SERIES[a] for a in assets]
    indices = [ASSET_TO_INDEX[a] for a in assets]
    auth = Auth.load(args.env_file)
    ledger = Ledger(args.output)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    deadline = time.monotonic() + args.minutes * 60
    known: Dict[str, Dict[str, Any]] = {}
    seqs: Dict[int, int] = {}
    connects = 0
    ledger.write(
        "start",
        assets=assets,
        series=series,
        indices=indices,
        five_hz_indices=sorted(set(indices) & FIVE_HZ),
        minutes=args.minutes,
        ws=WS,
        rest=REST,
    )

    try:
        while not stop.is_set() and time.monotonic() < deadline:
            headers = auth.headers("GET", "/trade-api/ws/v2")
            try:
                async with websockets.connect(
                    WS,
                    additional_headers=headers,
                    ping_interval=None,
                    close_timeout=3,
                    max_queue=200_000,
                    max_size=4 * 1024 * 1024,
                ) as ws:
                    connects += 1
                    ledger.write("connect", connects=connects)
                    command_id = 1
                    await ws.send(subscribe_cf(command_id, indices))
                    command_id += 1

                    discovered = await asyncio.to_thread(discover_once, series)
                    for ticker, market in discovered.items():
                        if ticker not in known:
                            known[ticker] = market
                            ledger.write("market", market=market)
                    if discovered:
                        await ws.send(subscribe_markets(command_id, sorted(discovered)))
                        command_id += 1

                    next_discovery = time.monotonic() + args.discovery_seconds
                    next_status = time.monotonic() + 60
                    while not stop.is_set() and time.monotonic() < deadline:
                        timeout = max(0.05, min(next_discovery - time.monotonic(), 2.0))
                        try:
                            text = await asyncio.wait_for(ws.recv(), timeout=timeout)
                        except asyncio.TimeoutError:
                            text = None
                        if text is not None:
                            if isinstance(text, bytes):
                                text = text.decode("utf-8", errors="replace")
                            local = now_us()
                            try:
                                frame = json.loads(text)
                            except json.JSONDecodeError:
                                ledger.write("bad_json", raw=text)
                                continue
                            sid, seq = frame.get("sid"), frame.get("seq")
                            if isinstance(sid, int) and isinstance(seq, int):
                                previous = seqs.get(sid)
                                if previous is not None and seq != previous + 1:
                                    ledger.write("seq_gap", sid=sid, previous=previous, seq=seq)
                                seqs[sid] = seq
                            ledger.write("ws", frame=frame, frame_local_recv_us=local)

                        if time.monotonic() >= next_discovery:
                            discovered = await asyncio.to_thread(discover_once, series)
                            fresh = []
                            for ticker, market in discovered.items():
                                if ticker not in known:
                                    known[ticker] = market
                                    fresh.append(ticker)
                                    ledger.write("market", market=market)
                            if fresh:
                                await ws.send(subscribe_markets(command_id, sorted(fresh)))
                                ledger.write("subscribe_markets", tickers=sorted(fresh))
                                command_id += 1
                            next_discovery = time.monotonic() + args.discovery_seconds

                        if time.monotonic() >= next_status:
                            print(
                                f"rows={ledger.rows} markets={len(known)} connects={connects} "
                                f"remaining_min={(deadline-time.monotonic())/60:.1f}",
                                file=sys.stderr,
                                flush=True,
                            )
                            ledger.write(
                                "status",
                                rows=ledger.rows,
                                markets=len(known),
                                connects=connects,
                                sequence_streams=len(seqs),
                            )
                            next_status = time.monotonic() + 60
            except Exception as exc:
                ledger.write("disconnect", error=f"{type(exc).__name__}: {exc}")
                print(f"websocket: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
                await asyncio.sleep(1)
    finally:
        ledger.write("stop", rows=ledger.rows, markets=len(known), connects=connects)
        ledger.close()
        print(f"wrote {ledger.path} ({ledger.rows} rows)", file=sys.stderr)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--assets",
        nargs="+",
        default=["ETH", "SOL", "XRP", "DOGE", "BNB", "HYPE", "NEAR", "ZEC"],
    )
    p.add_argument("--minutes", type=float, default=60)
    p.add_argument("--discovery-seconds", type=float, default=10)
    p.add_argument(
        "--env-file",
        type=Path,
        default=Path.home() / ".config" / "weather-trading" / "env",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path("data/cf_exact") / f"capture_{int(time.time() * 1000)}.jsonl.gz",
    )
    return p


if __name__ == "__main__":
    asyncio.run(run(parser().parse_args()))
