"""MLP estimators and figure layouts. To test a new estimate, add a function:

    @metric("my_mlp", "My MLP estimate", axis="mlp")
    def my_mlp(c, ctx):
        return c["core_t1"] / c["core_cycles"]

`c` maps counter names (counters.py) to this interval's counts (core counters
summed over the workload CPUs, uncore counters summed over socket 0's CHAs).
`ctx` has run constants: interval_s, n_cha, threads, mem. Return a number;
a missing counter or a division by zero yields NaN for that interval. A new
counter needs one line in counters.py and a new run.

axis groups metrics on a comparable scale:
  "mlp"     requests in flight per core (or per CHA)
  "total"   requests in flight, system-wide
  "frac"    fractions in [0, 1]
  "other"   anything else (latencies, rates)
  "stall"   stall-cycle predictions S = N / MLP (validated against measured
            CYCLE_ACTIVITY.STALLS_L3_MISS in the "stalls" figure)
  "check"   algebraic cross-checks, left out of tracking plots
"""

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Metric:
    name: str
    label: str
    axis: str
    fn: Callable
    color: str | None = None
    style: str = "-"


METRICS: dict[str, Metric] = {}


def metric(name, label, axis="mlp", color=None, style="-"):
    def register(fn):
        METRICS[name] = Metric(name, label, axis, fn, color, style)
        return fn

    return register


# --- PACT's three curves ---------------------------------------------------


@metric("l2_mlp", "L2MLP: core T1/T2 (busy cycles)", color="tab:red")
def l2_mlp(c, ctx):
    return c["core_t1"] / c["core_t2"]


@metric("tor_mlp", "TOR-MLP: sum T1 / sum T2 over CHAs (PACT)", color="tab:blue")
def tor_mlp(c, ctx):
    # Per-CHA concurrency: sum T2 adds every CHA's busy cycles.
    return c["tor_t1"] / c["tor_t2"]


@metric("little_total", "BW x Latency (TOR): sum T1 / CHA cycles", axis="total", color="gray")
def little_total(c, ctx):
    # (inserts / cycles) x (T1 / inserts): whole-window average in flight.
    return c["tor_t1"] / (c["cha_clk"] / ctx["n_cha"])


# --- Core-side variants ----------------------------------------------------


@metric("core_occ", "core T1 / all cycles (whole window)", color="dimgray", style="--")
def core_occ(c, ctx):
    return c["core_t1"] / c["core_cycles"]


@metric("core_little", "core: requests/cycle x latency", axis="check", color="black", style=":")
def core_little(c, ctx):
    # Little's Law from a request count and an average latency; equals
    # core_occ algebraically (T1/req x req/cycles), kept as a check.
    latency = c["core_t1"] / c["core_req"]
    return (c["core_req"] / c["core_cycles"]) * latency


@metric("l3m_mlp", "L3-miss MLP: core T1/T2 for L3-miss demand reads", color="tab:purple")
def l3m_mlp(c, ctx):
    # Like L2MLP but only requests known to have missed L3 (served by
    # memory), so LLC hits do not count as memory-level parallelism.
    return c["l3m_t1"] / c["l3m_t2"]


# --- Stall model: S = k * N / MLP (PACT Eq. 1) -------------------------------
# N = retired loads that missed L3. Each MLP estimator gives a prediction;
# k is a constant and cancels in correlations and the rescaled plots. The
# reference is the measured stall count. "N alone" is the frequency baseline:
# does dividing by MLP predict stalls better than counting misses?


@metric("stalls", "measured stalls while an L3 miss is pending", axis="stall_ref")
def stalls(c, ctx):
    return c["stalls_l3m"]


@metric("S_freq", "N alone (miss count, no MLP)", axis="stall", color="tab:brown")
def s_freq(c, ctx):
    return c["l3_miss"]


@metric("S_l2", "N / L2MLP", axis="stall", color="tab:red")
def s_l2(c, ctx):
    return c["l3_miss"] / l2_mlp(c, ctx)


@metric("S_l3m", "N / L3-miss MLP", axis="stall", color="tab:purple")
def s_l3m(c, ctx):
    return c["l3_miss"] / l3m_mlp(c, ctx)


@metric("S_tor", "N / TOR-MLP (PACT)", axis="stall", color="tab:blue")
def s_tor(c, ctx):
    return c["l3_miss"] / tor_mlp(c, ctx)


@metric("S_little", "N / BW x Latency (PACT fallback)", axis="stall", color="gray")
def s_little(c, ctx):
    return c["l3_miss"] / little_total(c, ctx)


@metric("S_occ", "N / whole-window core occupancy", axis="stall", color="dimgray", style="--")
def s_occ(c, ctx):
    return c["l3_miss"] / core_occ(c, ctx)


# --- Fractions and diagnostics ---------------------------------------------


@metric("core_u", "core busy fraction T2/cycles", axis="frac", color="tab:green", style="--")
def core_u(c, ctx):
    return c["core_t2"] / c["core_cycles"]


@metric("tor_u", "CHA busy fraction sum T2 / sum cycles", axis="frac", color="tab:olive", style="--")
def tor_u(c, ctx):
    return c["tor_t2"] / c["cha_clk"]


@metric("core_latency", "core demand-read latency (core cycles)", axis="other")
def core_latency(c, ctx):
    return c["core_t1"] / c["core_req"]


@metric("tor_latency", "TOR DRd-miss latency (CHA cycles)", axis="other")
def tor_latency(c, ctx):
    return c["tor_t1"] / c["tor_ins"]


# --- Figures ---------------------------------------------------------------


@dataclass
class Figure:
    """kind "lines": `left`/`right` metrics on two y axes.
    kind "track": every metric on `track_axes` (or `left`, if given) rescaled
    to the mean of `ref` and drawn over it in one plot; for checking whether
    an estimate follows the reference's shape."""

    title: str
    left: list = field(default_factory=list)
    right: list = field(default_factory=list)
    left_label: str = "MLP"
    right_label: str = ""
    zoom_s: float | None = None  # window length; start from plot.py --zoom
    kind: str = "lines"
    ref: str | None = None
    track_axes: tuple = ("mlp", "total")


# Drawn by default: one image per run, every MLP estimate against PACT's own
# method (TOR-MLP), each rescaled to its mean so shapes can be compared. New
# metrics on the mlp/total axes appear automatically.
FIGURES = {
    "mlp": Figure("MLP estimates vs PACT TOR-MLP", kind="track", ref="tor_mlp",
                  left_label="MLP (scaled)"),
    # Which MLP estimate predicts measured stalls best (PACT's Fig. 2 test).
    "stalls": Figure("Stall model N / MLP vs measured L3-miss stalls", kind="track",
                     ref="stalls", track_axes=("stall",), left_label="stall cycles (scaled)"),
}

# PACT Fig. 3 panels and the busy-fraction view; only with plot.py --figs.
EXTRA_FIGURES = {
    "fig3a": Figure("(a) Temporal MLP", ["l2_mlp", "tor_mlp"], ["little_total"],
                    right_label="Approximated MLP"),
    "fig3b": Figure("(b) MLP stability", ["l2_mlp", "tor_mlp"], ["little_total"],
                    right_label="Approximated MLP", zoom_s=20),
    "fig3c": Figure("(c) Busy-time vs whole-window MLP", ["l2_mlp", "core_occ"], ["core_u"],
                    left_label="requests in flight per core", right_label="busy fraction"),
}
