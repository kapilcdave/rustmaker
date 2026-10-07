#!/usr/bin/env bash
# Move FINISHED live journals off the box: copy (rate-limited, so the live engine's link is not
# saturated), sha256-verify against the box, then delete the box copy. The newest journal (the
# run in progress) is never touched. Usage: runners/archive_journals.sh [dest_dir]
set -uo pipefail
dest="${1:-$(dirname "$0")/../data/box/live_penny_archive}"
mkdir -p "$dest"
files=$(ssh -o BatchMode=yes aws 'cd ~/trading/kalshi-mm15/data/live_penny && ls -t live_1790*.jsonl.gz | tail -n +2' < /dev/null)
for f in $files; do
    scp -q -l 20000 "aws:/home/admin/trading/kalshi-mm15/data/live_penny/$f" "$dest/$f" || { echo "$f copy failed"; continue; }
    l=$(shasum -a 256 "$dest/$f" | cut -d" " -f1)
    r=$(ssh -o BatchMode=yes aws "sha256sum ~/trading/kalshi-mm15/data/live_penny/$f" < /dev/null | cut -d" " -f1)
    if [ -n "$l" ] && [ "$l" = "$r" ]; then
        ssh -o BatchMode=yes aws "rm ~/trading/kalshi-mm15/data/live_penny/$f" < /dev/null && echo "$f archived"
    else
        echo "$f MISMATCH, kept on box"
    fi
done
ssh -o BatchMode=yes aws 'df -h / | tail -1' < /dev/null
