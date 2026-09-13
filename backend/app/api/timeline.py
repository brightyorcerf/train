"""Per-phase timing (§16) behind GET /trace/{id}/timeline.

HONESTY NOTE: before day 9 the engine recorded exactly ONE number — `wall_clock_s`. The four
phases §16 names (fetch / normalize / graph / score) were never measured, so this module adds the
instrumentation rather than pretending to expose something that already existed. Anything we did
not actually measure is reported as null, never as a plausible-looking share of the total.

`trace_jobs` has no per-phase column, so the breakdown rides in the job result under `phases`.
"""
import time
from contextlib import contextmanager

PHASES = ("fetch", "normalize", "graph", "score")


class Phases:
    """Accumulates elapsed seconds per phase. Celery workers each hold their own, so per-hop
    totals are summed in level_done — wall-clock across parallel tasks is NOT additive, and the
    `concurrent` flag says so rather than letting a reader mistake the sum for elapsed time."""

    def __init__(self, seed: dict | None = None):
        # Only phases actually entered get a key. Seeding all four with 0.0 would make an
        # UNINSTRUMENTED phase indistinguishable from one that genuinely took no time, and
        # summarize() would then report a measured-looking zero for work nobody measured.
        self.t: dict[str, float] = {}
        self.merge(seed)

    @contextmanager
    def phase(self, name: str):
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.t[name] = self.t.get(name, 0.0) + (time.perf_counter() - t0)

    def merge(self, other: dict | None) -> "Phases":
        for k, v in (other or {}).items():
            if k in PHASES:
                self.t[k] = self.t.get(k, 0.0) + float(v or 0.0)
        return self

    def as_dict(self) -> dict:
        return {k: round(v, 4) for k, v in self.t.items()}


def summarize(result: dict) -> dict:
    """Job result -> the /trace/{id}/timeline body."""
    ph = result.get("phases") or {}
    measured = {k: v for k, v in ph.items() if k in PHASES}
    total_measured = round(sum(measured.values()), 4) if measured else None
    wall = result.get("wall_clock_s")
    return {
        "wall_clock_s": wall,
        "attribution_wall_s": result.get("attribution_wall_s"),
        "phases": {p: measured.get(p) for p in PHASES},   # null = not measured, not zero
        "phase_total_s": total_measured,
        "concurrent": True,
        "note": ("phase seconds are summed across parallel Celery workers, so they may exceed "
                 "wall_clock_s; they measure work done, not elapsed time. null = not instrumented."),
        "calls": {"logical": result.get("api_calls"), "upstream": result.get("upstream_calls"),
                  "from_store": result.get("store_hits")},
        "hops": result.get("hops"),
        "addresses_seen": result.get("addresses_seen"),
        "providers": result.get("providers", {}),
        "stale_reads": len(result.get("stale_reads") or []),
        "partial": result.get("partial", False),
    }
