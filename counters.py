#!/usr/bin/env python3
"""Perf counters recorded by run.sh. Add a counter here and it is recorded on
the next run and available to metrics (by `name`) in metrics.py.

  name     stable name, used as the perf `name=` term and in metrics
  spec     perf event without the name term, e.g. "cpu/event=0x20,umask=0x1/"
  scope    "core"  : counted on the workload CPUs (perf -C), summed over them
           "uncore": counted per socket (perf -a --per-socket), socket 0 used
  group    counters with the same group are scheduled together (perf {...});
           needed when one counter reads another's counter (TOR T1/T2)
  aliases  older names of the same counter, so earlier runs still parse
  profile  None = always recorded; otherwise only in runs with that PROFILE
           (run.sh). Core events like OFFCORE_REQUESTS_OUTSTANDING fit only
           a few general-purpose counters: measured on traquina, one T1/T2
           pair + stalls + N run at 100%, two pairs multiplex. So each core
           MLP estimator gets its own profile and is validated in its own run
           (compare.py combines runs).

`python3 counters.py core|uncore [profile]` prints the perf -e arguments, one
per line; `python3 counters.py profiles` lists the profiles.

With run.sh PEBS=1 there are also sampled counters, binned per interval by
pebs_intervals.py into pebs.csv and available to metrics as
pebs_l3m_{slow,fast} (estimated L3-miss loads per tier) and
pebs_lat_{slow,fast}_{n,sum} (load-latency samples and summed latency).
"""

import sys
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Counter:
    name: str
    spec: str
    scope: str
    group: str | None = None
    aliases: tuple = field(default_factory=tuple)
    profile: str | None = None

    def perf(self) -> str:
        assert self.spec.endswith("/"), self.spec
        return f"{self.spec[:-1]},name={self.name}/"


COUNTERS = [
    # Core, per workload CPU (summed): L2-miss demand data reads.
    # OFFCORE_REQUESTS_OUTSTANDING.DEMAND_DATA_RD: per-cycle sum of pending.
    # T1/T2 pairs are grouped so both are always counted over the same time.
    Counter("core_t1", "cpu/event=0x20,umask=0x1/", "core", "l2", profile="l2"),
    # ...CYCLES_WITH_DEMAND_DATA_RD (cmask=1): cycles with >= 1 pending.
    Counter("core_t2", "cpu/event=0x20,umask=0x1,cmask=1/", "core", "l2", profile="l2"),
    # OFFCORE_REQUESTS.DEMAND_DATA_RD: number of such requests.
    Counter("core_req", "cpu/event=0x21,umask=0x1/", "core", profile="req"),
    Counter("core_cycles", "cpu/cycles/", "core"),  # fixed counter
    # Same, restricted to demand reads known to have missed L3 (i.e. served
    # by memory): OFFCORE_REQUESTS_OUTSTANDING.L3_MISS_DEMAND_DATA_RD, its
    # cmask=1 variant, and OFFCORE_REQUESTS.L3_MISS_DEMAND_DATA_RD.
    Counter("l3m_t1", "cpu/event=0x20,umask=0x10/", "core", "l3m", profile="l3m"),
    Counter("l3m_t2", "cpu/event=0x20,umask=0x10,cmask=1/", "core", "l3m", profile="l3m"),
    Counter("l3m_req", "cpu/event=0x21,umask=0x10/", "core", profile="req"),
    # Ground truth and PACT's numerator for the stall model S = k N / MLP:
    # CYCLE_ACTIVITY.STALLS_L3_MISS (execution stalled while an L3-miss
    # demand load is pending) and MEM_LOAD_RETIRED.L3_MISS (N).
    Counter("stalls_l3m", "cpu/event=0xa3,umask=0x6,cmask=6/", "core"),
    Counter("l3_miss", "cpu/event=0xd1,umask=0x20/", "core"),
    # Uncore CHA, per socket (summed over its CHAs). IA demand reads that
    # missed the LLC and target socket-local memory (_drd_local; with the
    # workload bound to the CXL node this is the CXL tier).
    # TOR occupancy is counter-0 only; event 0x1f thresh=1 counts cycles with
    # counter-0 occupancy >= 1, so it must be grouped with T1 as leader.
    Counter("tor_t1", "uncore_cha/event=0x36,umask=0xc816fe01/", "uncore", "tor",
            ("unc_cha_tor_occupancy.ia_miss_drd_local",)),
    Counter("tor_t2", "uncore_cha/event=0x1f,thresh=1/", "uncore", "tor"),
    Counter("tor_ins", "uncore_cha/event=0x35,umask=0xc816fe01/", "uncore", None,
            ("unc_cha_tor_inserts.ia_miss_drd_local",)),
    Counter("cha_clk", "uncore_cha/event=0x1/", "uncore", None,
            ("unc_cha_clockticks",)),
]


def by_scope(scope, profile=None):
    """Counters of a scope; with a profile, only those recorded in it."""
    return [c for c in COUNTERS if c.scope == scope
            and (profile is None or c.profile in (None, profile))]


def profiles():
    return sorted({c.profile for c in COUNTERS if c.profile} | {"pebs"})


def perf_args(scope, profile):
    """One perf -e argument per line: each group as {a,b}, others alone."""
    out, groups = [], {}
    for c in by_scope(scope, profile):
        if c.group:
            groups.setdefault(c.group, []).append(c.perf())
        else:
            out.append(c.perf())
    return [f"{{{','.join(g)}}}" for g in groups.values()] + out


def name_map(scope):
    """Every name a counter may appear under in perf output -> its name."""
    m = {}
    for c in by_scope(scope):
        m[c.name] = c.name
        for a in c.aliases:
            m[a] = c.name
    return m


if __name__ == "__main__":
    if sys.argv[1] == "profiles":
        print(" ".join(profiles()))
    else:
        print("\n".join(perf_args(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "l2")))
