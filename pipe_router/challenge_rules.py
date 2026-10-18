"""Traceable checks from the five-page Pipe Layout Optimization V3 brief.

``assess_layout`` accepts an already normalized millimeter layout. It evaluates
only the supplied geometry and explicit engineering observations. It is not a
certificate of compliance and never calls polyline vertices physical bends.

Optional ``layout.engineering`` is an object with ``units: 'mm'``. Its lengths
are always millimeters, even when the original drawing used another unit.
Supported fields are::

    {
      "units": "mm", "source_refs": ["drawing/component handle"],
      "route_points": [[0, 0, 1000], [1000, 0, 960]],
      "nominal_diameter_mm": 50, "nominal_size_basis_verified": true,
      "ship_axes": {"longitudinal": "+X", "transverse": "+Y",
                    "up": "+Z", "confirmed": true},
      "flow_direction": "start_to_end",
      "inventory_complete": {"pieces": true, "hangers": false,
                             "walkways": false, "valves": false},
      "pieces": [{"id": "P1", "source_refs": ["CAD:P1"],
                  "centerline_length_mm": 1000,
                  "bends": [{"radius_mm": 152.4, "angle_deg": 90}],
                  "straight_between_bends_mm": [],
                  "weld_spacings_mm": [25],
                  "planarity_deviation_mm": 0,
                  "planarity_tolerance_mm": 1}],
      "hangers": [{"id": "H1", "source_refs": ["CAD:H1"],
                   "orientation": "vertical", "distance_mm": 381}],
      "walkways": [{"id": "W1", "source_refs": ["CAD:W1"],
                    "headroom_mm": 1960}],
      "valves": [{"id": "V1", "source_refs": ["CAD:V1"],
                  "orientation": "vertical_up", "height_above_deck_mm": 1000}],
      "evidence": {"fitting_catalog": {"verified": true,
                      "source_refs": ["reviewed catalog and component mapping"]}}
    }

``route_points`` must match the assessed route to within LENGTH_TOLERANCE_MM
before any assembly measurements can pass. Missing source references or an
incomplete inventory leaves the corresponding check in ``needs_review``.
This prevents original measurements from silently certifying a new route.
An empty, explicitly complete inventory means the rule is not applicable.
Malformed supplied values raise ValueError instead of disappearing as unknown.

Ship axes may alternatively come from ``layout.model_context.ship_axes``.
Only horizontal signed X/Y axes and +Z up are currently supported. Diagonal
horizontal drain runs are left for review: the brief supplies no diagonal law.
All tolerances below are numerical comparison tolerances, not manufacturing
allowances. Near-planarity needs an explicit reviewer-supplied tolerance because
the brief does not specify one. Source pages are one-based PDF page numbers.
"""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any

from . import core

PROFILE_ID = "fmg-v3"
DOCUMENT = "[n2 Eng] Pipe Layout Optimization V3-.pdf"
LENGTH_TOLERANCE_MM = 1e-6
ANGLE_TOLERANCE_DEG = 1e-6
PROFILE = {
    "id": PROFILE_ID,
    "document": DOCUMENT,
    "shock_clearance_mm": 50.8,
    "longitudinal_min_slope": 0.04,
    "transverse_min_slope": 0.01,
    "max_piece_length_mm": 6096.0,
    "min_weld_spacing_mm": 25.0,
    "max_bends_per_piece": 3,
    "preferred_angle_increment_deg": 5.0,
    "min_walkway_headroom_mm": 1960.0,
    "preferred_vertical_hanger_distance_mm": 381.0,
    "max_horizontal_hanger_distance_mm": 50.8,
}
_TOP_KEYS = {"units", "source_refs", "route_points", "nominal_diameter_mm", "ship_axes",
             "flow_direction", "nominal_size_basis_verified", "inventory_complete", "pieces", "hangers", "walkways", "valves", "evidence"}
_EVIDENCE_KEYS = {"ship_boundary", "required_spaces", "scene_coverage", "fitting_catalog", "component_sizes"}
STATUSES = ("pass", "fail", "needs_review", "not_applicable")


def _mapping(value: Any, label: str) -> dict:
    return core._mapping(value, label)


def _keys(value: dict, allowed: set[str], label: str) -> None:
    extra = set(value) - allowed
    if extra:
        raise ValueError(f"{label} has unsupported fields: {', '.join(sorted(str(key) for key in extra))}")


def _number(value: Any, label: str, *, positive: bool = False) -> float:
    result = core._number(value, label, positive=positive)
    if result < 0:
        raise ValueError(f"{label} must be nonnegative")
    return result


def _refs(record: dict, label: str) -> list[str]:
    refs = record.get("source_refs", [])
    if not isinstance(refs, list) or any(not isinstance(ref, str) or not ref.strip() for ref in refs):
        raise ValueError(f"{label}.source_refs must be a list of nonempty strings")
    return refs


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be true or false")
    return value


def _points(value: Any, label: str) -> list[tuple[float, float, float]]:
    if not isinstance(value, (list, tuple)) or not 2 <= len(value) <= core.MAX_ROUTE_POINTS:
        raise ValueError(f"{label} must contain 2 to {core.MAX_ROUTE_POINTS} points")
    return [core._point(point, f"{label}[{index}]") for index, point in enumerate(value)]


def _axes(value: Any, label: str) -> dict:
    axes = _mapping(value, label)
    _keys(axes, {"longitudinal", "transverse", "up", "confirmed"}, label)
    for key in ("longitudinal", "transverse", "up"):
        if key in axes and axes[key] not in ("+X", "-X", "+Y", "-Y", "+Z", "-Z"):
            raise ValueError(f"{label}.{key} must name a signed coordinate axis")
    if "confirmed" in axes:
        _boolean(axes["confirmed"], f"{label}.confirmed")
    present = [axes[key][-1] for key in ("longitudinal", "transverse", "up") if key in axes]
    if len(present) != len(set(present)):
        raise ValueError(f"{label} axes must be mutually perpendicular")
    return axes


def _validate_engineering(layout: dict) -> dict:
    engineering = layout.get("engineering")
    if engineering is None:
        return {}
    _mapping(engineering, "engineering")
    _keys(engineering, _TOP_KEYS, "engineering")
    if engineering.get("units") != "mm":
        raise ValueError("engineering.units must explicitly be mm")
    _refs(engineering, "engineering")
    if "route_points" in engineering:
        _points(engineering["route_points"], "engineering.route_points")
    if "nominal_diameter_mm" in engineering:
        _number(engineering["nominal_diameter_mm"], "engineering.nominal_diameter_mm", positive=True)
    if "nominal_size_basis_verified" in engineering:
        _boolean(engineering["nominal_size_basis_verified"], "engineering.nominal_size_basis_verified")
    if "ship_axes" in engineering:
        _axes(engineering["ship_axes"], "engineering.ship_axes")
    if "flow_direction" in engineering and engineering["flow_direction"] not in ("start_to_end", "end_to_start"):
        raise ValueError("engineering.flow_direction must be start_to_end or end_to_start")
    complete = engineering.get("inventory_complete", {})
    _mapping(complete, "engineering.inventory_complete")
    _keys(complete, {"pieces", "hangers", "walkways", "valves"}, "engineering.inventory_complete")
    for key, value in complete.items():
        _boolean(value, f"engineering.inventory_complete.{key}")
    allowed = {
        "pieces": {"id", "source_refs", "centerline_length_mm", "bends", "straight_between_bends_mm", "weld_spacings_mm", "planarity_deviation_mm", "planarity_tolerance_mm"},
        "hangers": {"id", "source_refs", "orientation", "distance_mm"},
        "walkways": {"id", "source_refs", "headroom_mm"},
        "valves": {"id", "source_refs", "orientation", "height_above_deck_mm"},
    }
    for group, fields in allowed.items():
        if group not in engineering:
            continue
        records = engineering[group]
        if not isinstance(records, list) or len(records) > 10000:
            raise ValueError(f"engineering.{group} must be a list with at most 10000 entries")
        seen = set()
        for i, record in enumerate(records):
            label = f"engineering.{group}[{i}]"
            _mapping(record, label)
            _keys(record, fields, label)
            identity = record.get("id")
            if not isinstance(identity, str) or not identity.strip() or identity in seen:
                raise ValueError(f"{label}.id must be nonempty and unique within {group}")
            seen.add(identity)
            _refs(record, label)
            for field in ("centerline_length_mm", "planarity_deviation_mm", "planarity_tolerance_mm", "distance_mm", "headroom_mm", "height_above_deck_mm"):
                if field in record:
                    _number(record[field], f"{label}.{field}", positive=field == "centerline_length_mm")
            if group == "hangers" and "orientation" in record and record["orientation"] not in ("vertical", "horizontal"):
                raise ValueError(f"{label}.orientation must be vertical or horizontal")
            if group == "valves" and "orientation" in record and record["orientation"] not in ("horizontal", "vertical_up", "vertical_down"):
                raise ValueError(f"{label}.orientation must be horizontal, vertical_up, or vertical_down")
            if "bends" in record:
                if not isinstance(record["bends"], list) or len(record["bends"]) > 10000:
                    raise ValueError(f"{label}.bends must be a list with at most 10000 entries")
                for j, bend in enumerate(record["bends"]):
                    prefix = f"{label}.bends[{j}]"
                    _mapping(bend, prefix)
                    _keys(bend, {"radius_mm", "angle_deg"}, prefix)
                    for field in ("radius_mm", "angle_deg"):
                        if field in bend:
                            _number(bend[field], f"{prefix}.{field}", positive=True)
                    if bend.get("angle_deg", 0) > 180:
                        raise ValueError(f"{prefix}.angle_deg must be at most 180")
            for field in ("straight_between_bends_mm", "weld_spacings_mm"):
                if field in record:
                    if not isinstance(record[field], list) or len(record[field]) > 10000:
                        raise ValueError(f"{label}.{field} must be a list with at most 10000 entries")
                    for j, value in enumerate(record[field]):
                        _number(value, f"{label}.{field}[{j}]")
            if "bends" in record and "straight_between_bends_mm" in record:
                if len(record["straight_between_bends_mm"]) != max(0, len(record["bends"]) - 1):
                    raise ValueError(f"{label}.straight_between_bends_mm needs one entry between each consecutive pair of physical bends")
    evidence = engineering.get("evidence", {})
    _mapping(evidence, "engineering.evidence")
    _keys(evidence, _EVIDENCE_KEYS, "engineering.evidence")
    for key, record in evidence.items():
        label = f"engineering.evidence.{key}"
        _mapping(record, label)
        _keys(record, {"verified", "source_refs"}, label)
        if "verified" in record:
            _boolean(record["verified"], f"{label}.verified")
        _refs(record, label)
    return engineering


def validate_engineering_metadata(layout: dict) -> None:
    """Reject malformed supplied metadata before starting an expensive search."""
    _mapping(layout, "layout")
    _validate_engineering(layout)


def directional_requirements(layout: dict) -> dict:
    """Return confirmed horizontal ship axes and flow direction, or a reason.

    Missing information is not inferred from the shape or order of a polyline.
    This helper does not claim that system flow direction is CAD-verified.
    """
    _mapping(layout, "layout")
    engineering = layout.get("engineering", {})
    _mapping(engineering, "engineering")
    if engineering and engineering.get("units") != "mm":
        raise ValueError("engineering.units must explicitly be mm")
    if "flow_direction" in engineering and engineering["flow_direction"] not in ("start_to_end", "end_to_start"):
        raise ValueError("engineering.flow_direction must be start_to_end or end_to_start")
    axes = engineering.get("ship_axes")
    context = layout.get("model_context", {})
    if axes is None and isinstance(context, dict):
        axes = context.get("ship_axes")
    if axes is not None:
        axes = _axes(axes, "ship_axes")
    flow = engineering.get("flow_direction")
    result = {"ready": False, "longitudinal_min_slope": 0.04, "transverse_min_slope": 0.01,
              "flow_direction": flow, "ship_axes": deepcopy(axes)}
    if not axes or not axes.get("confirmed") or any(key not in axes for key in ("longitudinal", "transverse", "up")):
        return {**result, "reason": "Confirm the ship's longitudinal, transverse and upward axes."}
    if axes["up"] != "+Z" or axes["longitudinal"][-1] not in "XY" or axes["transverse"][-1] not in "XY":
        return {**result, "reason": "This implementation supports horizontal signed X/Y ship axes with +Z upward only."}
    if flow is None:
        return {**result, "reason": "Confirm flow_direction as start_to_end or end_to_start."}
    return {**result, "ready": True, "reason": None,
            "longitudinal_axis": "XYZ".index(axes["longitudinal"][-1]),
            "transverse_axis": "XYZ".index(axes["transverse"][-1])}


def directional_minimum_fall(layout: dict, start: Any, end: Any) -> float | None:
    """Required fall in mm, or None for missing axes/flow or diagonal runs.

    Caller must order the points with flow. A purely vertical segment requires
    nonnegative fall and returns zero. This function never invents a diagonal
    interpolation between the two slopes specified in the brief.
    """
    settings = directional_requirements(layout)
    start, end = core._point(start, "start"), core._point(end, "end")
    if not settings["ready"]:
        return None
    long = abs(end[settings["longitudinal_axis"]] - start[settings["longitudinal_axis"]])
    transverse = abs(end[settings["transverse_axis"]] - start[settings["transverse_axis"]])
    if long > LENGTH_TOLERANCE_MM and transverse > LENGTH_TOLERANCE_MM:
        return None
    return long * 0.04 if long > LENGTH_TOLERANCE_MM else transverse * 0.01


def bend_requirements(nominal_diameter_mm: float | None) -> dict | None:
    """Size-category requirements use declared nominal size, never outside diameter."""
    if nominal_diameter_mm is None:
        return None
    size = _number(nominal_diameter_mm, "nominal_diameter_mm", positive=True)
    if size <= 50 + LENGTH_TOLERANCE_MM:
        return {"min_radius_mm": 152.4, "min_straight_between_bends_mm": 101.6}
    if abs(size - 80) <= LENGTH_TOLERANCE_MM:
        return {"min_radius_mm": 228.6, "min_straight_between_bends_mm": 177.8}
    if size >= 100 - LENGTH_TOLERANCE_MM:
        return {"min_radius_mm": 304.8, "min_straight_between_bends_mm": 228.6}
    return None


def _bound(engineering: dict, points: list) -> bool:
    raw = engineering.get("route_points")
    return raw is not None and len(raw) == len(points) and all(math.dist(a, b) <= LENGTH_TOLERANCE_MM for a, b in zip(raw, points))


def _finding(identity: str, status: str, page: int, message: str, *, measured: Any = None,
             required: Any = None, refs: list[str] | None = None, severity: str = "error") -> dict:
    return {"id": identity, "rule_id": identity, "status": status, "severity": severity,
            "source": {"document": DOCUMENT, "page": page}, "message": message,
            "measured": measured, "required": required, "source_refs": list(dict.fromkeys(refs or []))}


def _inventory_finding(engineering: dict, points: list, group: str, identity: str, page: int,
                       required: Any, evaluate, *, severity: str = "error") -> dict:
    records = engineering.get(group)
    bound = _bound(engineering, points)
    complete = engineering.get("inventory_complete", {}).get(group, False)
    observations = []
    for record in records or []:
        status, value, message = evaluate(record)
        if not bound or not record.get("source_refs"):
            status = "needs_review"
            message = "Measurements need source references and a binding to this exact route."
        observations.append({"id": record["id"], "status": status, "value": value, "message": message})
    if not bound or records is None:
        status = "needs_review"
    elif any(item["status"] == "fail" for item in observations):
        status = "fail"
    elif not complete or any(item["status"] == "needs_review" for item in observations):
        status = "needs_review"
    elif not observations or all(item["status"] == "not_applicable" for item in observations):
        status = "not_applicable"
    else:
        status = "pass"
    refs = [ref for record in records or [] for ref in record.get("source_refs", [])]
    message = {
        "pass": "All supplied measurements pass this check for the declared complete inventory.",
        "fail": "At least one source-referenced measurement does not meet this requirement.",
        "needs_review": "A complete source-referenced inventory and measurements bound to this route are required.",
        "not_applicable": "The reviewed inventory contains no applicable components or measurements.",
    }[status]
    if severity == "preference" and status == "fail":
        message = "At least one measurement is outside the brief's recommendation. This is not a hard prohibition."
    return _finding(identity, status, page, message, measured=observations, required=required, refs=refs, severity=severity)


def _limit(record: dict, field: str, *, minimum: float | None = None, maximum: float | None = None):
    if field not in record:
        return "needs_review", None, f"Supply {field}."
    value = record[field]
    failed = minimum is not None and value + LENGTH_TOLERANCE_MM < minimum
    failed |= maximum is not None and value - LENGTH_TOLERANCE_MM > maximum
    return ("fail" if failed else "pass"), value, "Compared explicit measurement with the limit."


def _shock(layout: dict, points: list) -> dict:
    obstacles = layout.get("obstacles")
    if not isinstance(obstacles, list):
        raise ValueError("layout.obstacles must be a list")
    if len(obstacles) > core.MAX_OBSTACLES:
        raise ValueError("layout.obstacles exceeds the supported local obstacle limit")
    pipe = _mapping(layout.get("pipe"), "pipe")
    diameter = _number(pipe.get("diameter"), "pipe.diameter", positive=True)
    shapes = [core._prepare_obstacle(item, f"obstacles[{i}]", diameter / 2 + 50.8) for i, item in enumerate(obstacles)]
    if len({shape.id for shape in shapes}) != len(shapes):
        raise ValueError("layout.obstacles IDs must be unique")
    if sum(len(shape.capsules) for shape in shapes) > core.MAX_TOTAL_CAPSULES:
        raise ValueError("layout.obstacles exceeds the supported capsule limit")
    exemptions = {}
    protected = layout.get("protected_endpoints", [])
    if not isinstance(protected, list) or len(protected) > 2:
        raise ValueError("protected_endpoints must contain at most two records")
    seen = set()
    for record in protected:
        _mapping(record, "protected endpoint")
        endpoint = record.get("endpoint")
        if endpoint not in ("start", "end") or endpoint in seen:
            raise ValueError("protected endpoint labels must be unique start or end")
        seen.add(endpoint)
        stub = _points(record.get("points"), "protected endpoint points")
        identifiers = record.get("obstacle_ids")
        if not isinstance(identifiers, list) or any(not isinstance(item, str) or item not in {shape.id for shape in shapes} for item in identifiers):
            raise ValueError("protected endpoint obstacle_ids must name supplied obstacles")
        maximum = _number(record.get("max_distance"), "protected endpoint max_distance", positive=True)
        if maximum > 1000 or sum(math.dist(a, b) for a, b in zip(stub, stub[1:])) > maximum + LENGTH_TOLERANCE_MM:
            raise ValueError("protected endpoint exceeds its bounded fixed connection length")
        original = _points(pipe.get("original_route"), "pipe.original_route")
        if len(stub) >= len(original):
            raise ValueError("protected endpoint must leave a movable route section")
        expected = original[:len(stub)] if endpoint == "start" else original[-len(stub):]
        if stub != expected:
            raise ValueError("protected endpoint must be an exact original route prefix or suffix")
        actual = points[:len(stub)] if endpoint == "start" else points[-len(stub):]
        if actual != stub:
            continue
        first = 0 if endpoint == "start" else len(points) - len(stub)
        for index in range(first, first + len(stub) - 1):
            exemptions[index] = set(identifiers)
    if protected and sum(len(record["points"]) - 1 for record in protected) >= len(pipe["original_route"]) - 1:
        raise ValueError("protected endpoints must leave a movable middle section")
    hits, waived = [], []
    for i, (start, end) in enumerate(zip(points, points[1:])):
        for shape in shapes:
            if core._segment_intersects_box(start, end, shape):
                destination = waived if shape.id in exemptions.get(i, set()) else hits
                destination.append({"segment": i, "obstacle_id": shape.id})
    refs = [item["obstacle_id"] for item in hits]
    status = "fail" if hits else "pass" if shapes else "needs_review"
    return _finding("shock_clearance", status, 1,
        "Checked continuous centerline plus outside radius and 50.8 mm clearance against supplied obstacle envelopes only. Boundary contact is conservatively rejected. Accepted fixed connection contacts remain explicit assumptions.",
        measured={"obstacle_count": len(shapes), "collisions": hits, "assumed_fixed_contacts": waived},
        required={"minimum_clearance_mm": 50.8, "scope": "supplied local obstacle envelopes"}, refs=refs)


def assess_layout(layout: dict, route: list | None = None) -> dict:
    """Return JSON-safe V3 findings without treating missing evidence as success.

    ``route`` uses mm and defaults to pipe.original_route. Assessment does not
    mutate the layout or optimize routes. ``complete`` remains false when any
    rule needs review or fails; it never means certified engineering compliance.
    """
    _mapping(layout, "layout")
    if layout.get("units") != "mm":
        raise ValueError("assess_layout requires a normalized layout with units='mm'")
    if layout.get("system_type") not in ("pressurized", "gravity"):
        raise ValueError("layout.system_type must be pressurized or gravity")
    pipe = _mapping(layout.get("pipe"), "pipe")
    points = _points(pipe.get("original_route") if route is None else route, "route")
    engineering = _validate_engineering(layout)
    findings = [_shock(layout, points)]
    settings = directional_requirements(layout)
    slope_measurements = []
    if layout["system_type"] == "pressurized":
        slope_status, slope_message = "not_applicable", "Directional drainage slopes do not apply to the declared pressurized system."
    elif not settings["ready"]:
        slope_status, slope_message = "needs_review", settings["reason"]
    else:
        directed = points if settings["flow_direction"] == "start_to_end" else list(reversed(points))
        for i, (start, end) in enumerate(zip(directed, directed[1:])):
            minimum = directional_minimum_fall(layout, start, end)
            fall = start[2] - end[2]
            status = "fail" if fall < -LENGTH_TOLERANCE_MM else "needs_review" if minimum is None else "fail" if fall + LENGTH_TOLERANCE_MM < minimum else "pass"
            slope_measurements.append({"segment_in_flow_order": i, "actual_fall_mm": fall, "minimum_fall_mm": minimum, "horizontal_run_mm": math.hypot(end[0] - start[0], end[1] - start[1]), "status": status})
        slope_status = "fail" if any(item["status"] == "fail" for item in slope_measurements) else "needs_review" if any(item["status"] == "needs_review" for item in slope_measurements) else "pass"
        slope_message = "Checked fall along confirmed flow: 1:25 longitudinal and 1:100 transverse. Diagonal horizontal runs require a reviewed rule."
    findings.append(_finding("directional_drain_slope", slope_status, 2, slope_message, measured=slope_measurements,
                             required={"longitudinal_min_slope": 0.04, "transverse_min_slope": 0.01}, refs=engineering.get("source_refs", [])))

    exact_pitch_status = slope_status
    if layout["system_type"] == "gravity" and slope_status == "pass":
        if any(item["horizontal_run_mm"] > LENGTH_TOLERANCE_MM and abs(item["actual_fall_mm"] - item["minimum_fall_mm"]) > LENGTH_TOLERANCE_MM for item in slope_measurements):
            exact_pitch_status = "needs_review"
    findings.append(_finding("directional_drain_pitch", exact_pitch_status, 2,
                             "The brief specifies 1:25 and 1:100 pitches. A steeper segment passing the minimum-fall screen still needs review because no acceptable pitch tolerance is supplied.",
                             measured=slope_measurements, required={"longitudinal_pitch": 0.04, "transverse_pitch": 0.01, "pitch_tolerance": "not specified in brief"}))

    def inventory(group, identity, page, required, evaluate, severity="error"):
        findings.append(_inventory_finding(engineering, points, group, identity, page, required, evaluate, severity=severity))

    inventory("pieces", "piece_length", 2, {"maximum_mm": 6096}, lambda record: _limit(record, "centerline_length_mm", maximum=6096))

    def weld(record):
        values = record.get("weld_spacings_mm")
        if values is None:
            return "needs_review", None, "Supply measured distances between adjacent welds."
        return ("fail" if any(value + LENGTH_TOLERANCE_MM < 25 for value in values) else "pass" if values else "not_applicable"), values, "Only explicitly measured adjacent weld spacings were checked."
    inventory("pieces", "weld_spacing", 2, {"minimum_mm": 25}, weld)

    def bends(record):
        values = record.get("bends")
        if values is None:
            return "needs_review", None, "Identify physical bends in this manufactured piece."
        return ("pass" if len(values) <= 3 else "fail"), len(values), "Count is the declared physical bends, not sampled polyline corners."
    inventory("pieces", "bends_per_piece", 2, {"maximum": 3}, bends)
    bend_limits = bend_requirements(engineering.get("nominal_diameter_mm")) if engineering.get("nominal_size_basis_verified") else None

    def radius(record):
        values = record.get("bends")
        if values == []:
            return "not_applicable", [], "No physical bends declared for this piece."
        if values is None or bend_limits is None or any("radius_mm" not in item for item in values):
            return "needs_review", None, "Supply actual bend radii and a nominal size covered by the brief's table."
        measured = [item["radius_mm"] for item in values]
        return ("fail" if any(value + LENGTH_TOLERANCE_MM < bend_limits["min_radius_mm"] for value in measured) else "pass"), measured, "Measured physical centerline bend radii were compared with the size table."
    inventory("pieces", "bend_radius", 2, bend_limits or {"nominal_size_required": True}, radius)

    def straight(record):
        values = record.get("straight_between_bends_mm")
        bends = record.get("bends")
        if bends is not None and len(bends) < 2:
            return "not_applicable", [], "Fewer than two physical bends in this piece."
        if values is None or bends is None or bend_limits is None:
            return "needs_review", None, "Supply consecutive physical bends, intervening straight lengths and nominal pipe size."
        return ("fail" if any(value + LENGTH_TOLERANCE_MM < bend_limits["min_straight_between_bends_mm"] for value in values) else "pass"), values, "Measured tangent-to-tangent straight lengths were checked."
    inventory("pieces", "straight_between_bends", 2, bend_limits or {"nominal_size_required": True}, straight)

    def angle(record):
        values = record.get("bends")
        if values is None or any("angle_deg" not in item for item in values):
            return "needs_review", None, "Supply the angle of each physical bend."
        measured = [item["angle_deg"] for item in values]
        failed = any(abs(value - 5 * round(value / 5)) > ANGLE_TOLERANCE_DEG for value in measured)
        return ("fail" if failed else "pass" if values else "not_applicable"), measured, "Five-degree increments are a recommendation."
    inventory("pieces", "bend_angle_increment", 2, {"preferred_increment_degrees": 5}, angle, "preference")

    def planar(record):
        if "planarity_deviation_mm" not in record or "planarity_tolerance_mm" not in record:
            return "needs_review", None, "The brief says generally lay flat but provides no numerical tolerance. Supply an explicit reviewed tolerance."
        return ("pass" if record["planarity_deviation_mm"] <= record["planarity_tolerance_mm"] + LENGTH_TOLERANCE_MM else "fail"), {"deviation_mm": record["planarity_deviation_mm"], "reviewer_tolerance_mm": record["planarity_tolerance_mm"]}, "Tolerance is supplied by the reviewer, not specified in the brief."
    inventory("pieces", "piece_planarity", 2, {"reviewer_defined_tolerance_required": True}, planar, "preference")

    def hanger(record, orientation, limit):
        if "orientation" not in record:
            return "needs_review", None, "Specify hanger orientation and the applicable measurement convention."
        if record["orientation"] != orientation:
            return "not_applicable", None, f"This is not a {orientation} hanger."
        return _limit(record, "distance_mm", maximum=limit)
    inventory("hangers", "vertical_hanger_distance", 1, {"preferred_maximum_mm": 381, "measurement": "pipe center to structure; longer is allowed"}, lambda record: hanger(record, "vertical", 381), "preference")
    inventory("hangers", "horizontal_hanger_distance", 1, {"maximum_mm": 50.8, "measurement": "pipe edge to structure"}, lambda record: hanger(record, "horizontal", 50.8))
    inventory("walkways", "walkway_headroom", 1, {"minimum_mm": 1960}, lambda record: _limit(record, "headroom_mm", minimum=1960))

    def valve(record):
        limits = {"horizontal": (229, 1829), "vertical_up": (150, 1295), "vertical_down": (None, 1980)}
        if "orientation" not in record:
            return "needs_review", None, "Supply the valve stem orientation."
        low, high = limits[record["orientation"]]
        status, value, message = _limit(record, "height_above_deck_mm", minimum=low, maximum=high)
        return status, {"orientation": record["orientation"], "height_mm": value}, message
    inventory("valves", "valve_access_height", 1, {"horizontal_mm": [229, 1829], "vertical_up_mm": [150, 1295], "vertical_down_max_mm": 1980, "outside_range_allowed_but_not_recommended": True}, valve, "preference")

    evidence = engineering.get("evidence", {})
    descriptions = {
        "ship_boundary": (3, "Verify the complete pipe and fittings remain inside actual ship structure. The local search box is not a ship boundary."),
        "required_spaces": (2, "Verify the route passes through required spaces and preserves required connections and components."),
        "scene_coverage": (4, "Verify aligned structure, equipment, piping, ventilation and wireway surroundings. Local obstacle coverage cannot prove scene completeness."),
        "fitting_catalog": (3, "Verify each fitting identity, size, commercial availability and its physical occupied volume."),
        "component_sizes": (3, "Verify source component size identities and that no component size changed."),
    }
    for key, (page, message) in descriptions.items():
        record = evidence.get(key, {})
        verified = record.get("verified") is True and bool(record.get("source_refs"))
        # These assertions concern this particular assembly, not any future route.
        status = "pass" if verified and _bound(engineering, points) else "needs_review"
        findings.append(_finding(key, status, page, message, measured={"reviewer_verified": verified, "route_binding_matches": _bound(engineering, points)}, required={"reviewed_evidence_required": True}, refs=record.get("source_refs", [])))
    if layout.get("protected_endpoints"):
        findings.append(_finding("fixed_connection_identity", "needs_review", 3,
                                 "Fixed endpoint contact exemptions are user assumptions. Verify port identities and connection orientations before implementation.",
                                 measured={"assumed_connection_count": len(layout["protected_endpoints"])}, required={"verified_ports_and_orientations": True}))
    # Keep unimplemented brief requirements visible even when every supported
    # observation has been supplied. No metadata assertion implements a missing
    # geometric, component or system-function check.
    pending = [
        ("penetration_and_stiffener_clearance", 2,
         "Penetration and stiffener clearances need identified structural features and their actual clearance measurements.",
         "Review the penetration and stiffener requirements in the brief."),
        ("deck_bulkhead_offset", 2,
         "Distance from deck and bulkhead surfaces is not evaluated by a local obstacle envelope check.",
         {"minimum_offset": "one pipe diameter", "identified_surfaces_required": True}),
        ("physical_installability", 2,
         "Assembly installation, actual fitting volumes and fabrication feasibility have not been checked.",
         "Verify the physically realizable assembly and installation sequence."),
        ("navigation_and_trip_access", 1,
         "Passageway, ladder and trip hazards need identified access geometry. A headroom measurement alone does not establish safe access.",
         "Protect passageways, ladders and access from piping interference."),
        ("elbow_cutback_angles", 2,
         "Cut-back elbow angles and their permitted source fitting geometry have not been verified.",
         "Verify cut-back fittings against the brief and identified component geometry."),
        ("physical_routing_objective", 1,
         "Polyline length and sampled direction changes do not measure actual manufactured bends and elevation changes. The full optimization objective is not yet evaluated.",
         "Minimize required changes in direction and elevation using physical component identities."),
        ("valve_compartment_preservation", 2,
         "Valve access height checks do not establish that a moved valve remains in its original compartment.",
         "Preserve the valve's required compartment and connectivity."),
        ("required_component_preservation", 3,
         "The required valves, equipment and branch connections have not been verified as a complete functioning system.",
         "Retain required components and their functional connections."),
    ]
    for identity, page, message, required in pending:
        findings.append(_finding(identity, "needs_review", page, message, required=required,
                                 severity="preference" if identity == "elbow_cutback_angles" else "error"))
    counts = {status: sum(item["status"] == status for item in findings) for status in STATUSES}
    hard_failures = [item["id"] for item in findings if item["status"] == "fail" and item["severity"] == "error"]
    return {"profile": PROFILE_ID, "source_document": DOCUMENT, "units": "mm", "findings": findings,
            "counts": counts, "hard_failures": hard_failures, "complete": counts["needs_review"] == 0 and counts["fail"] == 0,
            "engineering_compliance_certified": False,
            "measurement_binding_matches": _bound(engineering, points),
            "tolerances": {"length_mm": LENGTH_TOLERANCE_MM, "angle_degrees": ANGLE_TOLERANCE_DEG},
            "limitations": ["Checks cover supplied geometry and source-referenced declarations only.",
                            "Passing these checks does not certify engineering compliance or fitness for installation.",
                            "Centerline corner counts are not treated as physical fitting counts."]}
