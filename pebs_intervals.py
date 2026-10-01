#!/usr/bin/env python3
"""Bin a run's PEBS samples (pebs.data, from run.sh with PEBS=1) into the
perf stat intervals and per memory tier, writing pebs.csv for plot.py.

Two sampled events (see run.sh):
  l3m  MEM_LOAD_RETIRED.L3_MISS        -> slow/fast L3-miss load counts
  lat  MEM_TRANS_RETIRED.LOAD_LATENCY  -> slow/fast load latency (weight)
A sample's tier comes from its physical address and the host's NUMA memory
map saved at run time (nodemap.txt: node start end). Counts are scaled by
the sample period, so l3m_* estimate the number of misses.

Usage: python3 pebs_intervals.py <run_dir>
"""

import bisect
import csv
import subprocess
import sys
from collections import defaultdict
from pathlib import Path


def main():
    run = Path(sys.argv[1])
    meta = dict(
        kv.split("=", 1)
        for line in (run / "meta.txt").read_text().splitlines()
        for kv in line.split()
        if "=" in kv
    )
    interval_s = float(meta["interval_ms"]) / 1000
    t0 = float(meta["mono_start"])  # CLOCK_MONOTONIC at perf stat start
    slow = {int(n) for n in meta["slow_nodes"].split(",")}
    periods = {"l3m": int(meta["pebs_l3m_period"]), "lat": int(meta["pebs_lat_period"])}

    ranges = sorted(
        (int(a, 16), int(b, 16), int(n))
        for n, a, b in (l.split() for l in (run / "nodemap.txt").read_text().splitlines())
    )
    starts = [r[0] for r in ranges]

    def tier(phys):
        i = bisect.bisect_right(starts, phys) - 1
        if i < 0 or phys >= ranges[i][1]:
            return None
        return "slow" if ranges[i][2] in slow else "fast"

    rows = defaultdict(lambda: defaultdict(float))
    out = subprocess.run(
        ["perf", "script", "-i", str(run / "pebs.data"), "-F", "event,time,phys_addr,weight"],
        capture_output=True, text=True, check=True,
    ).stdout
    for line in out.splitlines():
        # perf script -F event,time,phys_addr,weight prints, on traquina's
        # perf: "<time>: <event>: <weight> <phys_addr hex, no 0x>"
        f = line.split()
        if len(f) != 4:
            continue
        ev = {"pebs_l3m:": "l3m", "pebs_lat:": "lat"}.get(f[1])
        if ev is None:
            continue
        try:
            t, weight, phys = float(f[0].rstrip(":")), int(f[2]), int(f[3], 16)
        except ValueError:
            continue
        tr = tier(phys) if phys else None  # phys 0: address not translated
        if tr is None:
            continue
        k = round((t - t0) / interval_s)
        r = rows[k]
        if ev == "l3m":
            r[f"l3m_{tr}"] += periods["l3m"]
        else:
            r[f"lat_{tr}_n"] += 1
            r[f"lat_{tr}_sum"] += weight

    cols = ["l3m_slow", "l3m_fast", "lat_slow_n", "lat_slow_sum", "lat_fast_n", "lat_fast_sum"]
    with (run / "pebs.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["interval", *cols])
        for k in sorted(rows):
            w.writerow([k, *(rows[k].get(c, 0.0) for c in cols)])
    n = sum(r.get("lat_slow_n", 0) + r.get("lat_fast_n", 0) for r in rows.values())
    print(f"pebs.csv: {len(rows)} intervals, {n:.0f} latency samples")


if __name__ == "__main__":
    main()
