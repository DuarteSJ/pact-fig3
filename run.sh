#!/usr/bin/env bash
# Reproduce PACT Fig. 3 on bare metal: run a workload (workloads.sh) with its
# memory on one tier while sampling core L2-MLP and CHA TOR-MLP counters every
# INTERVAL ms (and PEBS samples, per profile). See README.md for the metrics.
#
# Env (defaults in brackets):
#   MEM        cxl | dram | numa | interleave                [cxl]
#              (numa: the other socket's DRAM; PACT's three single-tier
#              configurations are dram, numa and cxl)
#   CXL_NODE   CXL NUMA node (cpuless, socket 0)              [2]
#   DRAM_NODE  DRAM NUMA node of socket 0                     [0]
#   NUMA_NODE  DRAM NUMA node of the other socket             [1]
#   CPUS       workload CPUs, on socket 0                      [0-7]
#   WORKLOAD   workload name from workloads.sh                  [bc]
#   TRIALS     bc trials (runtime)                             [8]
#   INTERVAL   perf sampling interval in ms                    [100]
#   PROFILE    counter profile (counters.py: python3 counters.py
#              profiles). One core MLP estimator per run fits the PMU;
#              "pebs" samples loads (perf record) for the slow-tier
#              bandwidth x latency estimate (pebs.csv)            [l2]
#   PEBS_L3M_PERIOD / PEBS_LAT_PERIOD  sample periods      [2003 / 101]
#   LDLAT      load-latency threshold in cycles                [60]
#   BIN        directory with the workload binaries and datasets
#              (workloads.sh)       [~/demeter-criticality/bin]
set -euo pipefail
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MEM="${MEM:-cxl}"
CXL_NODE="${CXL_NODE:-2}"
DRAM_NODE="${DRAM_NODE:-0}"
NUMA_NODE="${NUMA_NODE:-1}"
CPUS="${CPUS:-0-7}"
TRIALS="${TRIALS:-8}"
INTERVAL="${INTERVAL:-100}"
PROFILE="${PROFILE:-l2}"
PEBS=$(python3 "$SELF/counters.py" pebs "$PROFILE" && echo 1 || echo 0)
PEBS_L3M_PERIOD="${PEBS_L3M_PERIOD:-2003}"
PEBS_LAT_PERIOD="${PEBS_LAT_PERIOD:-101}"
LDLAT="${LDLAT:-60}"
WORKLOAD="${WORKLOAD:-bc}"

# SLOW_NODES: the tier PEBS samples are attributed to ("slow" in pebs.csv):
# the bound node in single-tier runs, the CXL node when interleaved.
case "$MEM" in
cxl) MEMARGS=(--membind="$CXL_NODE") SLOW_NODES="$CXL_NODE" ;;
dram) MEMARGS=(--membind="$DRAM_NODE") SLOW_NODES="$DRAM_NODE" ;;
numa) MEMARGS=(--membind="$NUMA_NODE") SLOW_NODES="$NUMA_NODE" ;;
interleave) MEMARGS=(--interleave="$DRAM_NODE,$CXL_NODE") SLOW_NODES="$CXL_NODE" ;;
*) echo "MEM must be cxl, dram, numa or interleave" >&2; exit 1 ;;
esac
THREADS=$(numactl -C "$CPUS" nproc)
source "$SELF/workloads.sh"
workload_cmd "$WORKLOAD" || exit 1

OUT="$SELF/runs/$(date +%Y%m%dT%H%M%S)-$WORKLOAD-$MEM-$PROFILE"
mkdir -p "$OUT"

# Counters come from counters.py (one perf -e argument per line).
CORE_E=() UNCORE_E=()
while read -r e; do CORE_E+=(-e "$e"); done < <(python3 "$SELF/counters.py" core "$PROFILE")
while read -r e; do UNCORE_E+=(-e "$e"); done < <(python3 "$SELF/counters.py" uncore "$PROFILE")

cat >"$OUT/meta.txt" <<EOF
workload=$WORKLOAD mem=$MEM membind=${MEMARGS[*]} cpus=$CPUS threads=$THREADS trials=$TRIALS
interval_ms=$INTERVAL
cmd=${CMD[*]}
host=$(hostname) kernel=$(uname -r)
cha_per_socket=$(ls -d /sys/bus/event_source/devices/uncore_cha_* | wc -l)
profile=$PROFILE slow_nodes=$SLOW_NODES pebs=$PEBS pebs_l3m_period=$PEBS_L3M_PERIOD pebs_lat_period=$PEBS_LAT_PERIOD ldlat=$LDLAT
EOF

if [ "$PEBS" = 1 ]; then
	# Physical address ranges per NUMA node (node start end, hex), so
	# pebs_intervals.py can tell the tier of each sample.
	BS=$((16#$(cat /sys/devices/system/memory/block_size_bytes)))
	for n in /sys/devices/system/node/node[0-9]*; do
		for m in "$n"/memory[0-9]*; do
			b=${m##*/memory}
			printf "%s %x %x\n" "${n##*/node}" $((b * BS)) $(((b + 1) * BS))
		done
	done >"$OUT/nodemap.txt"
fi

export LC_ALL=C
# The NMI watchdog holds a core counter; free it for the run, restore after.
NMI_WD=$(cat /proc/sys/kernel/nmi_watchdog)
sudo sysctl -q kernel.nmi_watchdog=0
trap 'sudo sysctl -q kernel.nmi_watchdog="$NMI_WD"' EXIT
# CLOCK_MONOTONIC at perf stat start: perf record -k CLOCK_MONOTONIC stamps
# samples on the same clock, so they can be binned into the stat intervals.
echo "mono_start=$(python3 -c 'import time; print(time.clock_gettime(time.CLOCK_MONOTONIC))')" >>"$OUT/meta.txt"
if [ "$PEBS" = 1 ]; then
	# L3-miss loads (all misses: slow-tier request rate) and load latency
	# (per-load latency in the weight field), both with physical addresses.
	# No data-source field, so the load-latency event needs no aux event.
	perf record -q -C "$CPUS" -k CLOCK_MONOTONIC --phys-data -W -o "$OUT/pebs.data" \
		-e "cpu/event=0xd1,umask=0x20,period=$PEBS_L3M_PERIOD,name=pebs_l3m/pp" \
		-e "cpu/event=0xcd,umask=0x1,ldlat=$LDLAT,period=$PEBS_LAT_PERIOD,name=pebs_lat/pp" &
	PEBS_PID=$!
fi
perf stat -x, -I "$INTERVAL" -C "$CPUS" "${CORE_E[@]}" -o "$OUT/core.csv" &
CORE_PID=$!
perf stat -x, -I "$INTERVAL" -a --per-socket "${UNCORE_E[@]}" -o "$OUT/uncore.csv" &
UNCORE_PID=$!
echo "perf_start_epoch=$(date +%s.%N)" >>"$OUT/meta.txt"
sleep 1

echo "[fig3] $WORKLOAD $MEM $PROFILE: CPUs $CPUS ($THREADS threads), output $OUT"
echo "workload_start_epoch=$(date +%s.%N)" >>"$OUT/meta.txt"
RC=0  # set -e must not skip stopping perf if the workload fails
OMP_NUM_THREADS="$THREADS" numactl -C "$CPUS" "${MEMARGS[@]}" \
	"${CMD[@]}" >"$OUT/workload.log" 2>&1 || RC=$?
echo "workload_rc=$RC workload_end_epoch=$(date +%s.%N)" >>"$OUT/meta.txt"

sleep 1
kill -INT "$CORE_PID" "$UNCORE_PID" ${PEBS_PID:-}
wait "$CORE_PID" "$UNCORE_PID" ${PEBS_PID:-} 2>/dev/null || true
if [ "$PEBS" = 1 ]; then
	python3 "$SELF/pebs_intervals.py" "$OUT" && rm -f "$OUT/pebs.data"
fi
tail -3 "$OUT/workload.log"
cp "$SELF/counters.py" "$OUT/"  # what was recorded, for later reference
echo "[fig3] done: python3 $SELF/plot.py $OUT"
