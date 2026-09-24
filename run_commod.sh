#!/usr/bin/env bash
# Box launcher for PREREG_commodities.md. Places NO orders (probe, shadow and rtt are read-only).
# Waits until exchange index 2 (Crypto & Commodities) is trading and a commodity market is open,
# then runs a fresh rtt, a 12 h probe and a 12 h shadow side by side.
#
#   setsid nohup ./run_commod.sh [ENV_FILE] > data/launcher.log 2>&1 < /dev/null &
set -u
cd "$(dirname "$0")"
ENV_ARGS=()
[ -n "${1:-}" ] && ENV_ARGS=(--env-file "$1")
REST=https://external-api.kalshi.com/trade-api/v2
BIN=./target/release/kalshi-mm15
MINUTES=${MINUTES:-720}
mkdir -p data/probe data/shadow

ready() {
  curl -s --max-time 5 "$REST/exchange/status" | python3 -c '
import json, sys
d = json.load(sys.stdin)
sys.exit(0 if any(x["exchange_index"] == 2 and x["trading_active"] for x in d["exchange_index_statuses"]) else 1)' \
  && curl -s --max-time 5 "$REST/markets?series_ticker=KXGOLD15M&status=open&limit=1" | python3 -c '
import json, sys
sys.exit(0 if json.load(sys.stdin)["markets"] else 1)'
}

echo "$(date -u +%FT%TZ) waiting for index 2 trading_active + an open KXGOLD15M market"
until ready; do sleep 20; done
echo "$(date -u +%FT%TZ) trading is live; starting"

"$BIN" rtt --n 200 "${ENV_ARGS[@]}" > data/rtt_commod.json 2> data/rtt_commod.err
nohup "$BIN" probe --out data/probe --minutes "$MINUTES" "${ENV_ARGS[@]}" > data/probe.log 2>&1 &
echo "probe pid $!"
nohup "$BIN" shadow --out data/shadow --minutes "$MINUTES" "${ENV_ARGS[@]}" > data/shadow.log 2>&1 &
echo "shadow pid $!"
echo "$(date -u +%FT%TZ) started; both end after $MINUTES min"
wait
echo "$(date -u +%FT%TZ) both finished"
