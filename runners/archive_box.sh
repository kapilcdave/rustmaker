#!/usr/bin/env bash
# Move FINISHED files off the box: copy each one (rate-limited, so the live engine's link is not
# saturated), sha256-verify against the box, then delete the box copy. A file modified in the last
# IDLE_MIN minutes (default 10) is treated as still being written and never touched, so this is safe
# to run against the live journal dir and the hourly-rotated depth recorder while they run.
# Box paths are relative to /home/admin; copies land at <dest>/<same relative path>.
# Usage: runners/archive_box.sh <box_dir> [name_glob] [dest]
#   runners/archive_box.sh trading/kalshi-mm15/data/live_penny 'live_*.jsonl.gz'
#   runners/archive_box.sh trading/kalshi-mm15-depth/data/depth
set -uo pipefail
dir="${1:?box dir relative to ~}"
glob="${2:-*}"
dest="${3:-$(dirname "$0")/../data/box_archive}"
idle="${IDLE_MIN:-10}"
files=$(ssh -o BatchMode=yes aws "cd ~ && find '$dir' -type f -name '$glob' -mmin +$idle" < /dev/null)
n=0; bytes=0
for f in $files; do
    mkdir -p "$dest/$(dirname "$f")"
    scp -q -l 20000 "aws:/home/admin/$f" "$dest/$f" || { echo "$f copy failed"; continue; }
    l=$(shasum -a 256 "$dest/$f" | cut -d" " -f1)
    r=$(ssh -o BatchMode=yes aws "sha256sum ~/'$f'" < /dev/null | cut -d" " -f1)
    if [ -n "$l" ] && [ "$l" = "$r" ]; then
        ssh -o BatchMode=yes aws "rm ~/'$f'" < /dev/null && n=$((n + 1)) && bytes=$((bytes + $(stat -f %z "$dest/$f")))
    else
        echo "$f MISMATCH, kept on box"
    fi
done
echo "$dir: archived $n files, $((bytes / 1048576)) MB"
ssh -o BatchMode=yes aws 'df -h / | tail -1' < /dev/null
