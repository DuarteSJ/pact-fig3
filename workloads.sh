# Workload command lines for run.sh (sourced). `workload_cmd NAME` sets the
# CMD array. Binaries and datasets are the Demeter fork's host builds in
# $BIN; arguments follow its bench defaults (bench/bench/workload.py), with
# thread counts from $THREADS and bc's trial count from $TRIALS.
#
# Add a workload: a new case here (and it is runnable as WORKLOAD=name).

BIN="${BIN:-$HOME/demeter-criticality/bin}"
WORKLOADS="bc pagerank graph500 xsbench btree gups liblinear bwaves silo"

workload_cmd() {
	case "$1" in
	# PACT's headline workload: GAPBS betweenness centrality, Kronecker
	# scale 26 (bin/kron26.sg, ~10 GB RSS).
	bc) CMD=("$BIN/bc" -f "$BIN/kron26.sg" -n "$TRIALS") ;;
	# GAPBS PageRank on the Twitter graph.
	pagerank) CMD=("$BIN/pr" -l -a -f "$BIN/twitter.sg" -n 5 -i 20) ;;
	# Graph500 BFS (OpenMP CSR), scale 24, edge factor 24.
	graph500) CMD=("$BIN/omp-csr" -V -s 24 -e 24 -n 10) ;;
	# XSBench, history-based Monte Carlo cross-section lookups.
	xsbench) CMD=("$BIN/XSBench" -m history -G unionized -t "$THREADS" -l 34 -g 25000 -p 10000000) ;;
	# B-tree: build 2e8 elements, then 2e9 lookups.
	btree) CMD=("$BIN/bench_btree_mt" -- -n 200000000 -l 2000000000) ;;
	# GUPS, hot set (1 GB, 9x the updates) at the top of a 14 GB array.
	gups) CMD=("$BIN/gups" --thread="$THREADS" --update=800000000 --len=15023996928 --granularity=8
		--report=1000 hotset --hot=1065353216 --weight=9 --reverse) ;;
	# LIBLINEAR training on the kdda dataset.
	liblinear) CMD=("$BIN/train" -m "$THREADS" -s 2 "$BIN/kdda" /dev/null) ;;
	# SPEC CPU 2017 603.bwaves_s.
	bwaves) CMD=("$BIN/bind-stdin" "$BIN/bwaves_s.in" "$BIN/bwaves_s") ;;
	# Silo in-memory database, YCSB.
	silo) CMD=("$BIN/dbtest" --verbose --slow-exit --parallel-loading --bench=ycsb
		--num-threads="$THREADS" --scale-factor=55000 --ops-per-worker=10000000) ;;
	*) echo "unknown workload $1 (known: $WORKLOADS)" >&2; return 1 ;;
	esac
}
