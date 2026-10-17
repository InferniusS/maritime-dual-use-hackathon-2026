"""Clearly labeled controlled changes to validated CAD-derived route layouts.

This module does not change CAD files or claim that a source designer made a
mistake. It creates a reproducible test input from geometry that already passes
our implemented checks. Unsupported or blocked inputs are rejected.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math

from . import core
from .workflow import normalize_layout, validate_layout_route

OFFSET_DISTANCES_MM = (25.0, 50.0, 100.0, 250.0, 500.0, 1000.0)
MAX_SEGMENTS_TO_TRY = 16
MAX_TRIALS = 384


def _protected_range(layout, points):
    """Return mutable segment indices without entering fixed endpoint stubs."""
    first, stop = 0, len(points) - 1
    for record in layout.get("protected_endpoints", []):
        if record["endpoint"] == "start":
            first = max(first, len(record["points"]) - 1)
        elif record["endpoint"] == "end":
            stop = min(stop, len(points) - len(record["points"]))
    return first, stop


def _eligible_segments(layout, points):
    first, stop = _protected_range(layout, points)
    eligible = []
    for index in range(first, stop):
        a, b = points[index:index + 2]
        changed = [axis for axis in range(3) if abs(a[axis] - b[axis]) > core.EPSILON]
        # Preserve all sampled curves. Only a single existing axis-aligned
        # straight segment is eligible for the controlled U-shaped detour.
        if len(changed) == 1 and math.dist(a, b) > core.EPSILON * 100:
            eligible.append((math.dist(a, b), index, changed[0]))
    return sorted(eligible, key=lambda item: (-item[0], item[1]))[:MAX_SEGMENTS_TO_TRY]


def make_detour_case(layout: dict, *, offset_mm: float | None = None) -> dict:
    """Return a checked pressure-pipe detour test with all dimensions in mm.

    The original route must already pass the public validator. All original
    points, fixed endpoint stubs, obstacles, and diameters are retained. Four
    new points add a U-shaped detour strictly inside one straight segment.
    An explicit offset_mm tries only that magnitude in different directions.
    The finite search can fail even when another valid detour might exist.
    """
    if not isinstance(layout, dict) or layout.get("provenance") != "cad-derived":
        raise ValueError("A controlled CAD detour needs a CAD-derived layout")
    if layout.get("system_type") != "pressurized":
        raise ValueError("The controlled detour currently supports pressure routes only")
    if isinstance(layout.get("benchmark"), dict) and layout["benchmark"].get("synthetic_mutation"):
        raise ValueError("This layout already has a synthetic mutation. Start from the unchanged baseline")
    if offset_mm is not None:
        offset_mm = core._number(offset_mm, "offset_mm", positive=True)
        if offset_mm > 1000:
            raise ValueError("offset_mm must not exceed 1000 mm")
    distances = OFFSET_DISTANCES_MM if offset_mm is None else (offset_mm,)
    normalized = normalize_layout(layout)
    points = normalized["pipe"]["original_route"]
    if len(points) + 4 > core.MAX_ROUTE_POINTS:
        raise ValueError("The baseline has too many points to add a controlled detour")
    baseline_check = validate_layout_route(normalized, points)
    if not baseline_check["valid"]:
        codes = sorted({issue.get("code", "unknown") for issue in baseline_check["issues"]})
        raise ValueError("The unchanged baseline must pass the implemented checks before adding a detour: " + ", ".join(codes))
    eligible = _eligible_segments(normalized, points)
    if not eligible:
        raise ValueError("No unprotected axis-aligned straight segment is available for a controlled detour")
    trials = 0
    for _, index, direction_axis in eligible:
        a, b = points[index:index + 2]
        quarter = [a[axis] + 0.25 * (b[axis] - a[axis]) for axis in range(3)]
        three_quarters = [a[axis] + 0.75 * (b[axis] - a[axis]) for axis in range(3)]
        for distance in distances:
            for offset_axis in range(3):
                if offset_axis == direction_axis:
                    continue
                for sign in (1, -1):
                    if trials >= MAX_TRIALS:
                        raise ValueError(f"No checked detour found within the {MAX_TRIALS}-trial limit. The unchanged baseline was preserved")
                    trials += 1
                    offset_start, offset_end = list(quarter), list(three_quarters)
                    offset_start[offset_axis] += sign * distance
                    offset_end[offset_axis] += sign * distance
                    added = [quarter, offset_start, offset_end, three_quarters]
                    trial_points = points[:index + 1] + added + points[index + 1:]
                    check = validate_layout_route(normalized, trial_points)
                    if not check["valid"] or check["length"] <= baseline_check["length"] + core.EPSILON:
                        continue
                    result = deepcopy(normalized)
                    result["pipe"]["original_route"] = deepcopy(trial_points)
                    result["pipe"]["route_representation"] = "controlled_detour_on_cad_centerline"
                    result["name"] = f"{normalized.get('name', 'CAD route')} with a controlled extra detour"
                    source_json = json.dumps(layout, sort_keys=True, allow_nan=False, separators=(",", ":"))
                    result["benchmark"] = {
                        "kind": "controlled_extra_detour",
                        "synthetic_mutation": True,
                        "label": "Controlled extra detour added for testing. Not an observed defect in the source CAD.",
                        "source_layout_sha256": hashlib.sha256(source_json.encode("utf-8")).hexdigest(),
                        "source_input_units": layout.get("units", "mm"),
                        "baseline_layout": deepcopy(normalized),
                        "baseline": {
                            "name": normalized.get("name"), "units": "mm",
                            "points": deepcopy(points), "diameter_mm": normalized["pipe"]["diameter"],
                            "validation": deepcopy(baseline_check),
                            "source": deepcopy(normalized.get("source", {})),
                            "cad_context": deepcopy(normalized.get("cad_context", {})),
                        },
                        "mutation": {
                            "segment_index": index, "offset_axis": "xyz"[offset_axis],
                            "offset_sign": sign, "offset_mm": distance,
                            "inserted_points": deepcopy(added),
                            "added_length_mm": check["length"] - baseline_check["length"],
                            "trials": trials,
                        },
                        "validation": deepcopy(check),
                        "fixed_original_points_preserved": True,
                        "source_cad_edited": False,
                        "limitations": [
                            "This is a generated test change on CAD-derived input. It is not a discovered source-model problem.",
                            "Passing the implemented checks does not verify missing geometry or engineering standards.",
                            "The search tries a limited set of axis-aligned detours. A valid detour does not guarantee the routing solver will find an improvement.",
                        ],
                    }
                    result.setdefault("assumptions", []).append(result["benchmark"]["label"])
                    return result
    raise ValueError(f"No checked detour found in {trials} bounded trials. The unchanged baseline was preserved")
