"""Geometric evidence for endpoint contacts, never automatic connection approval.

Distances here describe the conservative obstacle representation used by the
checker. They are not measurements of source B-rep surfaces or equipment ports.
"""
from __future__ import annotations

from copy import deepcopy
import math

from . import core


def _box_margin(point, low, high):
    return max(0.0, *(max(a - p, p - b) for p, a, b in zip(point, low, high)))


def _segment_distance(point, start, end):
    direction = [b - a for a, b in zip(start, end)]
    squared = sum(v * v for v in direction)
    t = max(0.0, min(1.0, sum((p - a) * v for p, a, v in zip(point, start, direction)) / squared)) if squared else 0.0
    return math.dist(point, [a + t * v for a, v in zip(start, direction)])


def envelope_contact_margin(point, obstacle):
    """Inflation needed for a point to touch this checker's envelope.

    This matches its expanded boxes and clipped capsules at a single point;
    in particular, box expansion is not a Euclidean source-surface distance.
    """
    broad = _box_margin(point, obstacle["min"], obstacle["max"])
    if "capsules" not in obstacle:
        return broad
    margins = []
    for capsule in obstacle["capsules"]:
        distance = max(0.0, _segment_distance(point, capsule["start"], capsule["end"]) - capsule["radius"])
        if "clip_min" in capsule:
            distance = max(distance, _box_margin(point, capsule["clip_min"], capsule["clip_max"]))
        margins.append(distance)
    return max(broad, min(margins))


def endpoint_stub_extent(points, prepared_obstacles):
    """First continuous contact from the endpoint; later re-entry stays active."""
    intervals, travelled = [], 0.0
    for start, end in zip(points, points[1:]):
        length = math.dist(start, end)
        for obstacle in prepared_obstacles:
            intervals.extend((travelled + low * length, travelled + high * length)
                             for low, high in core._segment_obstacle_intervals(start, end, obstacle))
        travelled += length
    end_of_contact, next_contact = 0.0, travelled
    for low, high in sorted(intervals):
        if low > end_of_contact + core.EPSILON:
            next_contact = low
            break
        end_of_contact = max(end_of_contact, high)
    covers_route = end_of_contact >= travelled - core.EPSILON
    return {
        "contact_length_mm": end_of_contact,
        "required_length_mm": None if covers_route else end_of_contact + min(0.001, (next_contact - end_of_contact) / 2),
        "route_length_mm": travelled,
        "reentry_distance_mm": next_contact if next_contact < travelled else None,
        "covers_route": covers_route,
    }


def contact_evidence(points, endpoint, obstacle, pipe_radius, clearance, maximum_stub):
    """Explain a discovered contact without granting any obstacle exemption."""
    directed = points if endpoint == "start" else list(reversed(points))
    point = directed[0]
    margin = envelope_contact_margin(point, obstacle)
    overlap = margin <= pipe_radius + core.EPSILON
    method = obstacle.get("obstacle_method", "supported_surface_capsules" if "capsules" in obstacle else "sample_box")
    free_margin = max(0.0, margin - pipe_radius)
    if overlap:
        summary = "The pipe-radius envelope touches this component envelope even without added clearance. This does not prove a physical connection."
    else:
        summary = (f"Clearance-only contact: the pipe-radius envelope is separate from this component envelope; "
                   f"approximately {free_margin:.3f} mm of envelope margin remains, below the selected {clearance:g} mm clearance. "
                   "This is not evidence of a physical joint.")
    if method == "sample_box":
        summary += " This component uses a fallback box, which can include empty space."
    prepared = core._prepare_obstacle(obstacle, "endpoint evidence", pipe_radius + clearance)
    extent = endpoint_stub_extent(directed, [prepared])
    required = extent["required_length_mm"]
    status = "covers_route" if extent["covers_route"] else "exceeds_limit" if required > maximum_stub else "available"
    return {
        "contact_kind": "pipe_envelope_overlap" if overlap else "clearance_only",
        "contact_summary": summary,
        "geometry_basis": method,
        "connection_verified": False,
        "requires_explicit_confirmation": True,
        "envelope_contact_margin_mm": margin,
        "remaining_pipe_clearance_mm": free_margin,
        "clearance_required_mm": clearance,
        "distance_basis": "Inflation of the checker envelope; not a measured CAD surface gap",
        "stub_preview": {"status": status, "required_length_mm": required, "limit_mm": maximum_stub,
                         "reentry_distance_mm": extent["reentry_distance_mm"],
                         "scope": "Single-contact preview only. Combined selected contacts are recomputed; unrelated obstacles stay active."},
    }


def connection_selection_review(layout):
    """Check the fixed portions *after* explicit contact selections are applied.

    This is an early diagnostic, not a connection recognizer or a waiver. It
    uses the same inflated envelopes as route validation, reports every
    unselected component touching an unchanged stub, and checks the movable
    anchor against every obstacle, including selected contacts. The rounded
    proposal still requires its independent complete-route checks.
    """
    route = layout["pipe"]["original_route"]
    margin = layout["pipe"]["diameter"] / 2 + layout["rules"]["clearance"]
    obstacles = layout["obstacles"]
    shapes = [(obstacle, core._prepare_obstacle(obstacle, "fixed connection review", margin))
              for obstacle in obstacles]
    sources = {}
    for context_name in ("cad_context", "model_context"):
        context = layout.get(context_name)
        if not isinstance(context, dict):
            continue
        for source in context.get("obstacle_sources", []) or []:
            if isinstance(source, dict) and isinstance(source.get("id"), str):
                sources[source["id"]] = source
    records = {record["endpoint"]: record for record in layout.get("protected_endpoints", [])}
    ends = []
    for endpoint in ("start", "end"):
        directed = route if endpoint == "start" else list(reversed(route))
        record = records.get(endpoint)
        fixed = (record["points"] if endpoint == "start" else list(reversed(record["points"]))) if record else [directed[0]]
        approved = set(record.get("obstacle_ids", [])) if record else set()
        anchor = fixed[-1]
        # For the end connection, this direction points backward into the
        # movable route. It is not an equipment-port orientation inference.
        tangent_points = fixed if len(fixed) > 1 else directed[:2]
        tangent = None
        for a, b in reversed(list(zip(tangent_points, tangent_points[1:]))):
            distance = math.dist(a, b)
            if distance > core.EPSILON:
                tangent = [(y - x) / distance for x, y in zip(a, b)]
                break
        blockers = []
        for obstacle, shape in shapes:
            identifier = obstacle["id"]
            scopes, segments = [], []
            if identifier not in approved:
                if core._segment_intersects_box(tuple(fixed[0]), tuple(fixed[0]), shape):
                    scopes.append("fixed_endpoint")
                for index, (a, b) in enumerate(zip(fixed, fixed[1:])):
                    if core._segment_intersects_box(tuple(a), tuple(b), shape):
                        segments.append(index)
                if segments:
                    scopes.append("unchanged_stub")
            if record and core._segment_intersects_box(tuple(anchor), tuple(anchor), shape):
                scopes.append("movable_anchor")
            if not scopes:
                continue
            source = sources.get(identifier, {})
            blockers.append({
                "obstacle_id": identifier,
                "source_handle": source.get("source_handle", identifier),
                "instance_path": deepcopy(source.get("instance_path", [])),
                "scopes": scopes, "fixed_segment_indices": segments,
                "explicitly_selected_contact": identifier in approved,
                "obstacle_method": source.get("obstacle_method", "supported_surface_capsules" if "capsules" in obstacle else "sample_box"),
            })
        ends.append({
            "endpoint": endpoint,
            "status": "blocked" if blockers else "not_blocked_by_fixed_connection_check",
            "fixed_endpoint": list(fixed[0]), "movable_anchor": list(anchor),
            "fixed_stub_points": deepcopy(fixed),
            "fixed_stub_length_mm": sum(math.dist(a, b) for a, b in zip(fixed, fixed[1:])),
            "departure_direction_into_movable_section": tangent,
            "direction_basis": "last_preserved_segment" if record else "original_reference_segment_not_a_verified_port",
            "selected_obstacle_ids": sorted(approved), "blockers": blockers,
        })
    count = sum(len(item["blockers"]) for item in ends)
    if count:
        message = ("Fixed connection geometry still touches an active obstacle envelope. "
                   "A larger search area cannot clear an unchanged endpoint or stub. "
                   "Inspect the listed source components and connection assumptions; unrelated obstacles remain active.")
    else:
        message = ("No active obstacle contact was found on the fixed portions under the explicit selections. "
                   "This only permits the search to proceed; it does not verify connection identity, tangent bend room, or a complete route.")
    return {
        "status": "blocked" if count else "not_blocked_by_fixed_connection_check",
        "message": message, "endpoints": ends, "blocker_count": count,
        "obstacle_count_retained": len(obstacles), "connection_identity_verified": False,
        "all_obstacles_active_on_movable_section": True,
        "basis": "Conservative supplied obstacle envelopes expanded by pipe radius and clearance",
    }
