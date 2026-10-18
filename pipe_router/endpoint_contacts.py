"""Geometric evidence for endpoint contacts, never automatic connection approval.

Distances here describe the conservative obstacle representation used by the
checker. They are not measurements of source B-rep surfaces or equipment ports.
"""
from __future__ import annotations

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
