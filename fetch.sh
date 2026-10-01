#!/usr/bin/env bash
# Pull the figures (and compare.py's tables) from the measurement host's runs/
# into the local runs/, to look at them locally. The data stays on the host,
# where the figures are made. Incremental: only new or changed files are
# transferred; figures no longer on the host are removed locally.
#
# Usage: ./fetch.sh [host] [remote_dir]   (defaults: traquina ~/pact-fig3)
set -euo pipefail
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${1:-traquina}"
REMOTE="${2:-pact-fig3}"
mkdir -p "$SELF/runs"
rsync -a -m --delete --info=stats0,progress0 \
	--include='*/' --include='*.png' --include='compare-*.txt' --exclude='*' \
	"$HOST:$REMOTE/runs/" "$SELF/runs/"
ls -1 "$SELF/runs"
