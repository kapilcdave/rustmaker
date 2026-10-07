#!/usr/bin/env bash
# ARMED supervisor for the capped penny engine: REAL ORDERS, relaunched unattended.
# Runs the engine back to back (8 h deadline or $2 session trip), and between runs:
#   1. waits for the current 15-min round to settle (next quarter hour + 120 s), so leftovers
#      are cash, not marks;
#   2. reads the crypto shard's CASH balance from the venue (shard.py), and stops for good if it
#      is below the floor;
#   3. sizes the next session cap as min(SESSION_CAP_C, balance - floor), so one run cannot take the
#      account much below the floor (a leftover can still settle against us after the trip).
# The floor is RELATIVE: shard 2 cash at supervisor start minus MAX_DD_PCT percent of it, and the engine's cumulative
# baseline (live_state.json) is re-based at the same moment, so a deposit or withdrawal made between
# supervisor launches never leaves a stale absolute number behind. A withdrawal DURING a supervisor's
# life reads as a loss and stops it (the safe direction); relaunch to re-base.
# Stops for good on: balance < floor, headroom < a fifth of the drawdown, an engine that exits within 120 s (it
# refused to start or crashed), or a STOP file next to this script's working dir.
#   Launch:  cd ~/trading/kalshi-mm15 && CLIP=2 nohup setsid runners/penny_supervisor.sh > data/live_penny/supervisor.log 2>&1 &
#   Stop:    touch ~/trading/kalshi-mm15/STOP   (the engine running now finishes its run), or
#            kill -INT <engine pid> as well to end it now (clean cancel sweep).
set -uo pipefail
cd "$(dirname "$0")/.."

# Total drawdown allowed, as a percent of the shard-2 cash at supervisor start.
MAX_DD_PCT="${MAX_DD_PCT:-15}"
# Contracts per order. Position cap = CLIP, round net cap = 2 x CLIP (two fills, as replayed at 1 ct),
# venue group limit = 12 x CLIP, session cap = 200c x CLIP (then clipped to the floor headroom).
CLIP="${CLIP:-1}"
BIN="${BIN:-./kalshi-mm15-live-rnet}"
# Opening orders only at YES prices in [OPEN_MIN_C, OPEN_MAX_C); reducing orders quote anywhere.
# Wing fills settled at ~0 c/ct on 60% of capped live fills (2026-09-30). 0/100 = off.
OPEN_MIN_C="${OPEN_MIN_C:-10}"
OPEN_MAX_C="${OPEN_MAX_C:-90}"
# Coinbase spot guard, bps over 1 s; 0 turns it off (commodities: there is no spot feed for them).
SPOT_BPS="${SPOT_BPS:-2}"
# Quote one tick inside only when the spread is at least PENNY_ROOM ticks.
PENNY_ROOM="${PENNY_ROOM:-4}"
# Per-run loss cap in cents per contract of clip (then clipped to the floor headroom).
SESSION_CAP_C="${SESSION_CAP_C:-200}"
SERIES="${SERIES:-KXBTC15M,KXETH15M,KXSOL15M,KXXRP15M,KXDOGE15M,KXHYPE15M,KXBNB15M,KXZEC15M,KXNEAR15M}"
log() { echo "$(date -u +%FT%TZ) $*"; }

shard2_cents() {
    python3 shard.py .env | python3 -c 'import sys,ast; l=sys.stdin.read(); print(int(round(ast.literal_eval(l.split("breakdown $: ")[1].strip())[2]*100)))'
}

wait_for_settlement() {
    local now next
    now=$(date -u +%s)
    next=$(( (now / 900 + 1) * 900 + 120 ))
    log "waiting $((next - now)) s for the round to settle"
    sleep $((next - now))
}

bal0=$(shard2_cents) || { log "balance read failed; exiting"; exit 1; }
MAX_DD_C=$(( bal0 * MAX_DD_PCT / 100 ))
FLOOR_C=$(( bal0 - MAX_DD_C ))
MIN_HEAD_C=$(( MAX_DD_C / 5 ))
[ -f data/live_penny/live_state.json ] && mv data/live_penny/live_state.json "data/live_penny/live_state_$(date -u +%Y%m%dT%H%M%SZ).json"
log "start: shard 2 cash ${bal0}c, max drawdown ${MAX_DD_PCT}% = ${MAX_DD_C}c -> floor ${FLOOR_C}c; engine baseline re-based"

n=0
while true; do
    [ -f STOP ] && { log "STOP file present; exiting"; exit 0; }
    bal=$(shard2_cents) || { log "balance read failed; exiting"; exit 1; }
    head=$(( bal - FLOOR_C ))
    log "shard 2 cash ${bal}c, floor ${FLOOR_C}c, headroom ${head}c"
    [ "$bal" -lt "$FLOOR_C" ] && { log "below floor; exiting"; exit 0; }
    [ "$head" -lt "$MIN_HEAD_C" ] && { log "headroom under ${MIN_HEAD_C}c (a fifth of the drawdown); exiting"; exit 0; }
    want=$(( SESSION_CAP_C * CLIP ))
    cap=$(( head < want ? head : want ))
    n=$((n + 1))
    out="data/live_penny/sup_$(date -u +%Y%m%dT%H%M%SZ).log"
    log "run $n: ${SERIES}, spot ${SPOT_BPS} bps, penny room ${PENNY_ROOM}, clip ${CLIP}, open band ${OPEN_MIN_C}-${OPEN_MAX_C}c, session cap ${cap}c -> $out"
    start=$(date -u +%s)
    "$BIN" live --armed --env-file .env --penny-room "$PENNY_ROOM" --amend --spot-bps "$SPOT_BPS" --max-round-net $((2 * CLIP)) \
        --clip "$CLIP" --max-pos "$CLIP" --group-limit $((12 * CLIP)) \
        --stop-before-close-s 450 --open-cutoff-s 450 --open-min-c "$OPEN_MIN_C" --open-max-c "$OPEN_MAX_C" --series "$SERIES" --minutes 480 \
        --max-loss-c "$cap" --cum-max-loss-c "$MAX_DD_C" --out data/live_penny > "$out" 2>&1 < /dev/null
    rc=$?
    ran=$(( $(date -u +%s) - start ))
    log "run $n exited rc=$rc after ${ran}s: $(grep '^end equity' "$out" | tail -1)"
    [ "$ran" -lt 120 ] && { log "engine exited within 120 s ($(tail -1 "$out")); exiting"; exit 1; }
    wait_for_settlement
done
