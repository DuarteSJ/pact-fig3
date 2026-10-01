#!/usr/bin/env python3
"""Evaluate every MLP estimate in metrics.py on a run.sh output directory:
writes series.csv, prints per-metric stats and correlation with a reference
metric, and draws the figures declared in metrics.FIGURES.

Usage: python3 plot.py <run_dir> [--ref l2_mlp] [--metrics a,b,..]
                       [--figs fig3a,..] [--zoom START_S] [--no-plots]

Nothing here is metric-specific: add counters in counters.py and estimates
or figures in metrics.py.
"""

import argparse
import csv
import math
import statistics
from collections import defaultdict
from pathlib import Path

import counters
import metrics


def parse(path: Path, names: dict, interval_s: float, socket=None):
    """perf stat -x, -I output -> {interval index: {counter: count}}.

    Rows are keyed by round(time / interval): two perf processes stamp the
    same interval microseconds apart. With --per-socket rows
    (time,S<n>,<ncpus>,count,unit,event,...) only `socket` is kept."""
    rows = defaultdict(dict)
    if not path.exists():
        return rows
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        f = line.split(",")
        idx = next((i for i, x in enumerate(f) if x in names), None)
        if idx is None or idx < 2:
            continue
        if socket is not None and f[1].startswith("S") and f[1] != socket:
            continue
        try:
            rows[round(float(f[0]) / interval_s)][names[f[idx]]] = float(f[idx - 2])
        except ValueError:  # <not counted> / <not supported>
            continue
    return rows


def evaluate(m, c, ctx):
    try:
        v = m.fn(c, ctx)
        return float(v) if v is not None else math.nan
    except (KeyError, ZeroDivisionError, ValueError, TypeError):
        return math.nan


def finite(xs):
    return [x for x in xs if not math.isnan(x)]


def pearson(xs, ys):
    pts = [(x, y) for x, y in zip(xs, ys) if not (math.isnan(x) or math.isnan(y))]
    if len(pts) < 3:
        return math.nan
    try:
        return statistics.correlation([p[0] for p in pts], [p[1] for p in pts])
    except statistics.StatisticsError:  # constant input
        return math.nan


def load(run: Path):
    meta = dict(
        kv.split("=", 1)
        for line in (run / "meta.txt").read_text().splitlines()
        for kv in line.split()
        if "=" in kv
    )
    ctx = {
        "interval_s": float(meta.get("interval_ms", 100)) / 1000,
        "n_cha": int(meta.get("cha_per_socket", 32)),
        "threads": int(meta.get("threads", 1)),
        "mem": meta.get("mem", "?"),
    }
    core = parse(run / "core.csv", counters.name_map("core"), ctx["interval_s"])
    unc = parse(run / "uncore.csv", counters.name_map("uncore"), ctx["interval_s"], socket="S0")
    # PEBS samples binned by pebs_intervals.py (run.sh PEBS=1): columns
    # become counters prefixed "pebs_" (pebs_l3m_slow, pebs_lat_slow_sum, ...).
    pebs = defaultdict(dict)
    if (run / "pebs.csv").exists():
        with (run / "pebs.csv").open() as f:
            for row in csv.DictReader(f):
                k = int(row.pop("interval"))
                pebs[k] = {f"pebs_{c}": float(v) for c, v in row.items()}
    keys = sorted(set(core) | set(unc))
    t = [k * ctx["interval_s"] for k in keys]
    samples = [{**core.get(k, {}), **unc.get(k, {}), **pebs.get(k, {})} for k in keys]
    return ctx, t, samples


def active_intervals(samples, ctx, threshold):
    """Intervals where the workload is doing memory work: core busy fraction
    (l2 profile) above `threshold`, else any interval with L3 misses."""
    busy = [evaluate(metrics.METRICS["core_u"], c, ctx) for c in samples]
    if finite(busy):
        return [i for i, u in enumerate(busy) if not math.isnan(u) and u > threshold]
    return [i for i, c in enumerate(samples) if c.get("l3_miss", 0) > 0]


def analyze(run: Path, names=None, threshold=0.05):
    """Load a run and evaluate metrics: (ctx, t, samples, series, active)."""
    ctx, t, samples = load(run)
    names = names or list(metrics.METRICS)
    s = {n: [evaluate(metrics.METRICS[n], c, ctx) for c in samples] for n in names}
    return ctx, t, samples, s, active_intervals(samples, ctx, threshold)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run", type=Path)
    ap.add_argument("--ref", default="tor_mlp",
                    help="metric to correlate the others with (default: PACT's TOR-MLP)")
    ap.add_argument("--metrics", help="comma-separated subset (default: all)")
    ap.add_argument("--figs", help="figures to draw (default: metrics.FIGURES; "
                    "also metrics.EXTRA_FIGURES, e.g. fig3a,fig3b,fig3c)")
    ap.add_argument("--zoom", type=float, help="start (s) of zoomed figures")
    ap.add_argument("--active", type=float, default=0.05,
                    help="stats only over intervals with core busy fraction above this")
    ap.add_argument("--no-plots", action="store_true")
    a = ap.parse_args()

    chosen = a.metrics.split(",") if a.metrics else list(metrics.METRICS)
    ctx, t, samples, s, active = analyze(a.run, chosen, a.active)
    if not t:
        raise SystemExit(f"no intervals parsed in {a.run}")

    with (a.run / "series.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_s", *s])
        w.writerows(zip(t, *s.values()))

    # Correlate every metric with --ref and with each figure's reference.
    refs = list(dict.fromkeys([a.ref] + [f.ref for f in metrics.FIGURES.values() if f.ref]))
    refs = [r for r in refs if r in s and finite([s[r][i] for i in active])]
    ref_vals = {r: [s[r][i] for i in active] for r in refs}
    print(f"{a.run.name} ({ctx['mem']}): {len(t)} intervals, {len(active)} active; "
          f"r(x) = Pearson correlation with x")
    head = "".join(f" {'r(' + r + ')':>13s}" for r in refs)
    print(f"  {'metric':14s} {'axis':9s} {'mean':>12s} {'median':>12s}{head}  label")
    for n, m in ((n, metrics.METRICS[n]) for n in chosen):
        vals = [s[n][i] for i in active]
        fv = finite(vals)
        if not fv:
            print(f"  {n:14s} {m.axis:9s} {'n/a':>12s}  (counters not recorded in this run)")
            continue
        rs = "".join(f" {pearson(ref_vals[r], vals):13.3f}" for r in refs)
        print(f"  {n:14s} {m.axis:9s} {statistics.mean(fv):12.4g} "
              f"{statistics.median(fv):12.4g}{rs}  {m.label}")
    print(f"  wrote {a.run / 'series.csv'}")

    if a.no_plots:
        return
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("  matplotlib not available: no figures")
        return

    def series(n):
        if n not in s:
            s[n] = [evaluate(metrics.METRICS[n], c, ctx) for c in samples]
        return s[n]

    def window(spec):
        lo, hi = t[0], t[-1]
        if spec.zoom_s:
            lo = a.zoom if a.zoom is not None else t[len(t) // 2] - spec.zoom_s / 2
            hi = lo + spec.zoom_s
        return [i for i, x in enumerate(t) if lo <= x <= hi]

    def draw_lines(fname, spec):
        idx = window(spec)
        tt = [t[i] for i in idx]
        fig, ax = plt.subplots(figsize=(7, 3.2))
        axes = [(ax, spec.left, spec.left_label)]
        if spec.right:
            axes.append((ax.twinx(), spec.right, spec.right_label))
        handles = []
        for axis, names, ylabel in axes:
            for n in names:
                m = metrics.METRICS[n]
                (h,) = axis.plot(tt, [series(n)[i] for i in idx], m.style, color=m.color, lw=1, label=m.label)
                handles.append(h)
            axis.set_ylabel(ylabel)
            axis.set_ylim(bottom=0)
        ax.set_xlabel("Time (s)")
        ax.set_title(f"{spec.title} (bc-kron, {ctx['mem']})")
        ax.legend(handles, [h.get_label() for h in handles], loc="upper right", fontsize=7)
        return fig

    def draw_track(fname, spec):
        # Reference plus every other estimate rescaled to the reference's
        # mean over active intervals, in one plot: compares shapes, not levels.
        ref = series(spec.ref)
        names = spec.left or [
            n for n, m in metrics.METRICS.items()
            if m.axis in spec.track_axes and n != spec.ref and finite([series(n)[i] for i in active])
        ]
        act = set(active)
        idx = [i for i in window(spec) if i in act]
        tt = [t[i] for i in idx]
        ref_mean = statistics.mean(finite([ref[i] for i in active]))
        # One panel per estimate (reference + that estimate), stacked with a
        # shared time axis, so each comparison is readable on its own.
        ref_label = f"{metrics.METRICS[spec.ref].label} (reference)"
        fig, axes = plt.subplots(len(names), 1, sharex=True, squeeze=False,
                                 figsize=(9, 1.2 + 2.1 * len(names)))
        for ax, n in zip(axes[:, 0], names):
            m = metrics.METRICS[n]
            v = series(n)
            k = ref_mean / statistics.mean(finite([v[i] for i in active]))
            r = pearson([ref[i] for i in active], [v[i] for i in active])
            ax.plot(tt, [ref[i] for i in idx], color="black", lw=1.4, label=ref_label)
            ax.plot(tt, [k * v[i] for i in idx], m.style, color=m.color or "tab:orange",
                    lw=0.9, label=f"{m.label} (x{k:.3g})")
            ax.set_title(f"{n}: r = {r:.2f}", fontsize=9, loc="left")
            ax.set_ylim(bottom=0)
            ax.set_ylabel(spec.left_label, fontsize=8)
            ax.legend(loc="upper right", fontsize=7)
        axes[-1, 0].set_xlabel("Time (s)")
        fig.suptitle(f"{spec.title} (bc-kron, {ctx['mem']})")
        return fig

    all_figs = {**metrics.FIGURES, **metrics.EXTRA_FIGURES}
    figs = a.figs.split(",") if a.figs else list(metrics.FIGURES)
    if not a.figs:
        # Default output is metrics.FIGURES only: drop images from earlier
        # layouts or --figs runs so the run directory shows just these.
        for png in a.run.glob("*.png"):
            if png.stem not in metrics.FIGURES:
                png.unlink()
    for fname in figs:
        spec = all_figs[fname]
        fig = draw_track(fname, spec) if spec.kind == "track" else draw_lines(fname, spec)
        fig.tight_layout()
        fig.savefig(a.run / f"{fname}.png", dpi=150)
        plt.close(fig)
    print(f"  wrote {', '.join(f + '.png' for f in figs)}")


if __name__ == "__main__":
    main()
