#!/usr/bin/env bash
# ARMED: penny-room 4. REAL ORDERS. Extra flags pass through to `live`.
exec "$(dirname "$0")/penny.sh" 4 "$@"
