# Reproducing PACT Fig. 3 on traquina (Emerald Rapids + CXL)

PACT (ASPLOS '26) §4.2.2, Figure 3 ("Per-tier MLP", search the PDF for
`Portability across hardware` / `gray line`): for bc-kron, the per-tier MLP
from CHA TOR counters (TOR-MLP) tracks the system-wide L2 MLP over time, and
the Little's-Law fallback `MLP ~= Bandwidth x Latency / 64B` (gray line)
follows the same trends but overestimates. (b) zooms into 20 s to show MLP is
stable at fine time scales.

This directory recreates that figure on bare metal (host, not a VM: the TOR
counters are uncore and not virtualized) and adds what the paper does not
show: the slow tier's busy fraction `u = T2 / cycles`, which is exactly the
factor by which the Little's-Law estimate differs from TOR-MLP
(`occupancy / all cycles = u * occupancy / busy cycles`).

## Metrics, per perf interval (default 100 ms)

Built-in metrics (see `metrics.py` for the full, current list):

| curve | formula | counters |
|---|---|---|
| L2-MLP (red in PACT) | `T1c / T2c` summed over the workload CPUs | `OFFCORE_REQUESTS_OUTSTANDING.DEMAND_DATA_RD` (`cpu/event=0x20,umask=0x1/`) and its `cmask=1` variant (`CYCLES_WITH_DEMAND_DATA_RD`) |
| TOR-MLP (blue in PACT) | `sum T1 / sum T2` over CHAs (as PACT's runtime) | T1 `unc_cha_tor_occupancy.ia_miss_drd_local`, T2 `uncore_cha/event=0x1f,thresh=1/` (counter-0 occupancy >= 1, EMR's equivalent of SKX `COUNTER0_OCCUPANCY`; grouped with T1 so T1 sits on counter 0) |
| Little's Law (gray in PACT) | `(inserts / cycles) x (T1 / inserts) = T1 / cycles` | `unc_cha_tor_inserts.ia_miss_drd_local`, `unc_cha_clockticks` |
| busy fraction u | `sum T2 / sum clockticks` | as above |

Latency `T1/inserts` is in uncore (CHA) cycles and bandwidth `inserts/cycles`
in requests per uncore cycle, so their product is dimensionless (requests in
flight). With demand-read inserts the gray line is algebraically `T1/cycles`;
PACT's version used a memory-side bandwidth counter that also counts
prefetches, hence their overestimate. (traquina's `cxl_pmu_mem*` device
counters read 0 for memory traffic, so TOR inserts are used instead.)

## Platform notes (found while probing, 2026-10-01)

- Host kernel 6.10's CHA `umask` format is `config:8-15,32-55`. The EMR
  CXL-specific TOR filters (e.g. `..._ia_miss_drd_cxl_exp_local`, umask
  `0x20c8168201`) need bits up to 61, so they cannot be expressed (and the
  driver would mask them). The DDR filter (`0xc8178601`) fits. Hence the main
  configuration binds the workload's memory to the CXL node, so the
  socket-local DRD-miss TOR events (`..._drd_local`) are the CXL tier.
- TOR occupancy events are counter-0 only: two occupancy events (T1 and a
  `thresh=1` copy) multiplex at 50% each. `event=0x1f,thresh=1` grouped with
  T1 runs at 100%.
- `sum T1` over CHAs is the true total occupancy, but `sum T2` adds per-CHA
  busy cycles, which overstates global busy time when requests spread over
  the 32 CHAs (PACT also used a per-core TID filter). TOR-MLP here is
  therefore per-CHA concurrency; compare trends with L2-MLP, not levels.
- CXL nodes 2 and 3 are attached to socket 0 (distance 14 vs 24), so the
  workload runs on socket-0 CPUs and uncore counts are taken per socket.

## Adding counters and MLP estimates

Three files, each with one job:

- `counters.py`: what perf records. One `Counter(name, spec, scope,
  group)` line per counter; `run.sh` builds its perf arguments from it, so a
  new counter needs no other change (just a new run). Each run copies the
  file into its directory as a record of what was measured.
- `metrics.py`: how to estimate MLP, and which figures to draw. A new
  estimate is one function:

      @metric("my_mlp", "My MLP estimate", axis="mlp")
      def my_mlp(c, ctx):
          return c["core_t1"] / c["core_cycles"]

  `c` holds this interval's counts by counter name (core: summed over the
  workload CPUs; uncore: socket 0, summed over its CHAs), `ctx` the run
  constants (`interval_s`, `n_cha`, `threads`, `mem`). Missing counters or
  division by zero give NaN, so old runs still work. Figures are entries in
  `FIGURES` listing metric names for the left and right axes.
- `plot.py`: the engine (no metric-specific code). It evaluates every
  registered metric, writes `series.csv`, prints mean/median and the
  correlation with a reference metric (default: PACT's TOR-MLP), and draws
  one image per run, `mlp.png`: every MLP estimate over time, each rescaled
  to the mean of PACT's TOR-MLP (thick black) so shapes can be compared,
  with its scale factor and correlation in the legend. New metrics on the
  `mlp`/`total` axes appear in it automatically.

      python3 plot.py runs/<dir>                      # stats + mlp.png
      python3 plot.py runs/<dir> --figs fig3a,fig3b,fig3c   # PACT's panels
      python3 plot.py runs/<dir> --metrics l2_mlp,my_mlp --no-plots

## Run (on traquina, host)

    ./run.sh                    # bc-kron, memory on CXL node 2, 8 threads
    MEM=dram ./run.sh           # same on DRAM node 0 (reference)
    MEM=interleave ./run.sh     # interleaved over nodes 0 and 2

Outputs go to `runs/<timestamp>-<mem>/` (`core.csv`, `uncore.csv`,
`bc.log`, `meta.txt`, `counters.py`). Then `python3 plot.py runs/<dir>`
(figures need matplotlib: `apt install python3-matplotlib` on the host).
To look at the figures locally, `./fetch.sh` syncs the host's `runs/`
(data, `series.csv`, PNGs) into the local `runs/` (rsync, incremental).

Needs `perf`, `numactl`, GAPBS `bc` and the kron graph (defaults point at
the Demeter fork checkout: `~/demeter-criticality/workload/gapbs/bc` and
`~/demeter-criticality/bin/kron26.sg`). PACT used a scale-27 graph
(~19.5 GB RSS); scale 26 (~10 GB) is used here.
