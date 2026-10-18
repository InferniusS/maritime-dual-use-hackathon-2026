"""Single-pipe workflow with explicit units and provisional engineering rules.

The imported geometry remains distinguishable from synthetic examples. Passing
these checks is not a claim of standards compliance or fitting availability.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any

from . import core


UNIT_SCALE = {"mm": 1.0, "m": 1000.0, "in": 25.4}
DEFAULT_RULES_MM = {"clearance": 50.0, "grid_step": 250.0, "bend_penalty": 300.0}
MAX_GRAVITY_AXIS_COORDINATES = 64
MAX_ENDPOINT_STUB_MM = 1000.0


def _with_rule_profile(layout: dict) -> dict:
    """Apply the same explicit profile defaults to routing and validation."""
    core._mapping(layout, "layout")
    supplied = deepcopy(layout)
    profile = supplied.get("rule_profile", "prototype-unverified")
    if profile not in ("prototype-unverified", "fmg-v3"):
        raise ValueError("Unknown rule_profile. Choose prototype-unverified or fmg-v3")
    if profile == "fmg-v3":
        rules = core._mapping(supplied.setdefault("rules", {}), "rules")
        units = supplied.get("units", "mm")
        if not isinstance(units, str) or units not in UNIT_SCALE:
            raise ValueError("units must be mm, m, or in")
        scale = UNIT_SCALE[units]
        if "clearance" not in rules:
            rules["clearance"] = 50.8 / scale
        if core._number(rules["clearance"], "clearance") * scale < 50.8 - core.EPSILON:
            raise ValueError("The V3 challenge profile requires at least 50.8 mm shock clearance")
    return supplied


def normalize_layout(layout: dict) -> dict:
    """Return a separate millimeter copy, scaling every supported length field.

    Missing units explicitly mean provisional millimeters. Missing rule values
    are provisional defaults in millimeters, regardless of the input unit.
    """
    core._mapping(layout, "layout")
    result = deepcopy(layout)
    if "proposal" in result:
        proposal = core._mapping(result["proposal"], "proposal")
        if proposal.get("mode") != "rounded_pipe":
            raise ValueError("proposal.mode must be rounded_pipe")
        if "bend_radius_mm" in proposal:
            core._number(proposal["bend_radius_mm"], "proposal.bend_radius_mm", positive=True)
    source_units = result.get("units", "mm")
    if not isinstance(source_units, str) or source_units not in UNIT_SCALE:
        raise ValueError("units must be mm, m, or in")
    if result.get("provenance") not in ("synthetic", "cad-derived"):
        raise ValueError("provenance must be synthetic or cad-derived")
    if result.get("system_type") not in ("pressurized", "gravity"):
        raise ValueError("system_type must be pressurized or gravity")
    pipe = core._mapping(result.get("pipe"), "pipe")
    if result.get("fixed_waypoints") or pipe.get("fixed_waypoints"):
        raise ValueError("Fixed intermediate waypoints are not supported by this single-pipe workflow")
    assumptions = result.setdefault("assumptions", [])
    if not isinstance(assumptions, list) or not all(isinstance(item, str) for item in assumptions):
        raise ValueError("assumptions must be a list of strings")
    if "units" not in layout:
        assumptions.append("Drawing units were not supplied. Millimeters are provisionally assumed and need verification.")
    scale = UNIT_SCALE[source_units]

    def point(value: Any, label: str) -> list[float]:
        return [coordinate * scale for coordinate in core._point(value, label)]

    for key in ("start", "end"):
        pipe[key] = point(pipe.get(key), f"pipe.{key}")
    original = pipe.get("original_route")
    if not isinstance(original, (list, tuple)) or not 2 <= len(original) <= core.MAX_ROUTE_POINTS:
        raise ValueError(f"pipe.original_route needs 2 to {core.MAX_ROUTE_POINTS} points")
    pipe["original_route"] = [point(item, f"pipe.original_route[{i}]") for i, item in enumerate(original)]
    pipe["diameter"] = core._number(pipe.get("diameter"), "pipe.diameter", positive=True) * scale
    bounds = core._mapping(result.get("bounds"), "bounds")
    for key in ("min", "max"):
        bounds[key] = point(bounds.get(key), f"bounds.{key}")
    obstacles = result.get("obstacles")
    if not isinstance(obstacles, list) or len(obstacles) > core.MAX_OBSTACLES:
        raise ValueError(f"obstacles must be a list with at most {core.MAX_OBSTACLES} boxes")
    for i, obstacle in enumerate(obstacles):
        core._mapping(obstacle, f"obstacles[{i}]")
        for key in ("min", "max"):
            obstacle[key] = point(obstacle.get(key), f"obstacles[{i}].{key}")
        if "capsules" in obstacle:
            capsules = obstacle["capsules"]
            if not isinstance(capsules, list) or not 1 <= len(capsules) <= 256:
                raise ValueError("A pipe obstacle needs 1 to 256 capsule envelopes")
            for capsule in capsules:
                core._mapping(capsule, "obstacle capsule")
                for key in ("start", "end"):
                    capsule[key] = point(capsule.get(key), f"capsule.{key}")
                for key in ("clip_min", "clip_max"):
                    if key in capsule:
                        capsule[key] = point(capsule[key], f"capsule.{key}")
                capsule["radius"] = core._number(capsule.get("radius"), "capsule.radius", positive=True) * scale
    rules = core._mapping(result.setdefault("rules", {}), "rules")
    for key in (*DEFAULT_RULES_MM, "bend_radius"):
        if key in rules:
            rules[key] = core._number(rules[key], f"rules.{key}") * scale
        elif key in DEFAULT_RULES_MM:
            rules[key] = DEFAULT_RULES_MM[key]
            assumptions.append(f"Default {key} of {rules[key]:g} mm is a prototype assumption.")
    if "bend_radius" in rules and rules["bend_radius"] <= 0:
        raise ValueError("rules.bend_radius must be greater than zero")
    if result["system_type"] == "gravity":
        if "min_slope" not in rules:
            rules["min_slope"] = 0.01
            assumptions.append("Minimum drain slope of 0.01 is a prototype assumption, not a verified engineering standard.")
        rules["min_slope"] = core._number(rules["min_slope"], "rules.min_slope", positive=True)
    result["units"] = "mm"
    protected = result.get("protected_endpoints", [])
    if not isinstance(protected, list) or len(protected) > 2:
        raise ValueError("protected_endpoints must contain at most one start and one end record")
    seen_endpoints = set()
    obstacle_ids = {obstacle.get("id") for obstacle in obstacles}
    for record in protected:
        core._mapping(record, "protected endpoint")
        endpoint = record.get("endpoint")
        if endpoint not in ("start", "end") or endpoint in seen_endpoints:
            raise ValueError("Protected endpoints must use unique start or end labels")
        seen_endpoints.add(endpoint)
        raw_stub = record.get("points")
        if not isinstance(raw_stub, list) or not 2 <= len(raw_stub) <= len(pipe["original_route"]):
            raise ValueError("Each protected endpoint needs at least two fixed points from the original route")
        record["points"] = [point(item, "protected endpoint point") for item in raw_stub]
        record["max_distance"] = core._number(record.get("max_distance"), "protected endpoint max_distance", positive=True) * scale
        if record["max_distance"] > MAX_ENDPOINT_STUB_MM:
            raise ValueError(f"Endpoint contact assumptions are limited to {MAX_ENDPOINT_STUB_MM:g} mm")
        identifiers = record.get("obstacle_ids")
        if not isinstance(identifiers, list) or not all(isinstance(item, str) and item in obstacle_ids for item in identifiers):
            raise ValueError("Protected endpoint obstacle_ids must name existing obstacles")
        if any(not _connection_obstacle_allowed(result, identifier) for identifier in identifiers):
            raise ValueError("Protected access or structure obstacles cannot be treated as pipe connections")
        expected = pipe["original_route"][:len(raw_stub)] if endpoint == "start" else pipe["original_route"][-len(raw_stub):]
        if record["points"] != expected:
            raise ValueError("Protected endpoint points must exactly preserve the original route prefix or suffix")
        stub_length = sum(math.dist(a, b) for a, b in zip(record["points"], record["points"][1:]))
        if stub_length > record["max_distance"] + core.EPSILON:
            raise ValueError("The protected connection exceeds its bounded endpoint distance")
        origin = pipe[endpoint]
        margin = pipe["diameter"] / 2 + rules["clearance"]
        for identifier in identifiers:
            obstacle = next(item for item in obstacles if item["id"] == identifier)
            shape = core._prepare_obstacle(obstacle, "endpoint obstacle", margin)
            if not core._segment_intersects_box(tuple(origin), tuple(origin), shape):
                raise ValueError("An endpoint connection assumption must refer to an obstacle that touches that endpoint")
    protected_segments = sum(len(record["points"]) - 1 for record in protected)
    if protected and protected_segments >= len(pipe["original_route"]) - 1:
        raise ValueError("Protected endpoint stubs must leave a movable middle section")
    # Core's proven geometry preparation is reused without changing its public
    # synthetic-only contract. Public metadata never uses this private adapter.
    core._prepare_layout(_core_adapter(result))
    return result


def _connection_obstacle_allowed(layout, identifier):
    if not identifier.startswith("context:"):
        return True
    sources = layout.get("model_context", {}).get("obstacle_sources", [])
    source = next((item for item in sources if item.get("id") == identifier), {})
    return not identifier.startswith("context:keepout:") and source.get("category") in ("piping", "equipment")


def _core_adapter(layout: dict) -> dict:
    return {**layout, "provenance": "synthetic", "system_type": "pressurized"}


def _route_at_distance(points, distance):
    travelled = 0.0
    for start, end in zip(points, points[1:]):
        length = math.dist(start, end)
        if length and travelled + length >= distance:
            fraction = (distance - travelled) / length
            return [a + (b - a) * fraction for a, b in zip(start, end)]
        travelled += length
    return list(points[-1])


def _connection_exit_distance(points, boxes, maximum):
    intervals, travelled = [], 0.0
    for start, end in zip(points, points[1:]):
        length = math.dist(start, end)
        for box in boxes:
            for interval in core._segment_obstacle_intervals(start, end, box):
                intervals.append((travelled + interval[0] * length, travelled + interval[1] * length))
        travelled += length
    end_of_contact = 0.0
    next_contact = travelled
    for low, high in sorted(intervals):
        if low > end_of_contact + core.EPSILON:
            next_contact = low
            break
        end_of_contact = max(end_of_contact, high)
    if end_of_contact >= travelled - core.EPSILON:
        raise ValueError("The selected connector covers the whole original route. No free connection stub endpoint exists")
    exit_distance = end_of_contact + min(0.001, (next_contact - end_of_contact) / 2)
    if exit_distance > maximum:
        raise ValueError(f"The original path does not exit the selected connector within the {maximum:g} mm connection stub limit")
    return exit_distance


def with_endpoint_connections(layout: dict, endpoint_connections: dict | None, max_stub_length: float = 250.0) -> dict:
    """Preserve bounded original stubs for explicit connector contact assumptions.

    Every obstacle remains present. The movable route receives no collision
    exemptions. These choices are geometric prototype assumptions, not a
    declaration that a real equipment port or fitting has been verified.
    """
    if endpoint_connections is None:
        endpoint_connections = {}
    if not isinstance(endpoint_connections, dict) or any(key not in ("start", "end") for key in endpoint_connections):
        raise ValueError("endpoint_connections must map start or end to obstacle ID lists")
    maximum = core._number(max_stub_length, "max_stub_length", positive=True)
    if maximum > MAX_ENDPOINT_STUB_MM:
        raise ValueError(f"max_stub_length must not exceed {MAX_ENDPOINT_STUB_MM:g} mm")
    result = normalize_layout(layout)
    if not endpoint_connections:
        return result
    original = result["pipe"]["original_route"]
    prepared = core._prepare_layout(_core_adapter(result))
    box_lookup = {box.id: box for box in prepared.obstacles}
    total = sum(math.dist(a, b) for a, b in zip(original, original[1:]))
    requests = {}
    for endpoint, identifiers in endpoint_connections.items():
        if not isinstance(identifiers, list) or not all(isinstance(item, str) and item in box_lookup for item in identifiers):
            raise ValueError("Endpoint connections must name existing obstacle IDs")
        if any(not _connection_obstacle_allowed(result, identifier) for identifier in identifiers):
            raise ValueError("Protected access or structure obstacles cannot be treated as pipe connections")
        if not identifiers:
            continue
        directed = original if endpoint == "start" else list(reversed(original))
        selected = [box_lookup[identifier] for identifier in dict.fromkeys(identifiers)]
        if not all(core._segment_intersects_box(tuple(directed[0]), tuple(directed[0]), box) for box in selected):
            raise ValueError("Every selected endpoint connector must touch that original endpoint")
        distance = _connection_exit_distance(directed, selected, maximum)
        requests[endpoint] = {"distance": distance, "obstacle_ids": list(dict.fromkeys(identifiers))}
    if not requests:
        return result
    start_distance = requests.get("start", {}).get("distance", 0.0)
    end_distance = total - requests.get("end", {}).get("distance", 0.0)
    if start_distance >= end_distance - core.EPSILON:
        raise ValueError("The fixed connection stubs overlap. Select a longer run or fewer endpoint assumptions")
    distances = [0.0]
    for a, b in zip(original, original[1:]):
        distances.append(distances[-1] + math.dist(a, b))
    positions = sorted(set(distances + [start_distance, end_distance]))
    expanded = [_route_at_distance(original, position) for position in positions]
    # Keep all original coordinates exactly. Only anchor points are inserted.
    for position, point in zip(distances, original):
        expanded[positions.index(position)] = list(point)
    start_index, end_index = positions.index(start_distance), positions.index(end_distance)
    records = []
    for endpoint, request in requests.items():
        points = expanded[:start_index + 1] if endpoint == "start" else expanded[end_index:]
        records.append({"endpoint": endpoint, "points": deepcopy(points), "obstacle_ids": request["obstacle_ids"], "max_distance": maximum})
    result["pipe"]["original_route"] = expanded
    result["protected_endpoints"] = records
    result["assumptions"].append("Explicit endpoint connector contacts are accepted only on bounded unchanged original connection stubs. Equipment ports and connection identities remain unverified.")
    if isinstance(result.get("cad_context"), dict):
        result["cad_context"]["endpoint_connection_assumptions"] = deepcopy(records)
    return normalize_layout(result)


def _corner_angles(points: list[core.Point]) -> list[float]:
    angles = []
    for before, corner, after in zip(points, points[1:], points[2:]):
        incoming = tuple(b - a for a, b in zip(before, corner))
        outgoing = tuple(b - a for a, b in zip(corner, after))
        length = math.dist(before, corner) * math.dist(corner, after)
        cosine = sum(a * b for a, b in zip(incoming, outgoing)) / length if length else 1.0
        angles.append(math.acos(max(-1.0, min(1.0, cosine))))
    return angles


def _fitting_issues(points: list[core.Point], radius: float | None) -> list[dict]:
    if radius is None:
        return []
    allowances = [0.0]
    issues = []
    for index, angle in enumerate(_corner_angles(points), 1):
        if angle >= math.pi - 1e-8:
            issues.append({"code": "bend_reversal", "point": index, "message": "A reversing corner cannot use the assumed circular bend radius"})
            allowances.append(math.inf)
        else:
            allowances.append(radius * math.tan(angle / 2))
    allowances.append(0.0)
    for segment, (start, end) in enumerate(zip(points, points[1:])):
        required = allowances[segment] + allowances[segment + 1]
        if math.dist(start, end) + core.EPSILON < required:
            issues.append({
                "code": "insufficient_bend_space", "segment": segment,
                "message": "This leg is too short for the tangent lengths of the assumed bend radius",
                "required_length_mm": required if math.isfinite(required) else None,
            })
    return issues


def _validate_normalized(layout: dict, raw_points: Any, *, allow_pressure_polyline: bool = False) -> dict:
    prepared = core._prepare_layout(_core_adapter(layout))
    if layout["system_type"] == "pressurized" and not allow_pressure_polyline:
        result = core._validate(prepared, raw_points)
    else:
        result = core._validate(prepared, raw_points)
        # Sloped drain segments and existing CAD polylines are intentional.
        # All other core checks, including continuous slab collisions, apply.
        result["issues"] = [item for item in result["issues"] if item["code"] != "non_orthogonal"]
    if any(item["code"] == "invalid_points" for item in result["issues"]):
        return result
    points = [core._point(point, "route point") for point in raw_points]
    exemptions = {}
    for record in layout.get("protected_endpoints", []):
        stub = [tuple(point) for point in record["points"]]
        is_start = record["endpoint"] == "start"
        actual = points[:len(stub)] if is_start else points[-len(stub):]
        if actual != stub:
            result["issues"].append({"code": "protected_endpoint_changed", "endpoint": record["endpoint"], "message": "The fixed original endpoint connection stub must be preserved exactly"})
            continue
        first_segment = 0 if is_start else len(points) - len(stub)
        for segment in range(first_segment, first_segment + len(stub) - 1):
            exemptions[segment] = set(record["obstacle_ids"])
    waived_contacts = [issue for issue in result["issues"] if issue["code"] == "obstacle_collision" and issue.get("obstacle") in exemptions.get(issue.get("segment"), set())]
    result["issues"] = [issue for issue in result["issues"] if issue not in waived_contacts]
    if layout.get("protected_endpoints"):
        result["endpoint_contact_assumptions_applied"] = waived_contacts
    if layout["system_type"] == "gravity":
        slopes = []
        for segment, (start, end) in enumerate(zip(points, points[1:])):
            horizontal = math.hypot(end[0] - start[0], end[1] - start[1])
            fall = start[2] - end[2]
            if fall < -core.EPSILON:
                result["issues"].append({"code": "uphill_flow", "segment": segment, "message": "Gravity flow rises on this segment in the fixed start-to-end direction"})
            if horizontal > core.EPSILON:
                slope = fall / horizontal
                slopes.append(slope)
                if fall + core.EPSILON < layout["rules"]["min_slope"] * horizontal:
                    result["issues"].append({"code": "insufficient_slope", "segment": segment, "message": "This segment does not meet the provisional minimum fall per horizontal distance", "actual_slope": slope})
        result["minimum_segment_slope"] = min(slopes) if slopes else None
    if layout.get("model_context"):
        from .model_context import validate_allowed_spaces
        result["issues"].extend({"code": item.get("rule_id", "allowed_spaces"), **item}
                                for item in validate_allowed_spaces(layout, raw_points) if item["status"] == "fail")
    if layout.get("rule_profile") == "fmg-v3" and layout["system_type"] == "gravity":
        from .challenge_rules import directional_minimum_fall
        for index, (start, end) in enumerate(zip(points, points[1:])):
            required = directional_minimum_fall(layout, start, end)
            if required is not None and start[2] - end[2] + core.EPSILON < required:
                result["issues"].append({"code": "challenge_drain_slope", "segment": index,
                    "message": "Drain segment does not meet the V3 slope in this ship direction", "required_fall_mm": required})
    result["bends"] = sum(angle > 1e-7 for angle in _corner_angles(points))
    result["issues"].extend(_fitting_issues(points, layout["rules"].get("bend_radius")))
    result["valid"] = not result["issues"]
    return result


def validate_layout_route(layout: dict, points: list) -> dict:
    """Validate points in the layout's input units. Report lengths in mm."""
    layout = _with_rule_profile(layout)
    if layout.get("model_context"):
        from .model_context import apply_model_context
        normalized = apply_model_context(layout, layout["model_context"])
    else:
        normalized = normalize_layout(layout)
    if not isinstance(points, (list, tuple)) or not 2 <= len(points) <= core.MAX_ROUTE_POINTS:
        return _validate_normalized(normalized, points)
    try:
        scale = UNIT_SCALE[layout.get("units", "mm")]
        converted = [[value * scale for value in core._point(point, "route point")] for point in points]
    except ValueError as error:
        return {"valid": False, "issues": [{"code": "invalid_points", "message": str(error)}], "length": 0.0, "bends": 0}
    return _validate_normalized(normalized, converted, allow_pressure_polyline=normalized["provenance"] == "cad-derived")


def _gravity_axis(prepared: core._Layout, axis: int) -> list[float]:
    low, high = prepared.interior.minimum[axis], prepared.interior.maximum[axis]
    # The tiny extra distance deliberately avoids a closed obstacle boundary.
    padding = max(1e-5, prepared.grid_step * 1e-6)
    values = set(core._axis_coordinates(prepared, axis))
    for obstacle in prepared.obstacles:
        values.update((obstacle.minimum[axis] - padding, obstacle.maximum[axis] + padding))
    values = sorted(value for value in values if low <= value <= high)
    if len(values) > MAX_GRAVITY_AXIS_COORDINATES:
        selected = {values[round(i * (len(values) - 1) / (MAX_GRAVITY_AXIS_COORDINATES - 3))] for i in range(MAX_GRAVITY_AXIS_COORDINATES - 2)}
        selected.update((prepared.start[axis], prepared.end[axis]))
        values = sorted(selected)
    return values


def _gravity_search(layout: dict, *, objective_penalty: float | None = None) -> tuple[list | None, dict]:
    prepared = core._prepare_layout(_core_adapter(layout))
    start, end = prepared.start, prepared.end
    search = {"method": "bounded_uniform_slope_candidates", "evaluated_candidates": 0, "feasible_candidates": 0, "max_horizontal_turns": 3, "expanded_states": 0, "grid_nodes": 0}
    if not all(core._inside(point, prepared.interior) for point in (start, end)):
        search["reason"] = "A fixed endpoint does not fit within the supplied bounds and clearance"
        return None, search
    fall = start[2] - end[2]
    minimum_horizontal = math.hypot(end[0] - start[0], end[1] - start[1])
    if fall + core.EPSILON < minimum_horizontal * layout["rules"]["min_slope"] or fall < 0:
        search["reason"] = "The fixed endpoint fall is insufficient even for the straight horizontal distance under the selected minimum slope"
        return None, search
    x_values, y_values = _gravity_axis(prepared, 0), _gravity_axis(prepared, 1)
    search["grid_nodes"] = len(x_values) * len(y_values)
    s, e = start[:2], end[:2]

    def paths():
        if s == e:
            yield [s, e]
        yield [s, (e[0], s[1]), e]
        yield [s, (s[0], e[1]), e]
        for x in x_values:
            yield [s, (x, s[1]), (x, e[1]), e]
        for y in y_values:
            yield [s, (s[0], y), (e[0], y), e]
        for x in x_values:
            for y in y_values:
                yield [s, (x, s[1]), (x, y), (e[0], y), e]
                yield [s, (s[0], y), (x, y), (x, e[1]), e]

    seen = set()
    best, best_cost = None, math.inf
    for raw_path in paths():
        path = []
        for point in raw_path:
            if path and point == path[-1]:
                continue
            if len(path) > 1:
                before, last = path[-2], path[-1]
                a = (last[0] - before[0], last[1] - before[1])
                b = (point[0] - last[0], point[1] - last[1])
                if a[0] * b[1] == a[1] * b[0] and a[0] * b[0] + a[1] * b[1] > 0:
                    path[-1] = point
                    continue
            path.append(point)
        key = tuple(path)
        if key in seen:
            continue
        seen.add(key)
        horizontal = sum(math.dist(a, b) for a, b in zip(path, path[1:]))
        required_falls = None
        if layout.get("rule_profile") == "fmg-v3":
            from .challenge_rules import directional_minimum_fall
            values = [directional_minimum_fall(layout, [a[0], a[1], 0], [b[0], b[1], 0]) for a, b in zip(path, path[1:])]
            if values and all(v is not None for v in values):
                required_falls = values
                if sum(values) > fall + core.EPSILON:
                    continue
        if fall + core.EPSILON < horizontal * layout["rules"]["min_slope"]:
            continue
        if horizontal == 0:
            points = [list(start), list(end)]
        else:
            travelled = 0.0
            points = [list(start)]
            accumulated_fall = 0.0
            for index, (a, b) in enumerate(zip(path, path[1:])):
                distance = math.dist(a, b)
                travelled += distance
                if required_falls is not None:
                    accumulated_fall += required_falls[index] + max(0, fall - sum(required_falls)) * distance / horizontal
                    z = start[2] - accumulated_fall
                else:
                    z = start[2] - fall * travelled / horizontal
                points.append([b[0], b[1], z])
            points[-1] = list(end)
        search["evaluated_candidates"] += 1
        record = _validate_normalized(layout, points)
        if record["valid"]:
            search["feasible_candidates"] += 1
            penalty = prepared.bend_penalty if objective_penalty is None else objective_penalty
            cost = record["length"] + penalty * record["bends"]
            if cost < best_cost:
                best, best_cost = points, cost
    search["reason"] = "Found a checked route among bounded uniform-slope candidates" if best is not None else "No route found by this bounded uniform-slope search. Other routes may exist"
    return best, search


def _run_without_protected_search(layout: dict) -> dict:
    """Route a single pipe and retain truthful provenance and check coverage."""
    normalized = normalize_layout(layout)
    cad_derived = normalized["provenance"] == "cad-derived"
    original_points = normalized["pipe"]["original_route"]
    original = {"points": original_points, **_validate_normalized(normalized, original_points, allow_pressure_polyline=cad_derived)}
    if cad_derived:
        original["route_representation"] = normalized["pipe"].get("route_representation", "cad_centerline_polyline")
        original["bend_count_kind"] = "polyline_direction_changes_not_fitting_count"
    if normalized["system_type"] == "pressurized":
        if cad_derived or normalized.get("model_context"):
            search_layout = {**normalized, "rules": {**normalized["rules"], "bend_penalty": 0.0 if cad_derived else normalized["rules"]["bend_penalty"]}}
            from .model_context import prepare_allowed_space_predicate
            route, search = core._search(core._prepare_layout(_core_adapter(search_layout)),
                                        segment_filter=prepare_allowed_space_predicate(normalized))
            points = [list(point) for point in route] if route is not None else None
            method = "CPU A* on a finite orthogonal 3D grid. " + (
                "CAD-derived route comparisons use length only because sampled direction changes are not reliable fitting counts."
                if cad_derived else "The synthetic test uses length and the selected bend penalty, with declared allowed spaces enforced."
            )
        else:
            solved = core.solve_layout(_core_adapter(normalized))
            points = solved["candidate"]["points"] if solved["candidate"] else None
            search = solved["search"]
            method = solved["method"]
    else:
        points, search = _gravity_search(normalized, objective_penalty=0.0 if cad_derived else None)
        method = "CPU bounded route search with up to three horizontal turns. Height follows cumulative horizontal distance. Every candidate is checked continuously in 3D."
    candidate = {"points": points, **_validate_normalized(normalized, points)} if points is not None else None
    if candidate is not None:
        candidate["route_representation"] = "proposed_centerline_polyline"
        candidate["bend_count_kind"] = "centerline_direction_changes_not_fitting_count"
    if candidate is not None and not candidate["valid"]:
        search["rejected_candidate_issues"] = candidate["issues"]
        search["reason"] = "The search candidate failed additional workflow checks. No accepted replacement was found by this search"
        candidate = None
    status = "improved" if candidate is not None else "no_route"
    if original["valid"]:
        penalty = 0.0 if cad_derived else normalized["rules"]["bend_penalty"]
        original_cost = original["length"] + penalty * original["bends"]
        candidate_cost = math.inf if candidate is None else candidate["length"] + penalty * candidate["bends"]
        if candidate_cost >= original_cost - core.EPSILON:
            candidate = deepcopy(original)
            status = "unchanged"
    assumptions = list(normalized["assumptions"])
    cad_context = normalized.get("cad_context", {})

    def context_strings(key: str) -> list[str]:
        value = cad_context.get(key, []) if isinstance(cad_context, dict) else []
        return [item for item in value if isinstance(item, str)] if isinstance(value, (list, tuple)) else []

    if isinstance(cad_context, dict):
        assumptions.extend(context_strings("assumptions"))
    assumptions.extend([
        "All coordinates and lengths in this result are millimeters. Input dimensions were converted using the declared input unit.",
        "Selected rules are an unverified prototype profile, not an official engineering standard.",
        "Pipe outer diameter and fixed endpoint positions are preserved. The supplied diameter is treated as outer diameter.",
        "Pipe radius plus selected clearance is applied to every obstacle and boundary face.",
        "Obstacle envelopes include their interior. Supported surface capsules include the pipe radius and selected clearance. Other components keep their supplied exclusion boxes.",
    ])
    limitations = [
        "Passing the implemented checks does not establish compliance with engineering standards or fitness for installation.",
        "Only the supplied obstacle envelopes and rectangular boundary are checked. Missing structure, equipment, and other pipes remain unchecked.",
        "One pipe is routed at a time. Branch connectivity, intermediate valves, simultaneous routing, supports, and connection orientation are not modeled.",
        "Standard commercially available fittings have not been selected or verified. A designer must review every bend and connection.",
        "No flow, pressure, structural, or full engineering compliance calculation is performed.",
    ]
    if normalized["provenance"] == "synthetic":
        limitations.append("This layout is a synthetic test case. It is not an imported ship design.")
    else:
        limitations.append("This layout is marked CAD-derived. Extraction fidelity, declared units, component identity, and completeness require source-CAD review.")
        assumptions.append("CAD-derived route comparisons use polyline length only. Sampled direction changes are not counted as verified physical fittings or used as bend penalties.")
        limitations.append("Existing CAD centerlines are checked as the supplied straight polyline segments without snapping or straightening. Curved geometry between samples is not checked exactly.")
        limitations.append("The original route's direction-change count describes the supplied polyline samples. It is not a count of elbows or other fittings.")
        if isinstance(cad_context, dict):
            for key in ("limitations", "warnings", "source_warnings"):
                limitations.extend(context_strings(key))
    if normalized["system_type"] == "gravity":
        assumptions.append("Gravity flows from pipe.start to pipe.end. The selected minimum slope is fall divided by horizontal distance.")
        assumptions.append("The model uses Z as the upward direction. CAD coordinate orientation must be checked before interpreting drain slope.")
        limitations.append("Gravity search covers bounded XY candidates with at most three turns and a uniform slope. It can miss valid routes with variable slopes or more turns.")
    else:
        limitations.append("Pressure search uses a finite orthogonal grid. It does not establish a globally optimal continuous route.")
    if "bend_radius" in normalized["rules"]:
        limitations.append("The optional bend radius only checks available tangent lengths. Curved bend volumes and fitting catalog dimensions are not collision-checked.")
        if normalized["system_type"] == "pressurized":
            limitations.append("Pressure search does not optimize around bend-radius failures. It may miss another route that has sufficient fitting space.")
    else:
        limitations.append("Bend radius and fitting space have not been checked because no bend radius was supplied.")
    return {
        "status": status, "original": original, "candidate": candidate,
        "units": "mm", "input_units": layout.get("units", "mm"),
        "provenance": normalized["provenance"], "system_type": normalized["system_type"],
        "rule_profile": "prototype-unverified", "normalized_layout": normalized,
        "comparison_objective": "polyline_length_only" if cad_derived else "length_plus_direction_change_penalty",
        "cad_context": deepcopy(cad_context),
        "assumptions": list(dict.fromkeys(assumptions)), "limitations": list(dict.fromkeys(limitations)),
        "method": method, "search": search,
        "checks": {
            "fixed_endpoints": True, "outer_diameter_preserved": True,
            "continuous_segment_collision": True, "supplied_box_bounds": True,
            "gravity_slope": normalized["system_type"] == "gravity",
            "bend_tangent_space": "bend_radius" in normalized["rules"],
            "fitting_catalog_compliance": False, "complete_ship_geometry": False,
            "official_standards_compliance": False,
        },
    }


def _explain_obstacles(layout: dict, result: dict) -> dict:
    """Attach source identity and distinguish a blocked anchor from a failed search."""
    context = layout.get("cad_context", {})
    sources = {item["id"]: item for item in context.get("obstacle_sources", [])} if isinstance(context, dict) else {}
    shapes = {item["id"]: item for item in layout["obstacles"]}

    def describe(record):
        identifier = record.get("obstacle")
        source = sources.get(identifier, {})
        record["obstacle_method"] = "supported_surface_capsules" if "capsules" in shapes.get(identifier, {}) else "sample_box"
        if source.get("source_handle"):
            record["source_handle"] = source["source_handle"]
            record["instance_path"] = source.get("instance_path", [])
            if "message" in record:
                record["message"] += f" (source {source['source_handle']})"
        return record

    for name in ("original", "candidate"):
        if result.get(name):
            for issue in result[name]["issues"]:
                if issue.get("obstacle"):
                    describe(issue)
    blocked = [describe(dict(item)) for item in result["search"].get("blocked_endpoints", [])]
    result["search"]["blocked_endpoints"] = blocked
    result["checks"]["pipe_shaped_obstacles"] = sum("capsules" in item for item in layout["obstacles"])
    result["checks"]["fallback_box_obstacles"] = sum("capsules" not in item for item in layout["obstacles"])
    diagnostics = []
    if result["status"] == "no_route" and blocked:
        for item in blocked:
            source = item.get("source_handle", item["obstacle"])
            method = "pipe envelope" if item["obstacle_method"] == "supported_surface_capsules" else "obstacle box"
            location = ", ".join(f"{value:.3f}" for value in item["point"])
            endpoint = ("movable " if layout.get("protected_endpoints") else "fixed ") + item["endpoint"]
            diagnostics.append({**item, "stage": "before_search", "code": "blocked_endpoint",
                "message": f"Search did not start. The {endpoint} point at [{location}] mm touches the {method} for source {source}. Review that component and the selected clearance."})
        result["search"]["reason"] += ". " + diagnostics[0]["message"]
    result["diagnostics"] = diagnostics
    return result


def _run_geometry_workflow(layout: dict) -> dict:
    """Run the route search while preserving any explicit endpoint stubs."""
    normalized = normalize_layout(layout)
    protected = normalized.get("protected_endpoints", [])
    if not protected:
        return _explain_obstacles(normalized, _run_without_protected_search(layout))
    original_points = normalized["pipe"]["original_route"]
    start_record = next((item for item in protected if item["endpoint"] == "start"), None)
    end_record = next((item for item in protected if item["endpoint"] == "end"), None)
    start_index = len(start_record["points"]) - 1 if start_record else 0
    end_index = len(original_points) - len(end_record["points"]) if end_record else len(original_points) - 1
    middle = deepcopy(original_points[start_index:end_index + 1])
    inner = deepcopy(normalized)
    inner.pop("protected_endpoints", None)
    inner["pipe"].update({"start": middle[0], "end": middle[-1], "original_route": middle})
    if inner.get("model_context", {}).get("required_space_ids"):
        from .model_context import validate_allowed_spaces
        visited = set()
        for record in protected:
            visited.update(f["space_id"] for f in validate_allowed_spaces(normalized, record["points"])
                           if f["rule_id"] == "required_space" and f["status"] == "pass")
        inner["model_context"]["required_space_ids"] = [identity for identity in inner["model_context"]["required_space_ids"] if identity not in visited]
    result = _run_without_protected_search(inner)
    cad_derived = normalized["provenance"] == "cad-derived"
    original = {"points": original_points, **_validate_normalized(normalized, original_points, allow_pressure_polyline=cad_derived)}
    if cad_derived:
        original.update({"route_representation": normalized["pipe"].get("route_representation", "cad_centerline_polyline"), "bend_count_kind": "polyline_direction_changes_not_fitting_count"})
    candidate = result.get("candidate")
    if candidate is not None:
        prefix = start_record["points"][:-1] if start_record else []
        suffix = end_record["points"][1:] if end_record else []
        points = deepcopy(prefix) + candidate["points"] + deepcopy(suffix)
        candidate = {**candidate, "points": points, **_validate_normalized(normalized, points, allow_pressure_polyline=cad_derived)}
        if not candidate["valid"]:
            result["search"]["rejected_candidate_issues"] = candidate["issues"]
            candidate = None
    status = "improved" if candidate is not None else "no_route"
    penalty = 0.0 if cad_derived else normalized["rules"]["bend_penalty"]
    if original["valid"] and (candidate is None or candidate["length"] + penalty * candidate["bends"] >= original["length"] + penalty * original["bends"] - core.EPSILON):
        candidate, status = deepcopy(original), "unchanged"
    result.update({"original": original, "candidate": candidate, "status": status,
                   "normalized_layout": normalized, "input_units": layout.get("units", "mm"),
                   "protected_endpoints": deepcopy(protected)})
    result["search"]["middle_search_reason"] = result["search"].get("reason", "The movable-middle search finished")
    result["search"]["reason"] = {
        "unchanged": "Original route retained. No checked improvement remained after preserving and validating the fixed endpoint stubs",
        "improved": "Found a checked replacement for the movable middle. The original endpoint stubs remain unchanged",
        "no_route": "No accepted full route was found by this search after preserving and validating the fixed endpoint stubs",
    }[status]
    result["search"]["scope"] = "Only the movable middle is searched. All obstacle boxes remain active as broad bounds and fallbacks. Supported pipe envelopes supply the detailed check"
    context = normalized.get("cad_context", {})
    result["search"]["bounds_scope"] = context.get("bounds_scope", "supplied_rectangular_bounds") if isinstance(context, dict) else "supplied_rectangular_bounds"
    result["checks"]["protected_endpoint_stubs"] = True
    result["assumptions"].append("Only explicitly selected connector contacts on exact original endpoint stubs are exempted. The movable middle has no collision exemptions.")
    result["limitations"].append("Endpoint connector contact assumptions are user selections for this prototype. They do not verify equipment ports, fitting availability, or actual connectivity.")
    return _explain_obstacles(normalized, result)


def run_workflow(layout: dict) -> dict:
    """Route geometry and attach a separate, evidence-aware challenge assessment."""
    supplied = _with_rule_profile(layout)
    profile = supplied.get("rule_profile", "prototype-unverified")
    if supplied.get("model_context"):
        from .model_context import apply_model_context
        supplied = apply_model_context(supplied, supplied["model_context"])
    if profile == "fmg-v3":
        from .challenge_rules import assess_layout, directional_requirements
        baseline_layout = normalize_layout(supplied)
        # Validate engineering declarations before spending time on the search.
        assess_layout(baseline_layout)
        direction = directional_requirements(baseline_layout)
        if supplied["system_type"] == "gravity" and direction.get("flow_direction") == "end_to_start":
            raise ValueError("For routing, reverse the route points so flow is start_to_end. Rebind any engineering measurements to that order.")
    result = _run_geometry_workflow(supplied)
    result["input_units"] = layout.get("units", "mm")
    if supplied.get("proposal", {}).get("mode") == "rounded_pipe":
        from .proposal import add_pipe_proposal
        result = add_pipe_proposal(result)
    if profile != "fmg-v3":
        return result
    from .challenge_rules import EVIDENCE_KINDS, STATUSES, assess_layout
    normalized = result["normalized_layout"]
    baseline = assess_layout(normalized, result["original"]["points"])
    candidate_geometry = result.get("candidate")
    if candidate_geometry and result.get("assembly", {}).get("status") == "ready":
        # Old measurements bind to actual physical geometry, not to the sharp
        # construction vertices from which new circular fillets were made.
        candidate_geometry = {**candidate_geometry, "points": result["assembly"]["centerline_points"]}
    candidate = assess_layout(normalized, candidate_geometry["points"]) if candidate_geometry else None
    for assessment, record in ((baseline, result["original"]), (candidate, candidate_geometry)):
        if assessment is None:
            continue
        context = normalized.get("model_context", {})
        from .model_context import validate_allowed_spaces
        checks = [(item, item["rule_id"] == "allowed_space_size") for item in context.get("findings", [])]
        checks.extend((item, True) for item in validate_allowed_spaces(normalized, record["points"]))
        for item, geometric in checks:
            if geometric:
                evidence_kind = "computed_geometry"
                completeness = "supplied_space_geometry_only" if context.get("allowed_spaces") else "not_provided"
                evidence_note = ("Computed from this route and the supplied allowed-space boxes, including pipe radius and declared boundary clearance. "
                                 "Space identities, required-space assignments and alignment are supplied declarations; this does not independently verify a hull mesh or complete ship coverage.")
            else:
                evidence_kind = "reviewer_declaration"
                completeness = "incomplete_context_inventory" if context else "not_provided"
                evidence_note = ("This finding records missing or limited designer-supplied context, source references, alignment or coverage declarations. "
                                 "The program does not independently establish source completeness or inspect the referenced evidence.")
            assessment["findings"].append({"id": "context_" + item["rule_id"], "status": item["status"], "severity": "error",
                "source": {"document": "Pipe Layout Optimization V3", "page": 2}, "message": item["message"],
                "measured": item, "required": "Declared model context", "source_refs": item.get("source_refs", []),
                "evidence_kind": evidence_kind, "evidence_completeness": completeness, "evidence_note": evidence_note})
        assessment["counts"] = {status: sum(f["status"] == status for f in assessment["findings"]) for status in ("pass", "fail", "needs_review", "not_applicable")}
        assessment["evidence_counts"] = {kind: sum(f["evidence_kind"] == kind for f in assessment["findings"]) for kind in EVIDENCE_KINDS}
        assessment["evidence_status_counts"] = {kind: {status: sum(f["evidence_kind"] == kind and f["status"] == status
            for f in assessment["findings"]) for status in STATUSES} for kind in EVIDENCE_KINDS}
        assessment["hard_failures"] = [f["id"] for f in assessment["findings"] if f["status"] == "fail" and f["severity"] == "error"]
        assessment["complete"] = False
    result["rule_profile"] = "fmg-v3"
    result["challenge_review"] = {"profile": "fmg-v3", "original": baseline, "candidate": candidate,
        "complete_compliance_verified": False,
        "scope": "Checks use supplied geometry and declared engineering measurements. Unavailable evidence remains needs_review."}
    # Known mandatory failures cannot be presented as accepted replacements.
    if candidate and any(f["status"] == "fail" and f["severity"] == "error" for f in candidate["findings"]):
        result["challenge_review"]["rejected_candidate"] = deepcopy(candidate)
        result["candidate"] = None
        result["status"] = "no_route"
        if result.get("assembly"):
            result["assembly"]["status"] = "blocked"
            result["assembly"]["meshes"] = []
            result["assembly"]["findings"].append({"code": "mandatory_challenge_failure", "status": "fail", "message": "A mandatory V3 finding prevents acceptance of this proposal"})
        result["search"]["reason"] = "The geometric candidate failed a mandatory V3 check. See the challenge findings. No replacement was accepted."
    result["assumptions"] = [a for a in result["assumptions"] if a != "Selected rules are an unverified prototype profile, not an official engineering standard."]
    result["assumptions"].append("V3 values come from the supplied challenge brief. Evidence coverage and engineering suitability are not automatically verified.")
    if normalized["system_type"] == "gravity":
        result["search"]["slope_method"] = "Directional minimum fall when axes and flow are confirmed. Surplus fall is distributed along the route and exact pitch remains a separate review check."
        result["method"] = "Bounded XY route candidates with direction-dependent minimum fall when declared ship axes are available. Missing axes retain only the prototype slope screen."
        result["limitations"] = [text for text in result["limitations"] if "at most three turns and a uniform slope" not in text]
        result["limitations"].append("Drain search is bounded to at most three horizontal turns. Exact pitch, vertical offsets and more complex routes can need designer review.")
    result["limitations"].append("Required compartments are checked after search. The current search does not plan a sequence of mandatory compartments and may miss a valid alternative.")
    result["limitations"].append("Challenge findings based on declared measurements are bound to the assessed route. A changed centerline does not inherit manufacturing or access approval from the original.")
    return result
