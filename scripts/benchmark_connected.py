#!/usr/bin/env python3
"""Repeatable bounded CPU benchmark over six public synthetic network fixtures.

Wall time is measured without allocation tracing. Python allocation peak is
measured in a separate warm-cache pass and is not total process RSS. This is
host evidence only; it does not predict an IQ-9075 or AutoCAD laptop result.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import statistics
import sys
import time
import tracemalloc

try:
    import resource
except ImportError:  # Windows has no resource module; timing remains usable.
    resource = None

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipe_router import connected  # noqa: E402


def clear_route_cache():
    """Explicit cold-cache control, used only by this measurement tool."""
    with connected._CACHE_LOCK:
        connected._CACHE.clear()


def timed(network, max_evaluations):
    started = time.perf_counter()
    result = connected.optimize_network(network, max_evaluations=max_evaluations)
    return result, (time.perf_counter() - started) * 1000


def summarize(samples):
    return {"median_ms":statistics.median(samples), "min_ms":min(samples), "max_ms":max(samples), "samples_ms":samples}


def measure(network, repeats, max_evaluations):
    source = deepcopy(network)
    cold, warm = [], []
    cold_hits, warm_hits = [], []
    statuses = set()
    for _ in range(repeats):
        clear_route_cache()
        result, elapsed = timed(network, max_evaluations)
        cold.append(elapsed)
        cold_hits.append(result["search"]["cache_hits"])
        statuses.add(result["status"])
    # The final cold pass has populated this exact scene's geometry cache.
    for _ in range(repeats):
        result, elapsed = timed(network, max_evaluations)
        warm.append(elapsed)
        warm_hits.append(result["search"]["cache_hits"])
        statuses.add(result["status"])
    revised = deepcopy(network)
    revised["drawing"]["revision"] += "-benchmark-revised"
    revision_result, revision_elapsed = timed(revised, max_evaluations)
    tracemalloc.start()
    connected.optimize_network(network, max_evaluations=max_evaluations)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss if resource else None
    # Linux reports KiB; macOS reports bytes. This is the entire process high
    # water mark, including imports and earlier scenarios, never a route delta.
    rss_bytes = int(rss if sys.platform == "darwin" else rss * 1024) if rss is not None else None
    assert network == source, "Benchmark unexpectedly mutated an input snapshot"
    assert len(statuses) == 1, "Warm and cold search disagreed on outcome"
    return {"id":network["id"],"status":result["status"],
            "source_revision":result["source_revision"],"source_digest":result["source_digest"],
            "cold":summarize(cold), "warm":summarize(warm),
            "cold_cache_hits":cold_hits,"warm_cache_hits":warm_hits,
            "python_peak_allocation_bytes_separate_warm_pass":peak,
            "process_high_water_rss_bytes_including_imports_and_prior_scenarios":rss_bytes,
            "changed_revision":{"elapsed_ms":revision_elapsed,"status":revision_result["status"],"cache_hits":revision_result["search"]["cache_hits"],"source_digest_changed":revision_result["source_digest"] != result["source_digest"]},
            "before":result["before"],"after":result["after"],
            "changed_span_ids":result["changed_span_ids"],
            "hard_failures":result["validation"]["hard_failures"],
            "unverified_checks":result["validation"]["unverified"],
            "search_evaluations":result["search"]["evaluations"],
            "bounded_search_limits":result["search"]["limitations"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats",type=int,default=3)
    parser.add_argument("--max-evaluations",type=int,default=8)
    parser.add_argument("--output",type=Path,help="Optional JSON evidence file, outside source CAD directories")
    args=parser.parse_args(argv)
    if not 1 <= args.repeats <= 20:
        parser.error("--repeats must be between 1 and 20")
    if not 1 <= args.max_evaluations <= connected.MAX_EVALUATIONS:
        parser.error(f"--max-evaluations must be between 1 and {connected.MAX_EVALUATIONS}")
    fixtures=sorted((ROOT/"examples"/"connected").glob("*.json"))
    if len(fixtures)!=6:
        parser.error("Expected exactly the six public A-F JSON fixtures")
    scenarios=[measure(json.loads(path.read_text()),args.repeats,args.max_evaluations) for path in fixtures]
    cancel_source=json.loads((ROOT/"examples"/"connected"/"c-connected-branch.json").read_text())
    started=time.perf_counter()
    cancelled=connected.optimize_network(cancel_source,max_evaluations=args.max_evaluations,cancelled=lambda:True)
    evidence={"benchmark":"pipeforge-connected/4.0","created_at_utc":datetime.now(timezone.utc).isoformat(),
              "runtime":{"python":platform.python_version(),"implementation":platform.python_implementation(),"system":platform.system(),"machine":platform.machine(),"processor":platform.processor()},
              "scope":"Invented synthetic geometry; local CPU only; not AutoCAD, remote transport, Snapdragon or IQ-9075 evidence",
              "measurement":"Wall time uses perf_counter without tracemalloc; Python allocation peak is a separate warm pass; ru_maxrss is whole-process high-water RSS including imports and earlier scenarios, not route-only allocation; RSS is null where resource is unavailable",
              "repeats":args.repeats,"max_evaluations":args.max_evaluations,"scenarios":scenarios,
              "immediate_cancellation":{"status":cancelled["status"],"elapsed_ms":(time.perf_counter()-started)*1000,"search_evaluations":cancelled["search"]["evaluations"],"source_preserved":cancelled["proposed_network"] is None},
              "limits":{"route_cache_capacity":connected.CACHE_LIMIT,"max_modifiable_spans":connected.MAX_SEARCH_SPANS,"global_optimum_proven":False,"geometry_cache_only":"Every candidate is revalidated across the complete supplied network"}}
    encoded=json.dumps(evidence,indent=2,allow_nan=False)+"\n"
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(encoded)
    print(encoded,end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
