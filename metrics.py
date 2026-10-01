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
    to the mean of `ref` and drawn over it, plus a scatter against it; for
    checking whether an estimate follows the reference's shape."""

    title: str
    left: list = field(default_factory=list)
    right: list = field(default_factory=list)
    left_label: str = "MLP"
    right_label: str = ""
    zoom_s: float | None = None  # window length; start from plot.py --zoom
    kind: str = "lines"
    ref: str | None = None
    track_axes: tuple = ("mlp", "total")


FIGURES = {
    "fig3a": Figure("(a) Temporal MLP", ["l2_mlp", "tor_mlp"], ["little_total"],
                    right_label="Approximated MLP"),
    "fig3b": Figure("(b) MLP stability", ["l2_mlp", "tor_mlp"], ["little_total"],
                    right_label="Approximated MLP", zoom_s=20),
    "fig3c": Figure("(c) Busy-time vs whole-window MLP", ["l2_mlp", "core_occ"], ["core_u"],
                    left_label="requests in flight per core", right_label="busy fraction"),
    # PACT's own method (TOR-MLP) as reference; every other MLP estimate,
    # rescaled to its mean, over it. New metrics on the mlp/total axes are
    # included automatically.
    "track": Figure("MLP estimates vs PACT TOR-MLP", kind="track", ref="tor_mlp",
                    left_label="MLP (scaled to the reference's mean)"),
}
