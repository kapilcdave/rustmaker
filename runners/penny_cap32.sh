#!/usr/bin/env bash
# ARMED penny-room 4 at the RAISED drawdown budget. REAL ORDERS.
#
# ⚑ This is the operator's risk-budget decision, made 2026-10-08, and it is the whole point of the
# script: `penny_supervisor.sh` deliberately defaults MAX_DD_C to 400c and says "raising the risk
# budget is the operator's call, not a script's". This file IS that call, recorded, so the default
# stays conservative for every other invocation.
#
# WHY 3200c. The cap, not the market, has been destroying the sample. At the measured round
# distribution (ruin.py: 493 rounds, +3.24c mean, 82.57c sd) first-passage survival is:
#
#     cap  400c -> P(week) 38%   |  1600c -> 85%   |  3200c -> 98%
#
# The seat has been DARK since 2026-10-07T16:32Z, when a -576c cumulative drawdown tripped the 400c
# cap and the supervisor exited for good -- the fourth cap-kill in that day's four runs (sessions
# -396.73c, -74.10c, -67.55c, then refused to start). At 3200c a week of continuous quoting
# survives 98% of the time, which is what turns +$1.26/day at t=+0.87 into something establishable
# in ~67 calendar days instead of never.
#
# WHAT YOU ARE RISKING. 3200c of drawdown against an edge of +3.24c/round at t = +0.87 -- POSITIVE
# AND NOT ESTABLISHED. If the edge is real the 67 days pay about $84; if it is noise this spends up
# to $32. That trade is the operator's to make and it is made here explicitly, not implicitly by a
# default.
#
# FUNDING. Preflight needs COLLATERAL_C + MAX_DD_C on the TRADING SHARD (shard 2):
#     900c collateral (9 series x clip 1) + 3200c drawdown = 4100c = $41
# and it will refuse to start below that rather than quote without collateral (at $2.86 the venue
# refused 57% of posts). As of 2026-10-08T22:1xZ shard 2 held 251.78c and shard 3 held 800c, so
# this config needs a deposit of ~$30.50 even after consolidating the shards. The guard is the
# point: an underfunded run is strictly worse than no run.
#
# Usage:  runners/penny_cap32.sh [extra live flags...]
# Env:    MAX_DD_C/SESSION_CAP_C/CLIP/SERIES/ENV_FILE all still override (see penny_supervisor.sh).
set -euo pipefail
cd "$(dirname "$0")/.."

# The binary the last live run used was the amend-path build; keep that unless told otherwise.
export BIN="${BIN:-../kalshi-mm15-penny4/kalshi-mm15-amend}"
export ENV_FILE="${ENV_FILE:-$HOME/.config/kalshi/env}"

# The raised budget. SESSION_CAP_C is left equal to it: the per-run cap is not what was killing
# runs, the CUMULATIVE one was, and a session cap below the seat's own round-sd fires on noise.
export MAX_DD_C="${MAX_DD_C:-3200}"
export SESSION_CAP_C="${SESSION_CAP_C:-$MAX_DD_C}"

# A fifth of 3200c is 640c = 7.7 round-sd, comfortably above the 250c (3 round-sd) barrier, so the
# headroom guard stays meaningful rather than becoming a formality at the larger budget.
export MIN_HEAD_C="${MIN_HEAD_C:-250}"

echo "penny_cap32: MAX_DD_C=${MAX_DD_C}c  SESSION_CAP_C=${SESSION_CAP_C}c  BIN=${BIN}"
echo "             needs collateral + ${MAX_DD_C}c on shard 2; preflight will refuse if short."
exec runners/penny_supervisor.sh "$@"
