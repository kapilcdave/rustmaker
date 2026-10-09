#!/usr/bin/env bash
# ARMED 2-series penny seat: ZEC + NEAR. REAL ORDERS.
#
# WHY TWO SERIES AND NOT NINE (`scale_down.py`, all 511 subsets, 486 rounds -- no subset chosen for
# its P&L). Collateral is the only term linear in series count (100c x k x clip), while co-expiring
# markets share one price path, so round sd grows only as k^0.375 and round mean as k^0.649:
#
#     per-round Sharpe ~ k^0.275   (breadth buys edge QUALITY)
#     rope = cap / round-sd        26.5 at k=1  ->  1.9 at k=9   (breadth costs SURVIVAL)
#
# Measured for THIS pair at the post-loss balance (`score_subset.py 4093 KXZEC15M,KXNEAR15M`):
#
#     424 rounds, 788 markets, 5,720 ct, +$11.86    mean +2.80c/round  sd 58.53c  t +0.98
#     collateral 200c, headroom 3893c = 66.5 round-sd    P(week) 100.0%   P(67d) 99.8%
#     rounds to t=2: 1,752  = 438 quoting hours  (vs 2,600 rounds for the 9-series seat)
#
# So the pair survives AND establishes sooner, because dropping series cuts sd faster than mean.
# The 9-series seat at the same balance has 3893c of rope spread over an 82.57c round sd.
#
# ⚠ THE PAIR CAME FROM A PRIOR, NOT FROM THAT P&L. `zec-near-are-the-widest-15m-books` (penny-jump
# income needs spread room) and `eth-15m-gated-maker-closed-same-as-btc` both predate any subset
# scoring. A quarter of all 511 subsets have a non-positive mean, so choosing on the table above
# would be `in-sample-controls-cannot-detect-selection`. t = +0.98 on 424 rounds is NOT an edge --
# it is a prior worth 1,752 more rounds of test.
#
# ⛔ NOT A LATENCY PLAY. Dropping series can only recover the ~570 us of feed_age that the 9-series
# config carries above the 1-series benchmark, and even recovering ALL of it lands at ~8.5 ms
# reaction, not below 7.5 ms: ~4.5 ms of the feed is venue-internal publish delay and ~2.45 ms of
# the order path is behind the load balancer before auth starts. Neither is a function of series
# count. See FINDINGS_signing_and_transport_20261006.md.
#
# ⚑ MAX_DD_C IS DELIBERATELY NOT DEFAULTED. `penny_cap32.sh` exported `MAX_DD_C=${MAX_DD_C:-3200}`,
# and a later session launched it for an unrelated reason and inherited a 3200c real-money risk
# budget it never chose -- that run lost $18.13. A risk budget is the operator's decision every
# time, so this script refuses to start without one. Size it in ROUND-SD, not dollars:
#
#     cap 1000c = 17.1 round-sd      cap 2000c = 34.2 round-sd      cap 3893c = 66.5 (all headroom)
#
#   Launch:  cd ~/trading/kalshi-mm15 && rm -f STOP && \
#              MAX_DD_C=<cents> ENV_FILE=~/.config/kalshi/env nohup setsid runners/penny_pair.sh \
#              > data/live_penny/pair.log 2>&1 &
#   Stop:    touch ~/trading/kalshi-mm15/STOP
set -uo pipefail
cd "$(dirname "$0")/.."

if [ -z "${MAX_DD_C:-}" ]; then
    echo "REFUSING TO START: MAX_DD_C is not set."
    echo "  This seat's round sd is 58.53c. Pass the drawdown budget explicitly, in cents:"
    echo "    MAX_DD_C=1000 ...   # 17.1 round-sd"
    echo "    MAX_DD_C=2000 ...   # 34.2 round-sd"
    echo "  A risk budget inherited from a script default is how the 10-09 \$18.13 loss happened."
    exit 1
fi

export SERIES="${SERIES:-KXZEC15M,KXNEAR15M}"
# 4, not 2. The entire 424-round fill history above and the 10-08 arming quoted at room 4; the
# 10-09 run that lost $18.13 quoted at room 2, which is OUT of sample for every number here.
export PENNY_ROOM="${PENNY_ROOM:-4}"
export MAX_DD_C
export SESSION_CAP_C="${SESSION_CAP_C:-$MAX_DD_C}"
export BIN="${BIN:-../kalshi-mm15-penny4/kalshi-mm15-amend}"

k=$(awk -F, '{print NF}' <<<"$SERIES")
echo "penny_pair: ${SERIES} (k=${k})  room ${PENNY_ROOM}  MAX_DD_C=${MAX_DD_C}c" \
     "= $(awk -v c="$MAX_DD_C" 'BEGIN{printf "%.1f", c/58.53}') round-sd  BIN=${BIN}"
echo "            needs $((100 * k))c collateral + ${MAX_DD_C}c on shard 2; preflight will refuse if short."
# `bash ...` rather than relying on the exec bit: penny_supervisor.sh is mode 100644 in git, so a
# fresh checkout would fail the way `penny_cap32.sh`'s bare exec does off the box.
exec bash runners/penny_supervisor.sh "$@"
