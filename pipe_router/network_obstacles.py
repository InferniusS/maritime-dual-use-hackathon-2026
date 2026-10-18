"""Reversible hypothetical environment edits with complete-network analysis.

This module never changes a native drawing, a route, or a component. An edited
environment becomes a new source baseline; its digest invalidates older route
proposals independently of the unchanged native drawing revision.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math

from backend.geometry import segment_box_distance
from .network_fittings import fitting_envelopes, span_occupied_segments
from .network_model import normalize_network, network_digest
from .network_standards import requirement
from .network_validation import validate_network

MAX_ENVIRONMENT_EDITS = 128
MAX_ACTIVE_REVIEW_OBSTACLES = 64
MAX_RECENT_CHANGES = 64
MAX_ENVIRONMENT_LOG_BYTES = 128_000
_STRUCTURE = {"wall", "bulkhead", "deck", "structure", "stiffener", "ship_structure", "ship-structure", "structural"}
_CAD_FIELDS = {"cad_handle", "source_handle", "source_handles", "source_instances", "source_member_ids",
               "source_cad_handle", "source_cad_id", "original_cad_handle", "original_cad_id", "cad_object_id"}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _identifier(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError("obstacle_id must be nonempty text up to 256 characters")
    return value


def _vector(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3 or any(isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item) for item in value):
        raise ValueError("translation_mm must contain three finite XYZ numbers")
    if not any(value):
        raise ValueError("A move requires a nonzero translation_mm")
    return list(value)


def _kind(obstacle):
    kind = obstacle.get("kind", obstacle.get("type", "unknown"))
    if not isinstance(kind, str):
        raise ValueError("Obstacle kind must be text")
    return kind.lower().strip()


def _has_cad_source(obstacle):
    return any(obstacle.get(field) for field in _CAD_FIELDS)


def _is_structure(obstacle):
    return any(isinstance(obstacle.get(field), str) and obstacle[field].strip().lower() in _STRUCTURE
               for field in ("kind", "type", "category", "classification"))


def _review_flags(obstacle):
    return (obstacle.get("origin") == "designer-review" and obstacle.get("environment_edit_created") is True
            and obstacle.get("simulation") is True and not _has_cad_source(obstacle))


def _creation_record(obstacle, history):
    return next((record for record in reversed(history) if record.get("action") == "add"
                 and record.get("obstacle_id") == obstacle["id"] and record.get("origin") == "designer-review"
                 and record.get("simulation") is True), None)


def _require_editable(obstacle, history):
    if _has_cad_source(obstacle):
        raise ValueError("CAD-source obstacle geometry is immutable; use a separate hypothetical review obstacle")
    constraints = obstacle.get("constraints") or {}
    if not isinstance(constraints, dict):
        raise ValueError("Obstacle constraints must be an object")
    if "protected" in constraints and not isinstance(constraints["protected"], bool):
        raise ValueError("Obstacle protected constraint must be explicitly boolean")
    if obstacle.get("protected") is True or constraints.get("protected") is True:
        raise ValueError("Protected structure or equipment cannot be moved or removed")
    if _is_structure(obstacle) and not (_review_flags(obstacle) and _creation_record(obstacle, history)):
        raise ValueError("Existing ship structure is protected; only recorded hypothetical review walls can be edited")
    if obstacle.get("editable") is not True or obstacle.get("fixed", True) is not False:
        raise ValueError("Moving or removing an obstacle requires editable:true and fixed:false")


def _summary(obstacle):
    if obstacle is None:
        return None
    return {"id": obstacle["id"], "kind": _kind(obstacle), "min": deepcopy(obstacle["min"]), "max": deepcopy(obstacle["max"]),
            "geometry_digest": _digest(obstacle), "capsule_count": len(obstacle.get("capsules", [])),
            "simulation": obstacle.get("simulation", False), "editable": obstacle.get("editable", False),
            "fixed": obstacle.get("fixed", True), "protected": obstacle.get("protected", False)}


def _translate(obstacle, delta, up):
    for field in ("min", "max", "floor_point"):
        if field in obstacle:
            obstacle[field] = [value+offset for value, offset in zip(obstacle[field], delta)]
    for capsule in obstacle.get("capsules", []):
        for field in ("start", "end", "clip_min", "clip_max"):
            if field in capsule:
                capsule[field] = [value+offset for value, offset in zip(capsule[field], delta)]
    if "floor_elevation_mm" in obstacle:
        if up is None or abs(sum(item*item for item in up)-1) > 1e-6:
            raise ValueError("Moving a declared floor elevation requires an explicit unit ship-up axis")
        obstacle["floor_elevation_mm"] += sum(value*offset for value, offset in zip(up, delta))


def _bounded_history(history, obstacles):
    active = {obstacle["id"] for obstacle in obstacles if _review_flags(obstacle)}
    if len(active) > MAX_ACTIVE_REVIEW_OBSTACLES:
        raise ValueError("At most 64 active hypothetical review obstacles may be edited in one source snapshot")
    creation = {}
    for index, record in enumerate(history):
        if record.get("action") == "add" and record.get("obstacle_id") in active and record.get("origin") == "designer-review" and record.get("simulation") is True:
            creation[record["obstacle_id"]] = index
    keep = set(creation.values())
    recent = [index for index in range(len(history)) if index not in keep][-MAX_RECENT_CHANGES:]
    result = [deepcopy(history[index]) for index in sorted(keep | set(recent))]
    while len(json.dumps(result, allow_nan=False)) > MAX_ENVIRONMENT_LOG_BYTES and len(recent) > 1:
        recent.pop(0)
        result = [deepcopy(history[index]) for index in sorted(keep | set(recent))]
    if len(json.dumps(result, allow_nan=False)) > MAX_ENVIRONMENT_LOG_BYTES:
        raise ValueError("Environment creation proof and latest change exceed the 128 KB audit budget")
    return result


def _preserve_unknown_coverage(network):
    uncertain = [obstacle for obstacle in network["obstacles"] if _kind(obstacle) == "unknown"
                 or obstacle.get("geometry_verified") is False
                 or str(obstacle.get("geometry_status", "")).upper() in ("UNKNOWN", "UNVERIFIED", "INCOMPLETE")]
    if not uncertain:
        return
    coverage = network["coverage"]
    coverage["complete"] = False
    coverage["unknown_count"] = max(coverage.get("unknown_count", 0), len(uncertain))
    note = "Unknown obstacle geometry remains a conservative active envelope; environment editing does not verify missing dimensions or source fidelity."
    notes = coverage.setdefault("notes", [])
    if not isinstance(notes, list):
        raise ValueError("Coverage notes must be a list before preserving unknown obstacle evidence")
    if note not in notes:
        notes.append(note)


def _capsule_near_box(capsule, box, clearance):
    radius = capsule["radius"]+clearance
    if any(max(capsule["start"][axis], capsule["end"][axis])+radius < box["min"][axis]
           or min(capsule["start"][axis], capsule["end"][axis])-radius > box["max"][axis] for axis in range(3)):
        return False
    return float(segment_box_distance(capsule["start"], capsule["end"], box["min"], box["max"])) <= radius+1e-6


def _boxes_near(left, right, clearance):
    return all(left["min"][axis]-clearance <= right["max"][axis] and left["max"][axis]+clearance >= right["min"][axis] for axis in range(3))


def _affected_routes(network, before, after, action):
    from .network_components import component_geometry
    regions = [obstacle for obstacle in (before, after) if obstacle is not None]
    clearance = float(requirement("clearance.shock")["value"])
    reasons = {}
    tests = 0
    unknown_fittings = []
    for span in network["spans"]:
        span_reasons = []
        if any(issue["code"] in ("missing_fitting_radius", "missing_fitting_body") for issue in fitting_envelopes(span)["issues"]):
            unknown_fittings.append(span["id"])
            span_reasons.append("physical fitting dimensions are missing; affectedness cannot be excluded")
        capsules = span_occupied_segments(span)
        for region in regions:
            for capsule in capsules:
                tests += 1
                if _capsule_near_box(capsule, region, clearance):
                    span_reasons.append("finite pipe/fitting envelope intersects changed region or its required clearance")
                    break
        # Removing/moving a blocker may improve a detoured route that never
        # touched its old envelope. Test a potential endpoint shortcut too.
        if before is not None and action in ("move", "remove"):
            straight = {"start":span["points"][0], "end":span["points"][-1], "radius":span["outer_diameter_mm"]/2}
            tests += 1
            if _capsule_near_box(straight, before, clearance):
                span_reasons.append("old obstacle intersects potential direct endpoint corridor")
        if span_reasons:
            reasons[span["id"]] = list(dict.fromkeys(span_reasons))
    unknown_components = []
    known_span_ids = {span["id"] for span in network["spans"]}
    for component in component_geometry(network):
        incident = [identifier for identifier in component.get("incident_span_ids", []) if identifier in known_span_ids]
        known = component.get("min") is not None and component.get("max") is not None
        if not known:
            if component.get("issues"):
                unknown_components.append(component["id"])
                for identifier in incident or sorted(known_span_ids):
                    reasons.setdefault(identifier, []).append("component dimensions are missing; affectedness cannot be excluded")
            continue
        near = any(_boxes_near(component, region, clearance) for region in regions)
        if near:
            for identifier in incident or sorted(known_span_ids):
                reasons.setdefault(identifier, []).append("incident finite component envelope intersects changed region or clearance" if incident else "nearby physical component has no confirmed incident route; retain whole-network review scope")
    direct = set(reasons)
    touched_nodes = {identifier for span in network["spans"] if span["id"] in direct for identifier in (span["from_node"], span["to_node"])}
    neighbors = {span["id"] for span in network["spans"] if span["from_node"] in touched_nodes or span["to_node"] in touched_nodes}-direct
    for identifier in neighbors:
        reasons[identifier] = ["connected neighbor of an affected route; review shared component incidence together"]
    return {"affected_span_ids":sorted(direct | neighbors), "directly_affected_span_ids":sorted(direct),
            "connected_neighbor_span_ids":sorted(neighbors), "affected_detection":{
                "method":"Conservative finite pipe/elbow/component envelopes against old/new obstacle bounds, old-region shortcut corridors and one-hop connected neighbors",
                "conservative":True, "required_clearance_mm":clearance, "primitive_region_tests":tests,
                "unknown_component_ids":sorted(unknown_components), "unknown_fitting_span_ids":sorted(unknown_fittings), "reasons_by_span":reasons,
                "scope":"Candidates for coordinated reoptimization, not proof that other routes need no review. All source context is retained and independently validated."}}


def _pairs(validation, prefix):
    records = {}
    for check in validation["checks"]:
        if not check["id"].startswith(prefix):
            continue
        for violation in check.get("evidence", {}).get("violations", []):
            identifiers = sorted({str(violation[key]) for key in ("id", "other_id", "obstacle_id") if violation.get(key) is not None})
            if not identifiers:
                continue
            key = tuple(identifiers)
            records.setdefault(key, {"component_ids":identifiers, "check_ids":[]})["check_ids"].append(check["id"])
    for record in records.values():
        record["check_ids"] = sorted(set(record["check_ids"]))
    return records


def _collision_delta(original_validation, validation):
    before_collision, after_collision = _pairs(original_validation, "collision."), _pairs(validation, "collision.")
    before_clearance, after_clearance = _pairs(original_validation, "clearance.shock"), _pairs(validation, "clearance.shock")
    def summary(report, collisions, clearances):
        shock = next(check for check in report["checks"] if check["id"] == "clearance.shock")
        return {"hard_failures":report["hard_failures"], "collision_pair_count":len(collisions), "clearance_pair_count":len(clearances),
                "minimum_surface_gap_mm":shock.get("evidence", {}).get("minimum_surface_gap_mm"), "validation_status":report["status"]}
    return {"before":summary(original_validation, before_collision, before_clearance), "after":summary(validation, after_collision, after_clearance),
            "hard_failure_delta":validation["hard_failures"]-original_validation["hard_failures"],
            "new_collision_pairs":[after_collision[key] for key in sorted(after_collision.keys()-before_collision.keys())],
            "resolved_collision_pairs":[before_collision[key] for key in sorted(before_collision.keys()-after_collision.keys())],
            "new_clearance_pairs":[after_clearance[key] for key in sorted(after_clearance.keys()-before_clearance.keys())],
            "resolved_clearance_pairs":[before_clearance[key] for key in sorted(before_clearance.keys()-after_clearance.keys())],
            "counting_basis":"Unique reported component/obstacle ID sets; repeated fitting and structure findings for the same pair are counted once. Minimum gaps apply to supplied conservative geometry."}


def edit_obstacle(network, operation):
    """Copy, edit and independently analyze one explicit review obstacle.

    Operation geometry and translation are millimetres even when input source
    coordinates are declared in metres/inches. Native source IDs are immutable.
    """
    source = normalize_network(network)
    if not isinstance(operation, dict):
        raise ValueError("Obstacle edit operation must be an object")
    action = operation.get("action")
    if action not in ("add", "move", "remove"):
        raise ValueError("Obstacle action must be add, move or remove")
    identifier = _identifier(operation.get("obstacle_id"))
    revision = source.get("environment_revision", 0)
    if isinstance(revision, bool) or not isinstance(revision, int) or not 0 <= revision < 1_000_000_000:
        raise ValueError("environment_revision must be a nonnegative integer below 1000000000")
    history = source.get("environment_edits", [])
    if not isinstance(history, list) or len(history) > MAX_ENVIRONMENT_EDITS or any(not isinstance(record, dict) for record in history):
        raise ValueError("environment_edits must be a bounded list of at most 128 audit records")
    if any(record.get("action") not in ("add", "move", "remove") or not isinstance(record.get("obstacle_id"), str) for record in history):
        raise ValueError("Each environment edit record needs an action and obstacle_id")
    edited = deepcopy(source)
    found = next((obstacle for obstacle in edited["obstacles"] if obstacle["id"] == identifier), None)
    before, after, delta = deepcopy(found), None, None
    if action == "add":
        if found is not None:
            raise ValueError("An obstacle with this ID already exists")
        supplied = operation.get("obstacle")
        if not isinstance(supplied, dict) or supplied.get("id", identifier) != identifier:
            raise ValueError("An added obstacle must be an object with a matching obstacle_id")
        after = deepcopy(supplied)
        after["id"] = identifier
        if "min" not in after or "max" not in after:
            raise ValueError("An added obstacle needs explicit finite min/max dimensions; unknown geometry cannot be guessed")
        if _has_cad_source(after):
            raise ValueError("Review obstacle creation cannot assign or impersonate CAD source handles")
        for flag in ("editable", "fixed", "protected", "simulation"):
            if flag in after and not isinstance(after[flag], bool):
                raise ValueError("Obstacle " + flag + " must be explicitly boolean")
        after.setdefault("kind", after.get("type", "unknown"))
        after.setdefault("editable", False)
        after.setdefault("fixed", True)
        after.setdefault("protected", False)
        # These provenance flags are assigned here, not accepted as proof from
        # arbitrary supplied geometry. Structural exceptions also need this add
        # operation in the bounded environment log.
        after["origin"], after["environment_edit_created"] = "designer-review", True
        after["simulation"] = supplied.get("simulation") is True
        if _is_structure(after) and not after["simulation"]:
            after["fixed"], after["editable"] = True, False
        edited["obstacles"].append(after)
    else:
        if found is None:
            raise ValueError("Unknown obstacle_id")
        _require_editable(found, history)
        if action == "move":
            delta = _vector(operation.get("translation_mm"))
            _translate(found, delta, source["coordinate_system"].get("up"))
            after = found
        else:
            edited["obstacles"].remove(found)
    _preserve_unknown_coverage(edited)
    edited["environment_revision"] = revision+1
    change = {"action":action, "obstacle_id":identifier, "before":_summary(before), "after":_summary(after),
              "environment_revision":revision+1, "source_drawing_revision":source["drawing"]["revision"],
              "previous_source_digest":network_digest(source), "origin":"designer-review",
              "simulation":bool(after.get("simulation")) if after is not None else bool(before.get("simulation"))}
    if delta is not None:
        change["translation_mm"] = delta
    edited["environment_edits"] = _bounded_history(history+[change], edited["obstacles"])
    edited = normalize_network(edited)
    original_validation = validate_network(source, reference=source)
    validation = validate_network(edited, reference=edited)
    affected = _affected_routes(edited, before, after, action)
    return {"network":edited, "validation":validation, "original_validation":original_validation,
            "change":change, "environment_revision":revision+1, "source_digest":network_digest(edited),
            "previous_source_digest":network_digest(source), "collision_delta":_collision_delta(original_validation, validation),
            "native_cad_modified":False, "proposal_invalidated":True, **affected}
