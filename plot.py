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
    keys = sorted(set(core) | set(unc))
    t = [k * ctx["interval_s"] for k in keys]
    samples = [{**core.get(k, {}), **unc.get(k, {})} for k in keys]
    return ctx, t, samples


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run", type=Path)
    ap.add_argument("--ref", default="tor_mlp",
                    help="metric to correlate the others with (default: PACT's TOR-MLP)")
    ap.add_argument("--metrics", help="comma-separated subset (default: all)")
    ap.add_argument("--figs", help="comma-separated subset of metrics.FIGURES")
    ap.add_argument("--zoom", type=float, help="start (s) of zoomed figures")
    ap.add_argument("--active", type=float, default=0.05,
                    help="stats only over intervals with core busy fraction above this")
    ap.add_argument("--no-plots", action="store_true")
    a = ap.parse_args()

    ctx, t, samples = load(a.run)
    if not t:
        raise SystemExit(f"no intervals parsed in {a.run}")
    chosen = a.metrics.split(",") if a.metrics else list(metrics.METRICS)
    s = {n: [evaluate(metrics.METRICS[n], c, ctx) for c in samples] for n in chosen}

    with (a.run / "series.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_s", *s])
        w.writerows(zip(t, *s.values()))

    busy = [evaluate(metrics.METRICS["core_u"], c, ctx) for c in samples] \
        if "core_u" in metrics.METRICS else [1.0] * len(t)
    active = [i for i, u in enumerate(busy) if not math.isnan(u) and u > a.active]
    ref = [s[a.ref][i] for i in active] if a.ref in s else []
    print(f"{a.run.name} ({ctx['mem']}): {len(t)} intervals, {len(active)} active "
          f"(core busy > {a.active:.0%}); r = Pearson vs {a.ref}")
    print(f"  {'metric':16s} {'axis':6s} {'mean':>10s} {'median':>10s} {'r':>7s}  label")
    for n, m in ((n, metrics.METRICS[n]) for n in chosen):
        vals = [s[n][i] for i in active]
        fv = finite(vals)
        if not fv:
            print(f"  {n:16s} {m.axis:6s} {'n/a':>10s}  (counters not recorded in this run)")
            continue
        r = pearson(ref, vals) if ref else math.nan
        print(f"  {n:16s} {m.axis:6s} {statistics.mean(fv):10.3f} "
              f"{statistics.median(fv):10.3f} {r:7.3f}  {m.label}")
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
        # mean over active intervals: compares shapes, not levels.
        ref = series(spec.ref)
        names = spec.left or [
            n for n, m in metrics.METRICS.items()
            if m.axis in spec.track_axes and n != spec.ref and finite([series(n)[i] for i in active])
        ]
        idx = [i for i in window(spec) if i in set(active)]
        ref_mean = statistics.mean(finite([ref[i] for i in active]))
        fig, (ax, sc) = plt.subplots(2, 1, figsize=(7, 6.4), gridspec_kw={"height_ratios": [3, 2]})
        tt = [t[i] for i in idx]
        ax.plot(tt, [ref[i] for i in idx], color="black", lw=1.6,
                label=f"{metrics.METRICS[spec.ref].label} (reference)")
        lim = 0.0
        for n in names:
            m = metrics.METRICS[n]
            v = series(n)
            k = ref_mean / statistics.mean(finite([v[i] for i in active]))
            r = pearson([ref[i] for i in active], [v[i] for i in active])
            label = f"{m.label}  x{k:.3g}, r={r:.2f}"
            ax.plot(tt, [k * v[i] for i in idx], m.style, color=m.color, lw=0.9, label=label)
            xs = [ref[i] for i in idx]
            ys = [k * v[i] for i in idx]
            sc.scatter(xs, ys, s=3, alpha=0.4, color=m.color, label=n)
            lim = max(lim, max(finite(xs + ys), default=0))
        sc.plot([0, lim], [0, lim], color="black", lw=0.8, ls="--", label="y = x")
        ax.set_xlabel("Time (s)")
        ax.set_ylabel(spec.left_label)
        ax.set_ylim(bottom=0)
        ax.set_title(f"{spec.title} (bc-kron, {ctx['mem']})")
        ax.legend(loc="upper right", fontsize=6)
        sc.set_xlabel(f"reference: {spec.ref}")
        sc.set_ylabel("estimate (scaled)")
        sc.set_xlim(0, lim)
        sc.set_ylim(0, lim)
        sc.legend(loc="upper left", fontsize=6, markerscale=3)
        return fig

    figs = a.figs.split(",") if a.figs else list(metrics.FIGURES)
    for fname in figs:
        spec = metrics.FIGURES[fname]
        fig = draw_track(fname, spec) if spec.kind == "track" else draw_lines(fname, spec)
        fig.tight_layout()
        fig.savefig(a.run / f"{fname}.png", dpi=150)
        plt.close(fig)
    print(f"  wrote {', '.join(f + '.png' for f in figs)}")


if __name__ == "__main__":
    main()
