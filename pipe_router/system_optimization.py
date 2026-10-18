"""Bounded coordination of explicitly selected, non-overlapping CAD sections.

This is an order-dependent review plan. Every other source section remains an
obstacle, and each earlier accepted proposal becomes an additional obstacle.
No endpoint contact is automatically waived and no source CAD is modified.
"""
from __future__ import annotations

from copy import deepcopy
import math
import time

from . import core
from .assembly import _closest_segments, plan_assembly
from .cad_layout import prepare_cad_layout
from .workflow import normalize_layout, run_workflow

MAX_SECTIONS = 12
MAX_SECONDS = 180.0  # Soft deadline between sections; an active search completes.
MAX_PAIR_SEGMENT_CHECKS = 2_000_000
_PROPOSAL_PREFIX = "system-proposal-"
_STATUSES = ("proposed", "unchanged", "blocked", "needs_review", "not_processed")
_PREPARE_OPTIONS = ("system_type", "clearance", "padding", "reverse_flow", "endpoint_connections",
                    "max_stub_length", "rule_profile", "model_context", "engineering")


def _candidate_records(cad, sections):
    records = list(cad["route_candidates"])
    if any(s["candidate_id"].startswith("network:") for s in sections):
        from .cad_network import inspect_network
        records.extend(inspect_network(cad)["chains"])
    return records


def _source_keys(candidate):
    instances = candidate.get("source_instances")
    if instances is None:
        instances = [{"source_handle": handle, "instance_path": candidate.get("instance_path", [])}
                     for handle in candidate.get("source_handles", [])]
    if not isinstance(instances, list) or not instances:
        raise ValueError("Candidate has no traceable source instances")
    keys = set()
    for instance in instances:
        if not isinstance(instance, dict):
            raise ValueError("Candidate source instances must be records")
        handle, path = instance.get("source_handle"), instance.get("instance_path", [])
        if not isinstance(handle, str) or not handle or not isinstance(path, list) or not all(isinstance(p, str) for p in path):
            raise ValueError("Candidate source identity is incomplete")
        keys.add((handle, tuple(path)))
    return keys


def _validate_inputs(cad, sections):
    if not isinstance(cad, dict) or cad.get("provenance") != "cad" or cad.get("units") != "mm" or cad.get("preview"):
        raise ValueError("System review requires the full immutable CAD import in millimetres")
    if not isinstance(cad.get("objects"), list) or not isinstance(cad.get("route_candidates"), list):
        raise ValueError("System review needs full CAD objects and route candidates")
    if not isinstance(sections, list) or not 1 <= len(sections) <= MAX_SECTIONS:
        raise ValueError(f"Select between 1 and {MAX_SECTIONS} sections")
    identifiers = set()
    for section in sections:
        if not isinstance(section, dict) or not isinstance(section.get("candidate_id"), str) or not section["candidate_id"]:
            raise ValueError("Every selected section must provide a candidate_id")
        if section["candidate_id"] in identifiers:
            raise ValueError("Duplicate candidate selection: select each source section once")
        identifiers.add(section["candidate_id"])
    records = _candidate_records(cad, sections)
    selected, used_sources, failures = {}, {}, {}
    for section in sections:
        identifier = section["candidate_id"]
        matching = [r for r in records if isinstance(r, dict) and r.get("id") == identifier]
        if len(matching) != 1:
            failures[identifier] = "Selected candidate is missing or ambiguous in this CAD snapshot"
            continue
        try:
            keys = _source_keys(matching[0])
        except ValueError as error:
            failures[identifier] = str(error)
            continue
        for key in keys:
            if key in used_sources:
                raise ValueError(f"Selected sections overlap source instance {key[0]!r}: {used_sources[key]} and {identifier}. Choose non-overlapping sections")
            used_sources[key] = identifier
        selected[identifier] = matching[0]
    return selected, failures


def _capsules(assembly):
    diameter = core._number(assembly.get("outer_diameter_mm"), "proposal outer diameter", positive=True)
    answer = []
    for component in assembly.get("components", []):
        component_diameter = core._number(component.get("outer_diameter_mm"), "component outer diameter", positive=True)
        if abs(component_diameter - diameter) > 1e-6:
            raise ValueError("A generated component changed the selected pipe diameter")
        error = core._number(component.get("chord_error_mm", 0), "arc chord error")
        if error < 0:
            raise ValueError("Arc chord error must be nonnegative")
        points = [core._point(p, "component centerline point") for p in component.get("centerline_points", [])]
        if len(points) < 2:
            raise ValueError("A generated component has no complete centerline")
        for start, end in zip(points, points[1:]):
            if math.dist(start, end) <= 1e-9:
                continue
            answer.append({"start": list(start), "end": list(end), "radius": diameter / 2 + error})
    if not answer:
        raise ValueError("A generated proposal has no physical component envelopes")
    return answer


def _proposal_obstacles(route, recipient_clearance):
    """Capsule radius includes OD, sagitta and any larger donor clearance."""
    extra = max(0.0, route["layout"]["rules"]["clearance"] - recipient_clearance)
    capsules = [{**item, "radius": item["radius"] + extra} for item in _capsules(route["result"]["assembly"])]
    obstacles = []
    for offset in range(0, len(capsules), 256):
        chunk = capsules[offset:offset + 256]
        obstacles.append({"id": f"{_PROPOSAL_PREFIX}{route['index']}-{offset // 256 + 1}",
            "min": [min(min(c["start"][a], c["end"][a]) - c["radius"] for c in chunk) for a in range(3)],
            "max": [max(max(c["start"][a], c["end"][a]) + c["radius"] for c in chunk) for a in range(3)],
            "capsules": chunk})
    return obstacles


def _coordinated_layout(layout, earlier):
    answer = deepcopy(layout)
    reserved = []
    for route in earlier:
        reserved.extend(_proposal_obstacles(route, answer["rules"]["clearance"]))
    if len(answer["obstacles"]) + len(reserved) > core.MAX_OBSTACLES:
        raise ValueError("Source obstacles plus accepted proposals exceed the obstacle limit. No geometry was dropped")
    answer["obstacles"].extend(reserved)
    answer["cad_context"]["coordination"] = {"earlier_sections": [r["candidate_id"] for r in earlier],
        "additional_obstacle_ids": [r["id"] for r in reserved], "all_original_other_sources_retained": True}
    return answer


def _pair_conflict(first, second):
    clearance = max(first["layout"]["rules"]["clearance"], second["layout"]["rules"]["clearance"])
    checks = 0
    for a in _capsules(first["result"]["assembly"]):
        for b in _capsules(second["result"]["assembly"]):
            checks += 1
            if checks > MAX_PAIR_SEGMENT_CHECKS:
                raise ValueError("Pairwise envelope verification exceeds the work limit; this later proposal is not accepted")
            limit = a["radius"] + b["radius"] + clearance
            if any(max(a["start"][axis], a["end"][axis]) + limit < min(b["start"][axis], b["end"][axis])
                   or max(b["start"][axis], b["end"][axis]) + limit < min(a["start"][axis], a["end"][axis]) for axis in range(3)):
                continue
            distance = _closest_segments(tuple(a["start"]), tuple(a["end"]), tuple(b["start"]), tuple(b["end"]))[2]
            if distance <= limit + 1e-6:
                return {"other_section_index": first["index"], "minimum_sample_segment_distance_mm": distance,
                        "required_separation_mm": limit, "clearance_mm": clearance}
    return None


def _block(route, message, code="coordination_rejected"):
    route["status"], route["message"] = "blocked", message
    route["coordination"]["accepted"] = False
    if route.get("result"):
        result = route["result"]
        result["status"], result["candidate"] = "no_route", None
        assembly = result.get("assembly", {})
        assembly["status"], assembly["meshes"], assembly["components"] = "blocked", [], []
        assembly["centerline_points"] = []
        assembly.setdefault("findings", []).append({"code": code, "status": "fail", "message": message})
        result.setdefault("search", {})["reason"] = message
        review = result.get("search_region_review") or {}
        # A locally accepted route can still fail pair coordination or the
        # final independent check. Its report must not keep advertising that
        # intermediate acceptance. Preserve the more specific pre-search
        # endpoint/boundary diagnosis when the local search itself was blocked.
        if code != "section_search_blocked" or review.get("boundary_review") == "accepted":
            review = deepcopy(review)
            review.update(boundary_review="coordination_rejected", coordination_failure_code=code, message=message,
                next_action=("Inspect the conflicting accepted sections and included source geometry. This proposal failed the coordinated or final independent check and is not saved. "
                             "Review the selected sections and constraints before rerunning; no source obstacle or connection is automatically exempted."))
            result["search_region_review"] = review


def _check_invariants(route):
    layout, result = route["layout"], route["result"]
    assembly, candidate = result.get("assembly", {}), result.get("candidate")
    if assembly.get("status") != "ready" or not candidate or candidate.get("valid") is not True or not assembly.get("meshes"):
        raise ValueError("No accepted rounded assembly was returned")
    if not assembly.get("reference_meshes"):
        raise ValueError("Original comparison meshes are unavailable; this section needs separate review")
    assessment = assembly.get("manufacturing_assessment", {})
    if assessment.get("status") == "blocked" or assessment.get("blocking_failure_count", 0):
        raise ValueError("A mandatory generated-piece finding blocks this proposal")
    for points in (candidate.get("points", []), assembly.get("centerline_points", [])):
        if len(points) < 2 or list(points[0]) != layout["pipe"]["start"] or list(points[-1]) != layout["pipe"]["end"]:
            raise ValueError("Generated proposal changed a fixed endpoint")
    if abs(assembly.get("outer_diameter_mm", 0) - layout["pipe"]["diameter"]) > 1e-6:
        raise ValueError("Generated proposal changed the source outer diameter")
    _capsules(assembly)


def _metrics(routes):
    comparable = [r for r in routes if r["status"] in ("proposed", "unchanged")
                  and r["result"]["assembly"].get("baseline_metrics")]
    all_accepted = [r for r in routes if r["status"] in ("proposed", "unchanged")]
    metrics = {"comparable_sections": len(comparable), "accepted_sections": len(all_accepted),
               "scope": "Only accepted sections with a valid rounded baseline under the same assumptions; not a whole-system total"}
    for key, source in (("length_mm", "centerline_length_mm"), ("bend_count", "bend_count")):
        metrics["original_" + key] = sum(r["result"]["assembly"]["baseline_metrics"][source] for r in comparable) if comparable else None
        metrics["suggested_" + key] = sum(r["result"]["assembly"]["metrics"][source] for r in comparable) if comparable else None
    metrics["all_accepted_comparable"] = bool(all_accepted) and len(all_accepted) == len(comparable)
    return metrics


def optimize_sections(cad, sections, *, progress=None):
    """Propose at most twelve source-disjoint CAD sections in explicit order.

    Invalid global identity or overlapping selections fail before any search.
    Every individual preparation/search failure is retained in the route list.
    """
    started = time.monotonic()
    selected, failures = _validate_inputs(cad, sections)
    requests = deepcopy(sections)
    routes, accepted, base_layouts = [], [], {}
    for index, request in enumerate(requests, 1):
        identifier = request["candidate_id"]
        candidate = selected.get(identifier, {})
        route = {"index": index, "candidate_id": identifier, "label": candidate.get("label", identifier),
                 "source_handles": sorted({key[0] for key in _source_keys(candidate)}) if candidate else [],
                 "status": "not_processed", "message": "Not processed", "coordination": {"accepted": False}}
        # Convenience label from the current client scan; never use a position
        # supplied by the client to select geometry. Candidate ID and the
        # immutable source snapshot remain authoritative.
        source_scan_number = request.get("source_scan_number")
        if isinstance(source_scan_number, int) and not isinstance(source_scan_number, bool) and source_scan_number > 0:
            route["source_scan_number"] = source_scan_number
            route["source_scan_number_basis"] = "client_scan_label; candidate_id is authoritative"
        routes.append(route)
        if time.monotonic() - started >= MAX_SECONDS:
            route["message"] = "The between-section time budget was reached. This section was not processed"
            continue
        if progress:
            progress({"total": len(requests), "completed": index - 1, "current_index": index,
                      "candidate_id": identifier, "message": "Preparing and checking selected section"})
        if identifier in failures:
            route.update(status="needs_review", message=failures[identifier])
            continue
        try:
            options = {name: deepcopy(request[name]) for name in _PREPARE_OPTIONS if name in request}
            # Only imported, explicitly selected contact identities can be waived.
            connections = options.get("endpoint_connections")
            if isinstance(connections, dict) and any(isinstance(value, list) and any(isinstance(v, str) and v.startswith(_PROPOSAL_PREFIX) for v in value) for value in connections.values()):
                raise ValueError("A generated proposal cannot be declared an endpoint connection")
            layout = prepare_cad_layout(cad, identifier, **options)
            layout["rules"]["grid_step"] = core._number(request.get("grid_step", 250), "grid_step", positive=True)
            if layout["system_type"] == "gravity":
                layout["rules"]["min_slope"] = core._number(request.get("min_slope", .01), "min_slope", positive=True)
                route.update(status="needs_review", message="Gravity sections require slope-verified physical bends; no rounded gravity proposal is accepted", layout=layout)
                continue
            radius = core._number(request.get("bend_radius_mm", max(304.8, 1.5 * layout["pipe"]["diameter"])), "bend_radius_mm", positive=True)
            layout["proposal"] = {"mode": "rounded_pipe", "bend_radius_mm": radius}
            base_layouts[index] = normalize_layout(layout)
            route["layout"] = _coordinated_layout(base_layouts[index], accepted)
            result = run_workflow(route["layout"])
            route["result"] = result
            route["coordination"]["earlier_sections"] = [r["candidate_id"] for r in accepted]
            if result.get("status") not in ("improved", "unchanged") or result.get("assembly", {}).get("status") != "ready" or not result.get("candidate"):
                reason = result.get("search", {}).get("reason", "No rounded proposal passed this bounded search")
                observed = list(dict.fromkeys(f.get("message", "") for f in result.get("assembly", {}).get("findings", [])
                                             if f.get("status") == "fail" and f.get("message")))[:2]
                if observed:
                    reason += " Observed checks: " + "; ".join(observed)
                _block(route, reason, "section_search_blocked")
                continue
            _check_invariants(route)
            route["status"] = "unchanged" if result["status"] == "unchanged" else "proposed"
            route["message"] = ("Existing section retained under the selected checks" if route["status"] == "unchanged" else "Rounded proposal checked against original surroundings and earlier accepted sections")
            conflict = next((conflict for earlier in accepted if (conflict := _pair_conflict(earlier, route))), None)
            if conflict:
                route["coordination"]["conflict"] = conflict
                _block(route, "The rounded proposal conflicts with an earlier accepted section", "proposal_pair_conflict")
                continue
            route["coordination"]["accepted"] = True
            accepted.append(route)
        except Exception as error:
            # A single CAD section must never erase the rest of the review.
            if route.get("result"):
                _block(route, f"Section validation stopped: {error}")
            else:
                route.update(status="needs_review", message=f"Section could not be processed: {error}")
    # Independently repeat physical assembly checks from the original layouts,
    # then pair-check the complete accepted set. No proposal connector waiver
    # is ever added to protected endpoints during either stage.
    verified = []
    for route in accepted:
        try:
            original_layout = base_layouts[route["index"]]
            checked = plan_assembly(original_layout, route["result"]["candidate"]["points"],
                                    bend_radius_mm=route["result"]["assembly"]["bend_radius_mm"])
            if checked["status"] != "ready":
                _block(route, "Final independent check against original surroundings failed", "original_geometry_recheck")
                continue
            conflict = next((conflict for prior in verified if (conflict := _pair_conflict(prior, route))), None)
            if conflict:
                route["coordination"]["conflict"] = conflict
                _block(route, "Final all-section clearance check failed", "proposal_pair_conflict")
                continue
            route["coordination"].update(original_geometry_revalidated=True, pair_clearance_revalidated=True)
            verified.append(route)
        except Exception as error:
            _block(route, f"Final independent validation stopped: {error}")
    counts = {status: sum(r["status"] == status for r in routes) for status in _STATUSES}
    result = {"schema_version": 1, "units": "mm", "provenance": "cad-derived",
        "source": {"name": cad.get("name"), "file": cad.get("source"), "sha256": cad.get("source_sha256")},
        "routes": routes, "counts": counts, "complete_system_verified": False,
        "coverage": {"requested_sections": len(requests), "processed_sections": len(routes) - counts["not_processed"],
                     "accepted_sections": len(verified), "complete_system_verified": False},
        "metrics": _metrics(routes), "elapsed_seconds": round(time.monotonic() - started, 3),
        "limits": {"maximum_sections": MAX_SECTIONS, "between_section_time_budget_seconds": MAX_SECONDS,
                   "maximum_pair_segment_checks": MAX_PAIR_SEGMENT_CHECKS},
        "limitations": ["Only explicitly selected non-overlapping sections are coordinated; unselected sections are not certified.",
            "Results depend on processing order. Other original sections remain obstacles even when proposals are accepted, so feasible joint moves can be missed.",
            "Proposal pairs use the larger requested clearance, full outer diameter and circular-arc chord-error inflation.",
            "Source connectivity, pipe identity, complete ship geometry, commercial fittings and engineering approval remain unverified.",
            "A bounded search failure does not prove there is no valid route. The time budget is checked between sections, not a hard request timeout.",
            "Saved meshes are review envelopes. Original CAD components are not replaced and gravity fabrication is not implemented."]}
    if progress:
        progress({"total": len(requests), "completed": len(requests), "current_index": None,
                  "candidate_id": None, "message": "Coordinated review complete", "counts": counts})
    return result
