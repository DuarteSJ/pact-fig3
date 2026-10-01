#!/usr/bin/env bash
# Copy runs (data, series.csv, figures) from the measurement host into the
# local runs/ directory, to look at the figures locally. Incremental: only new
# or changed files are transferred, nothing local is deleted.
#
# Usage: ./fetch.sh [host] [remote_dir]   (defaults: traquina ~/pact-fig3)
set -euo pipefail
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${1:-traquina}"
REMOTE="${2:-pact-fig3}"
mkdir -p "$SELF/runs"
rsync -a --info=stats0,progress0 "$HOST:$REMOTE/runs/" "$SELF/runs/"
ls -1 "$SELF/runs"
