#!/usr/bin/env bash
# Mirror the measurement host's runs/ (data, series.csv, figures) into the
# local runs/ directory, to look at the figures locally. Incremental: only new
# or changed files are transferred; files no longer on the host (e.g. images
# from an earlier plot layout) are removed locally.
#
# Usage: ./fetch.sh [host] [remote_dir]   (defaults: traquina ~/pact-fig3)
set -euo pipefail
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${1:-traquina}"
REMOTE="${2:-pact-fig3}"
mkdir -p "$SELF/runs"
rsync -a --delete --info=stats0,progress0 "$HOST:$REMOTE/runs/" "$SELF/runs/"
ls -1 "$SELF/runs"
