#!/usr/bin/env python3
"""Compute connected industrial A–G cases with isolated per-case measurements.

Cold/warm search time excludes imports and post-run independent validation.
Python allocation tracing uses a separate search pass. Each CLI scenario runs
in a fresh process so its RSS high-water mark does not include earlier cases.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
import tracemalloc

try:
    import resource
except ImportError:
    resource = None

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipe_router import connected  # noqa: E402
from pipe_router.industrial_examples import industrial_scenarios  # noqa: E402
from pipe_router.network_components import component_geometry  # noqa: E402
from pipe_router.network_fittings import clear_geometry_cache, fitting_envelopes, geometry_cache_stats  # noqa: E402
from pipe_router.network_model import graph_summary, network_digest  # noqa: E402
from pipe_router.network_objective import STRATEGIES  # noqa: E402
from pipe_router.network_validation import validate_network  # noqa: E402


def _geometry(network):
    """Report distinct polyline and supplied-radius developed centerline bases."""
    length = developed = 0.
    bends = 0
    developed_unknown = []
    for span in network["spans"]:
        points = span["points"]
        local = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
        length += local
        turns = []
        for a, b, c in zip(points, points[1:], points[2:]):
            u, v = [y-x for x, y in zip(a, b)], [y-x for x, y in zip(b, c)]
            cosine = sum(x*y for x, y in zip(u, v))/(math.dist(a, b)*math.dist(b, c))
            angle = math.acos(max(-1., min(1., cosine)))
            if angle > 1e-7:
                turns.append(angle)
        bends += len(turns)
        data = fitting_envelopes(span)
        if turns and (len(data["fittings"]) != len(turns) or
                      any(issue["code"] in ("missing_fitting_radius", "missing_fitting_body")
                          for issue in data["issues"])):
            developed_unknown.append(span["id"])
        else:
            developed += local - sum(2*fitting["radius_mm"]*math.tan(angle/2) -
                                     fitting["radius_mm"]*angle
                                     for fitting, angle in zip(data["fittings"], turns))
    return {"polyline_centerline_length_mm": length,
            "developed_centerline_length_mm": None if developed_unknown else developed,
            "developed_length_unverified_span_ids": developed_unknown,
            "direction_changes": bends,
            "length_basis": "Polyline intersections and supplied-radius tangent-arc centerline are distinct; neither is a verified fabrication cut schedule"}


def _collision_summary(validation):
    rows = [check for check in validation["checks"] if "collision" in check["id"]]
    return {"failed_collision_checks": sum(check["status"] == "FAIL" for check in rows),
            "witnesses_by_check": {check["id"]: len(check.get("evidence", {}).get("violations", []))
                                   for check in rows},
            "scope": "Counts are checks and reported witnesses; a physical clash can appear under multiple checks"}


def _summary(samples):
    return {"median_ms": statistics.median(samples), "min_ms": min(samples),
            "max_ms": max(samples), "samples_ms": samples}


def _rss_bytes():
    if resource is None:
        return None
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def measure_scene(network, *, repeats=1, max_evaluations=24, max_seconds=30,
                  strategy="balanced", measure_memory=True):
    """Compute actual proposals, source/topology audit and bounded progress."""
    source = deepcopy(network)
    samples = {"cold": [], "warm": []}
    search_samples = {"cold": [], "warm": []}
    outcomes, progress_samples, count = [], [], 0

    def progress(event):
        nonlocal count
        count += 1
        progress_samples.append(deepcopy(event))
        if len(progress_samples) > 64:
            del progress_samples[32]

    result = None
    for mode in ("cold", "warm"):
        for _ in range(repeats):
            if mode == "cold":
                with connected._CACHE_LOCK:
                    connected._CACHE.clear()
                clear_geometry_cache()
            started = time.perf_counter()
            result = connected.optimize_network(network, max_evaluations=max_evaluations,
                                                max_seconds=max_seconds, strategy=strategy,
                                                progress=progress)
            samples[mode].append((time.perf_counter() - started) * 1000)
            search_samples[mode].append({key: result["search"].get(key) for key in (
                "evaluations", "expanded_states", "grid_nodes_peak", "local_solves",
                "cache_hits", "total_neighborhoods", "completed_neighborhoods",
                "termination_reason", "time_budget_reached")})
            search_samples[mode][-1]["fitting_geometry_cache"] = geometry_cache_stats()
            outcomes.append((result["status"], network_digest(
                result["proposed_network"] or result["original_network"])))
    if result is None:
        raise RuntimeError("No timing samples were requested")
    if network != source:
        raise RuntimeError("Industrial optimization mutated its source snapshot")
    original = result["original_network"]
    final = result["proposed_network"] or original
    audit = validate_network(final, reference=original)
    if result["proposed_network"] is not None and audit["hard_failures"]:
        raise RuntimeError("Industrial proposal failed independent whole-network audit")
    original_connections = {(s["id"], s["from_node"], s["to_node"], s["system_id"])
                            for s in original["spans"]}
    final_connections = {(s["id"], s["from_node"], s["to_node"], s["system_id"])
                         for s in final["spans"]}
    allocation = {"measured": False, "scope": "No allocation pass requested"}
    if measure_memory:
        tracemalloc.start()
        started = time.perf_counter()
        try:
            traced = connected.optimize_network(network, max_evaluations=max_evaluations,
                                                max_seconds=max_seconds, strategy=strategy)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        allocation = {"measured": True, "peak_python_allocation_bytes": peak,
                      "elapsed_ms": (time.perf_counter()-started)*1000,
                      "status": traced["status"],
                      "time_budget_reached": traced["search"].get("time_budget_reached"),
                      "scope": "Separate warm optimizer pass with tracemalloc; instrumentation can alter time-budget outcomes; not RSS"}
    search = result["search"]
    considered = {solve["span_id"] for attempt in search["attempts"]
                  for solve in attempt.get("span_searches", [])}
    reused = {solve["span_id"] for attempt in search["attempts"]
              for solve in attempt.get("span_searches", []) if solve.get("incremental_reuse")}
    eligible = set(result["selection"]["modifiable_span_ids"])
    physical = component_geometry(original)
    return {
        "id": network["id"], "status": result["status"],
        "optimization_quality": result.get("optimization_quality"),
        "source_digest": result["source_digest"], "source_revision": result["source_revision"],
        "node_count": len(original["nodes"]), "span_count": len(original["spans"]),
        "system_count": len(original["systems"]),
        "selected_span_count": len(result["selection"]["selected_span_ids"]),
        "modifiable_selected_span_count": len(result["selection"]["modifiable_span_ids"]),
        "considered_span_count": len(considered), "considered_span_ids": sorted(considered),
        "unconsidered_selected_span_ids": sorted(eligible-considered),
        "all_selected_spans_considered": eligible <= considered,
        "incrementally_reused_span_ids": sorted(reused),
        "consideration_scope": "Local solves include independently justified unchanged-straight reuse and cache hits. Considered does not mean an A* search or a changed route; expanded_states counts actual new search work.",
        "physical_component_count": sum(item.get("min") is not None for item in physical),
        "unverified_physical_component_count": sum(item.get("min") is None for item in physical),
        "support_count": len(original.get("supports", [])),
        "topology": graph_summary(final),
        "connections_before": len(original_connections),
        "connections_preserved": len(original_connections & final_connections),
        "all_connections_preserved": original_connections == final_connections,
        "source_preserved": source == network,
        "before": _geometry(original), "after": _geometry(final),
        "objective_before": result["before"], "objective_after": result["after"],
        "collisions_before": _collision_summary(result["original_validation"]),
        "collisions_after": _collision_summary(audit),
        "changed_span_ids": result["changed_span_ids"],
        "original_hard_failures": result["original_validation"]["hard_failures"],
        "final_hard_failures": audit["hard_failures"], "unverified_checks": audit["unverified"],
        "has_accepted_proposal": result["proposed_network"] is not None,
        "release_status": result["release_status"],
        "cold": _summary(samples["cold"]), "warm": _summary(samples["warm"]),
        "search_samples": search_samples,
        "cold_warm_outcomes_identical": len(set(outcomes)) == 1,
        "python_allocation": allocation,
        "process_high_water_rss_bytes": _rss_bytes(),
        "fitting_geometry_cache": geometry_cache_stats(),
        "rss_scope": "Per-scenario worker process peak including imports, cold/warm searches and allocation pass; not a route-only delta; null where resource is unavailable",
        "search": {key: search.get(key) for key in (
            "evaluations", "max_evaluations", "expanded_states", "grid_nodes_peak", "local_solves",
            "completed_neighborhoods", "total_neighborhoods", "neighborhoods", "interaction_graph",
            "internal_repair_updates", "geometry_cache_hits", "geometry_cache_bytes",
            "termination_reason", "bounded_search_completed", "time_budget_reached", "evaluation_budget_reached",
            "cancelled", "cache_hits", "method", "limitations")},
        "progress": {"event_count": count, "retained_event_count": len(progress_samples),
                     "samples": progress_samples},
        "failed_final_checks": [check for check in audit["checks"] if check["status"] == "FAIL"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", action="append", default=[])
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-evaluations", type=int, default=24)
    parser.add_argument("--max-seconds", type=float, default=30)
    parser.add_argument("--strategy", choices=sorted(STRATEGIES), default="balanced")
    parser.add_argument("--no-memory", action="store_true",
                        help="Skip the separately instrumented allocation pass")
    parser.add_argument("--output", type=Path,
                        help="Write local JSON evidence outside Git or under local-output/")
    parser.add_argument("--worker-case", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not 1 <= args.repeats <= 20:
        parser.error("--repeats must be between 1 and 20")
    if not 1 <= args.max_evaluations <= connected.MAX_EVALUATIONS:
        parser.error(f"--max-evaluations must be between 1 and {connected.MAX_EVALUATIONS}")
    if not 0.05 <= args.max_seconds <= 120:
        parser.error("--max-seconds must be between 0.05 and 120")
    files = sorted((ROOT/"examples"/"industrial").glob("*.json"))
    sources = {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in files}
    if set(sources) != set(industrial_scenarios()):
        parser.error("Expected the seven generated industrial A–G source files")
    if any(value.get("provenance") != "synthetic" for value in sources.values()):
        parser.error("Only invented synthetic industrial sources may be benchmarked")
    selected = args.scenario or list(sources)
    if args.worker_case:
        if args.worker_case not in sources:
            parser.error("Unknown worker scenario")
        print(json.dumps(measure_scene(sources[args.worker_case], repeats=args.repeats,
                                      max_evaluations=args.max_evaluations,
                                      max_seconds=args.max_seconds, strategy=args.strategy,
                                      measure_memory=not args.no_memory), allow_nan=False))
        return 0
    if set(selected)-set(sources):
        parser.error("--scenario must be an exact industrial example identifier")
    results = []
    for identifier in selected:
        command = [sys.executable, str(Path(__file__).resolve()), "--worker-case", identifier,
                   "--repeats", str(args.repeats), "--max-evaluations", str(args.max_evaluations),
                   "--max-seconds", str(args.max_seconds), "--strategy", args.strategy]
        if args.no_memory:
            command.append("--no-memory")
        process = subprocess.run(command, check=True, capture_output=True, text=True,
                                 encoding="utf-8", cwd=ROOT)
        result = json.loads(process.stdout)
        results.append(result)
        print(f"{identifier} | {result['status']} | runs={result['span_count']} | "
              f"length={result['before']['polyline_centerline_length_mm']:.1f}→"
              f"{result['after']['polyline_centerline_length_mm']:.1f} mm | "
              f"changed={len(result['changed_span_ids'])} | hard={result['final_hard_failures']} | "
              f"cold/warm={result['cold']['median_ms']:.1f}/{result['warm']['median_ms']:.1f} ms",
              flush=True)
    evidence = {
        "benchmark": "pipeforge-industrial/5.1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime": {"python": platform.python_version(), "system": platform.system(),
                    "machine": platform.machine(), "processor": platform.processor()},
        "repeats": args.repeats, "max_evaluations": args.max_evaluations,
        "max_seconds_per_network": args.max_seconds, "strategy": args.strategy,
        "scope": "Invented connected industrial geometry on local CPU; no real CAD, AutoCAD runtime, NPU or hardware-board performance evidence",
        "measurement": "Each scenario has a fresh process. perf_counter search time excludes imports and post-run audit; cold clears route and physical-fitting geometry caches, warm retains them. No validation verdict is cached. Allocation tracing is a separate pass; RSS includes process imports and all passes for only this scenario.",
        "scenarios": results, "global_optimum_proven": False,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(evidence, indent=2, allow_nan=False)+"\n", encoding="utf-8")
        print(f"JSON evidence: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
