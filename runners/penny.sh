#!/usr/bin/env bash
# ARMED penny-jump runner: builds the engine from this checkout and runs `live --armed`
# with --penny-room N (quote one tick inside the others' touch when their spread >= N ticks).
# REAL ORDERS. Usage: runners/penny.sh N [extra live flags...]
# Env overrides: SERIES, MINUTES, ENV_FILE (default .env: KALSHI_API_KEY_ID + KALSHI_PRIVATE_KEY[_PATH]).
set -euo pipefail
cd "$(dirname "$0")/.."

room="${1:?usage: runners/penny.sh N [extra live flags...]}"
shift
series="${SERIES:-KXBTC15M,KXETH15M,KXSOL15M,KXXRP15M,KXDOGE15M,KXHYPE15M,KXBNB15M,KXZEC15M,KXNEAR15M}"
minutes="${MINUTES:-480}"
env_file="${ENV_FILE:-.env}"
[ -f "$env_file" ] || { echo "missing $env_file (KALSHI_API_KEY_ID, KALSHI_PRIVATE_KEY or KALSHI_PRIVATE_KEY_PATH)" >&2; exit 1; }

cargo build --release
mkdir -p data/live_penny
log="data/live_penny/penny${room}_$(date -u +%Y%m%dT%H%M%SZ).log"
echo "penny-room $room, series $series, $minutes min -> $log"

./target/release/kalshi-mm15 live --armed --env-file "$env_file" \
    --penny-room "$room" --series "$series" --minutes "$minutes" \
    --max-pos 1 --stop-before-close-s 450 --open-cutoff-s 450 \
    --amend --spot-bps 2 \
    --out data/live_penny "$@" 2>&1 | tee "$log"
