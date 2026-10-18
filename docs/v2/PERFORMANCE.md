# Measured performance observations

Same pinned Python runtime, same five original fixtures and settings (0.5 m grid, 0.1 m clearance). Three untraced runs per implementation in sequential subprocesses. The v1 backend is read from the preserved commit using a temporary Git archive; no checkout or application files are reset.

| Scenario | v1 median (s) | v2 median (s) | v1/v2 runtime ratio | Same outcome/length/bends |
| --- | ---: | ---: | ---: | --- |
| inefficient | 0.007275 | 0.008749 | 0.83 | True |
| congested | 1.312332 | 0.324028 | 4.05 | True |
| drainage | 0.002876 | 0.005408 | 0.53 | True |
| impossible | 1.692800 | 0.421435 | 4.02 | True |
| efficient | 0.001510 | 0.001722 | 0.88 | True |

The congested and sealed-bulkhead searches are about four times faster in this observation. Direct-connector cases are slightly slower because extra numerical evidence and validation metadata add overhead; they remain in the millisecond range. These ratios are not guaranteed speedups and do not establish performance on an official vessel model.

The original congested profile attributed most runtime to repeated point-to-route segment-distance evaluation. Per-node caching, a specialized point/segment distance, zero-weight skipping, stale-entry pruning and spatial broad-phase filtering address that measured work. Straight-envelope narrow-phase tests remain authoritative.

The new gravity and vent detours, coordinated services, explored alternatives, validation times and Python traced-memory peaks are recorded in [benchmark.json](benchmark.json). Tracemalloc memory excludes native allocations and its separate traced run perturbs runtime. Untraced medians are reported independently.

Reproduce with `python scripts/compare_baseline.py` and `python scripts/benchmark.py` using the core virtual environment, without competing builds or tests.
