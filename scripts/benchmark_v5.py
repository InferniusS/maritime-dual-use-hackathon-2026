#!/usr/bin/env python3
"""Calculate ten invented multi-system scenes and measure cold/warm CPU time.

No result geometry or engineering outcome is stored in the scene files. Timing
excludes the independent post-run audit and optional allocation measurement.
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipe_router import connected  # noqa: E402
from pipe_router.network_model import network_digest  # noqa: E402
from pipe_router.network_objective import STRATEGIES  # noqa: E402
from pipe_router.network_validation import validate_network  # noqa: E402


def summarize(samples):
    return {"median_ms": statistics.median(samples), "min_ms": min(samples),
            "max_ms": max(samples), "samples_ms": samples}


def clear_route_cache():
    # Explicit benchmark instrumentation; the normal application owns its cache.
    with connected._CACHE_LOCK:
        connected._CACHE.clear()


def measure(network, *, repeats=3, max_evaluations=16, max_seconds=15,
            strategy="balanced", measure_memory=False):
    snapshot = deepcopy(network)
    samples = {"cold": [], "warm": []}
    hits = {"cold": [], "warm": []}
    outcomes = []
    latest = None
    for mode in ("cold", "warm"):
        for _ in range(repeats):
            if mode == "cold":
                clear_route_cache()
            started = time.perf_counter()
            latest = connected.optimize_network(
                network, max_evaluations=max_evaluations,
                max_seconds=max_seconds, strategy=strategy)
            samples[mode].append((time.perf_counter() - started) * 1000)
            hits[mode].append(latest["search"]["cache_hits"])
            outcomes.append((latest["status"], network_digest(
                latest["proposed_network"] or latest["original_network"])))
    assert latest is not None
    assert network == snapshot, "Search mutated the source scene"
    original = latest["original_network"]
    audited = validate_network(latest["proposed_network"] or original,
                               reference=original)
    if latest["proposed_network"] is not None:
        assert audited["hard_failures"] == 0, "Independent audit rejected proposal"
    allocation_peak = None
    if measure_memory:
        tracemalloc.start()
        try:
            connected.optimize_network(network, max_evaluations=max_evaluations,
                                       max_seconds=max_seconds, strategy=strategy)
            _, allocation_peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
    return {
        "id": network["id"], "status": latest["status"],
        "source_digest": latest["source_digest"],
        "source_revision": latest["source_revision"],
        "cold": summarize(samples["cold"]), "warm": summarize(samples["warm"]),
        "cold_cache_hits": hits["cold"], "warm_cache_hits": hits["warm"],
        "cold_warm_outcomes_identical": len(set(outcomes)) == 1,
        "python_peak_allocation_bytes_separate_warm_pass": allocation_peak,
        "before": latest["before"], "after": latest["after"],
        "changed_span_ids": latest["changed_span_ids"],
        "original_hard_failures": latest["original_validation"]["hard_failures"],
        "independent_hard_failures": audited["hard_failures"],
        "unverified_checks": audited["unverified"],
        "release_status": latest["release_status"],
        "search_evaluations": latest["search"]["evaluations"],
        "search_method": latest["search"]["method"],
        "search_limitations": latest["search"]["limitations"],
        "failed_checks": [check for check in audited["checks"]
                          if check["status"] == "FAIL"],
        "source_preserved": network == snapshot,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--max-evaluations", type=int, default=16)
    parser.add_argument("--max-seconds", type=float, default=15)
    parser.add_argument("--strategy", choices=sorted(STRATEGIES), default="balanced")
    parser.add_argument("--scenario", action="append", default=[],
                        help="Exact scenario stem; may be repeated")
    parser.add_argument("--measure-memory", action="store_true",
                        help="Additional separately timed warm pass with tracemalloc")
    parser.add_argument("--output", type=Path,
                        help="Optional JSON evidence file; use local-output/ or outside Git")
    args = parser.parse_args(argv)
    if not 1 <= args.repeats <= 20:
        parser.error("--repeats must be between 1 and 20")
    if not 1 <= args.max_evaluations <= connected.MAX_EVALUATIONS:
        parser.error(f"--max-evaluations must be between 1 and {connected.MAX_EVALUATIONS}")
    if not 0.05 <= args.max_seconds <= 120:
        parser.error("--max-seconds must be between 0.05 and 120")
    files = sorted((ROOT / "examples" / "multi-system").glob("*.json"))
    if len(files) != 10:
        parser.error("Expected exactly ten synthetic multi-system scenes")
    if args.scenario:
        selected = set(args.scenario)
        unknown = selected - {path.stem for path in files}
        if unknown:
            parser.error(f"Unknown scenarios: {', '.join(sorted(unknown))}")
        files = [path for path in files if path.stem in selected]
    scenes = [json.loads(path.read_text(encoding="utf-8")) for path in files]
    if any(scene.get("provenance") != "synthetic" for scene in scenes):
        parser.error("This tool processes synthetic fixtures only")
    results = [measure(scene, repeats=args.repeats,
                       max_evaluations=args.max_evaluations,
                       max_seconds=args.max_seconds, strategy=args.strategy,
                       measure_memory=args.measure_memory) for scene in scenes]
    started = time.perf_counter()
    cancelled = connected.optimize_network(scenes[0], cancelled=lambda: True,
                                            max_evaluations=args.max_evaluations)
    evidence = {
        "benchmark": "pipeforge-multi-system/5.0",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime": {"python": platform.python_version(),
                    "implementation": platform.python_implementation(),
                    "system": platform.system(), "machine": platform.machine(),
                    "processor": platform.processor()},
        "scope": "Invented geometry, local CPU only. No native AutoCAD, real ship, remote transport, NPU or IQ-9075 measurement.",
        "measurement": "perf_counter wall time excludes the independent post-run audit. Cold clears the route geometry cache; warm retains it. Neither clears operating-system or import caches. Optional tracemalloc measures a separate pass and is not process RSS.",
        "repeats": args.repeats, "max_evaluations": args.max_evaluations,
        "max_seconds_per_network": args.max_seconds, "strategy": args.strategy,
        "scenarios": results,
        "immediate_cancellation": {
            "status": cancelled["status"],
            "elapsed_ms": (time.perf_counter() - started) * 1000,
            "source_preserved": cancelled["proposed_network"] is None},
        "global_optimum_proven": False,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(evidence, indent=2, allow_nan=False) + "\n",
                               encoding="utf-8")
    print("Scenario | calculated status | length before→after (mm) | changed | cold/warm median ms")
    for result in results:
        print(f"{result['id']} | {result['status']} | "
              f"{result['before']['length_mm']:.1f}→{result['after']['length_mm']:.1f} | "
              f"{len(result['changed_span_ids'])} | "
              f"{result['cold']['median_ms']:.1f}/{result['warm']['median_ms']:.1f}")
    if args.output:
        print(f"JSON evidence: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
