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

`python3 counters.py core|uncore` prints the perf -e arguments, one per line.
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

    def perf(self) -> str:
        assert self.spec.endswith("/"), self.spec
        return f"{self.spec[:-1]},name={self.name}/"


COUNTERS = [
    # Core, per workload CPU (summed): L2-miss demand data reads.
    # OFFCORE_REQUESTS_OUTSTANDING.DEMAND_DATA_RD: per-cycle sum of pending.
    Counter("core_t1", "cpu/event=0x20,umask=0x1/", "core"),
    # ...CYCLES_WITH_DEMAND_DATA_RD (cmask=1): cycles with >= 1 pending.
    Counter("core_t2", "cpu/event=0x20,umask=0x1,cmask=1/", "core"),
    # OFFCORE_REQUESTS.DEMAND_DATA_RD: number of such requests.
    Counter("core_req", "cpu/event=0x21,umask=0x1/", "core"),
    Counter("core_cycles", "cpu/cycles/", "core"),
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


def by_scope(scope):
    return [c for c in COUNTERS if c.scope == scope]


def perf_args(scope):
    """One perf -e argument per line: each group as {a,b}, others alone."""
    out, groups = [], {}
    for c in by_scope(scope):
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
    print("\n".join(perf_args(sys.argv[1])))
