#!/usr/bin/env bash
# Reproduce PACT Fig. 3 on bare metal: run GAPBS bc on a Kronecker graph with
# its memory on one tier while sampling core L2-MLP and CHA TOR-MLP counters
# every INTERVAL ms. See README.md for the metric definitions.
#
# Env (defaults in brackets):
#   MEM        cxl | dram | interleave                       [cxl]
#   CXL_NODE   CXL NUMA node (cpuless, socket 0)              [2]
#   DRAM_NODE  DRAM NUMA node of socket 0                     [0]
#   CPUS       workload CPUs, on socket 0                      [0-7]
#   TRIALS     bc trials (runtime)                             [8]
#   INTERVAL   perf sampling interval in ms                    [100]
#   BC, GRAPH  GAPBS bc binary and .sg graph
set -euo pipefail
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MEM="${MEM:-cxl}"
CXL_NODE="${CXL_NODE:-2}"
DRAM_NODE="${DRAM_NODE:-0}"
CPUS="${CPUS:-0-7}"
TRIALS="${TRIALS:-8}"
INTERVAL="${INTERVAL:-100}"
BC="${BC:-$HOME/demeter-criticality/workload/gapbs/bc}"
GRAPH="${GRAPH:-$HOME/demeter-criticality/bin/kron26.sg}"

case "$MEM" in
cxl) MEMARGS=(--membind="$CXL_NODE") ;;
dram) MEMARGS=(--membind="$DRAM_NODE") ;;
interleave) MEMARGS=(--interleave="$DRAM_NODE,$CXL_NODE") ;;
*) echo "MEM must be cxl, dram or interleave" >&2; exit 1 ;;
esac
THREADS=$(numactl -C "$CPUS" nproc)

OUT="$SELF/runs/$(date +%Y%m%dT%H%M%S)-$MEM"
mkdir -p "$OUT"

# Counters come from counters.py (one perf -e argument per line).
CORE_E=() UNCORE_E=()
while read -r e; do CORE_E+=(-e "$e"); done < <(python3 "$SELF/counters.py" core)
while read -r e; do UNCORE_E+=(-e "$e"); done < <(python3 "$SELF/counters.py" uncore)

cat >"$OUT/meta.txt" <<EOF
mem=$MEM membind=${MEMARGS[*]} cpus=$CPUS threads=$THREADS trials=$TRIALS
interval_ms=$INTERVAL bc=$BC graph=$GRAPH
host=$(hostname) kernel=$(uname -r)
cha_per_socket=$(ls -d /sys/bus/event_source/devices/uncore_cha_* | wc -l)
EOF

export LC_ALL=C
perf stat -x, -I "$INTERVAL" -C "$CPUS" "${CORE_E[@]}" -o "$OUT/core.csv" &
CORE_PID=$!
perf stat -x, -I "$INTERVAL" -a --per-socket "${UNCORE_E[@]}" -o "$OUT/uncore.csv" &
UNCORE_PID=$!
echo "perf_start_epoch=$(date +%s.%N)" >>"$OUT/meta.txt"
sleep 1

echo "[fig3] $MEM: bc on CPUs $CPUS ($THREADS threads), output $OUT"
echo "bc_start_epoch=$(date +%s.%N)" >>"$OUT/meta.txt"
OMP_NUM_THREADS="$THREADS" numactl -C "$CPUS" "${MEMARGS[@]}" \
	"$BC" -f "$GRAPH" -n "$TRIALS" >"$OUT/bc.log" 2>&1
echo "bc_end_epoch=$(date +%s.%N)" >>"$OUT/meta.txt"

sleep 1
kill -INT "$CORE_PID" "$UNCORE_PID"
wait "$CORE_PID" "$UNCORE_PID" 2>/dev/null || true
grep -E "Trial Time|Average Time" "$OUT/bc.log" | tail -3
cp "$SELF/counters.py" "$OUT/"  # what was recorded, for later reference
echo "[fig3] done: python3 $SELF/plot.py $OUT"
