#!/usr/bin/env python3
"""Combine runs of the same workload/memory config recorded with different
counter profiles (run.sh PROFILE=...) into one comparison: a table of each
estimator's correlation with the figure's reference in every run where it
was recorded, and one image with a panel per estimator (from the first run
that has it) against that run's reference.

Usage: python3 compare.py <run> [<run> ...] [--fig stalls|mlp] [--out PNG]

The PMU fits one core MLP estimator per run alongside the measured stalls,
so e.g. the stall test needs an l2, an l3m and a pebs run; the uncore (TOR)
estimators are recorded in all of them.
"""

import argparse
import math
import statistics
from pathlib import Path

import metrics
from plot import analyze, finite, pearson


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+", type=Path)
    ap.add_argument("--fig", default="stalls", help="a track figure in metrics.FIGURES")
    ap.add_argument("--out", type=Path, help="output image (default: runs/compare-<fig>-<mem>.png)")
    a = ap.parse_args()
    spec = metrics.FIGURES[a.fig]
    assert spec.kind == "track" and spec.ref, f"{a.fig} is not a track figure"

    runs = []
    for run in a.runs:
        ctx, t, _, s, active = analyze(run)
        if finite([s[spec.ref][i] for i in active]):
            runs.append((run, ctx, t, s, active))
        else:
            print(f"skip {run.name}: no {spec.ref} recorded")
    if not runs:
        raise SystemExit("no usable runs")

    names = [n for n, m in metrics.METRICS.items() if m.axis in spec.track_axes and n != spec.ref]
    table = {}  # metric -> {run name: r}
    for run, ctx, t, s, active in runs:
        ref = [s[spec.ref][i] for i in active]
        for n in names:
            v = [s[n][i] for i in active]
            if finite(v):
                table.setdefault(n, {})[run.name] = pearson(ref, v)

    cols = [r[0].name for r in runs]
    print(f"r = Pearson correlation with {spec.ref} ({metrics.METRICS[spec.ref].label}), per run")
    print(f"  {'metric':14s} " + " ".join(f"{c[-24:]:>24s}" for c in cols) + f" {'mean':>7s}  label")
    for n in sorted(table, key=lambda n: -statistics.mean(table[n].values())):
        rs = table[n]
        cells = " ".join(f"{rs[c]:24.3f}" if c in rs else f"{'-':>24s}" for c in cols)
        print(f"  {n:14s} {cells} {statistics.mean(rs.values()):7.3f}  {metrics.METRICS[n].label}")

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not available: no figure")
        return

    panels = [n for n in sorted(table, key=lambda n: -statistics.mean(table[n].values()))]
    fig, axes = plt.subplots(len(panels), 1, squeeze=False, figsize=(9, 1.2 + 2.1 * len(panels)))
    for ax, n in zip(axes[:, 0], panels):
        run, ctx, t, s, active = next(r for r in runs if r[0].name in table[n])
        m = metrics.METRICS[n]
        ref, v = s[spec.ref], s[n]
        k = statistics.mean(finite([ref[i] for i in active])) / statistics.mean(finite([v[i] for i in active]))
        tt = [t[i] for i in active]
        ax.plot(tt, [ref[i] for i in active], color="black", lw=1.4,
                label=f"{metrics.METRICS[spec.ref].label} (reference)")
        ax.plot(tt, [k * v[i] for i in active], m.style, color=m.color or "tab:orange", lw=0.9,
                label=f"{m.label} (x{k:.3g})")
        ax.set_title(f"{n}: r = {table[n][run.name]:.2f}  [{run.name}]", fontsize=9, loc="left")
        ax.set_ylim(bottom=0)
        ax.set_ylabel(spec.left_label, fontsize=8)
        ax.legend(loc="upper right", fontsize=7)
    axes[-1, 0].set_xlabel("Time (s)")
    mem = runs[0][1]["mem"]
    fig.suptitle(f"{spec.title} (bc-kron, {mem}; sorted by mean r)")
    fig.tight_layout()
    out = a.out or a.runs[0].parent / f"compare-{a.fig}-{mem}.png"
    fig.savefig(out, dpi=150)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
