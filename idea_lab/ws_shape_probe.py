#!/usr/bin/env python3
"""One-shot: print the first few frames of each venue's best-bid/ask channel.

Used to pin the exact JSON shapes `fastspot.rs` must parse, instead of guessing them from
docs. Read-only public market data; no credentials, no orders.

Usage: python3 idea_lab/ws_shape_probe.py [ASSET]   (default ETH)
"""
from __future__ import annotations

import asyncio
import json
import sys
import time

import websockets

ASSET = (sys.argv[1] if len(sys.argv) > 1 else "ETH").upper()

# (name, url, subscribe-or-None, frames to show)
SYM = {
    "coinbase": f"{ASSET}-USD",
    "kraken": f"{ASSET}/USD",
    "bitstamp": f"{ASSET.lower()}usd",
    "gemini": f"{ASSET.lower()}usd",
    "binance_us": f"{ASSET.lower()}usd",
    "okx": f"{ASSET}-USDT",
    "crypto_com": f"{ASSET}_USD",
}

VENUES = [
    ("coinbase_adv_t", "wss://advanced-trade-ws.coinbase.com",
     {"type": "subscribe", "product_ids": [SYM["coinbase"]], "channel": "ticker"}),
    ("coinbase_adv_l2", "wss://advanced-trade-ws.coinbase.com",
     {"type": "subscribe", "product_ids": [SYM["coinbase"]], "channel": "level2"}),
    ("coinbase_ex_ticker", "wss://ws-feed.exchange.coinbase.com",
     {"type": "subscribe", "product_ids": [SYM["coinbase"]], "channels": ["ticker"]}),
    ("kraken_bbo", "wss://ws.kraken.com/v2",
     {"method": "subscribe",
      "params": {"channel": "ticker", "symbol": [SYM["kraken"]], "event_trigger": "bbo"}}),
    ("bitstamp_book", "wss://ws.bitstamp.net",
     {"event": "bts:subscribe", "data": {"channel": f"order_book_{SYM['bitstamp']}"}}),
    ("gemini_l2", "wss://api.gemini.com/v2/marketdata",
     {"type": "subscribe", "subscriptions": [{"name": "l2", "symbols": [SYM["gemini"].upper()]}]}),
    ("binance_us_bt", f"wss://stream.binance.us:9443/ws/{SYM['binance_us']}@bookTicker", None),
    ("okx_bbo", "wss://ws.okx.com:8443/ws/v5/public",
     {"op": "subscribe", "args": [{"channel": "bbo-tbt", "instId": SYM["okx"]}]}),
    ("crypto_com_book", "wss://stream.crypto.com/exchange/v1/market",
     {"id": 1, "method": "subscribe",
      "params": {"channels": [f"book.{SYM['crypto_com']}.10"]}, "nonce": int(time.time() * 1000)}),
]


async def one(name: str, url: str, sub: dict | None, n: int = 3) -> None:
    out = [f"=== {name}  {url}"]
    try:
        async with websockets.connect(url, open_timeout=15, close_timeout=2) as ws:
            if sub is not None:
                await ws.send(json.dumps(sub))
            shown = 0
            t0 = time.time()
            while shown < n and time.time() - t0 < 25:
                raw = await asyncio.wait_for(ws.recv(), timeout=25)
                out.append(f"  {raw[:600]}")
                shown += 1
    except Exception as exc:  # noqa: BLE001 - diagnostic script
        out.append(f"  ERR {type(exc).__name__}: {exc}")
    print("\n".join(out), flush=True)


async def main() -> None:
    await asyncio.gather(*(one(n, u, s) for n, u, s in VENUES))


asyncio.run(main())
