"""Conservative grid routing for a synthetic, single pressurized pipe.

All dimensions are millimeters. Rules are caller-supplied demo assumptions.
This module does not interpret CAD, choose fittings, or certify a design.
"""

from __future__ import annotations

from dataclasses import dataclass
import heapq
import itertools
import math
from typing import Any

from . import collision


Point = tuple[float, float, float]
Index = tuple[int, int, int]
State = tuple[int, int, int, int]
EPSILON = 1e-8
MAX_ABSOLUTE_VALUE = 1e12
MAX_ROUTE_POINTS = 10_000
MAX_OBSTACLES = 256
MAX_CAPSULES_PER_OBSTACLE = 512
MAX_TOTAL_CAPSULES = 8192
MAX_AXIS_COORDINATES = 500
MAX_GRID_NODES = 150_000
MAX_EXPANDED_STATES = 600_000
DIRECTIONS = ((0, -1), (0, 1), (1, -1), (1, 1), (2, -1), (2, 1))

LIMITATIONS = [
    "Synthetic test layout only. No supplied CAD model has been imported.",
    "Clearance and other rule values are demo assumptions, not official standards.",
    "A route is checked against the supplied obstacle envelopes only. Missing obstacles cannot be checked.",
    "Boxes and capsule unions approximate source geometry and can reject a physically possible route.",
    "This version routes one pressurized pipe. Drain slope and other piping systems are unsupported.",
    "Sharp centerline corners do not model standard fittings, bend radius, supports, or connection orientation.",
    "The search optimizes a finite orthogonal grid. It does not establish the best possible continuous route.",
    "No flow, pressure, structural, or full engineering compliance calculation is performed.",
]


@dataclass(frozen=True)
class _Box:
    id: str
    minimum: Point
    maximum: Point
    capsules: tuple[collision.Capsule, ...] = ()


@dataclass(frozen=True)
class _Layout:
    bounds: _Box
    interior: _Box
    obstacles: tuple[_Box, ...]
    start: Point
    end: Point
    diameter: float
    clearance: float
    grid_step: float
    bend_penalty: float
    original_route: Any
    assumptions: tuple[str, ...]


def _number(value: Any, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f"{label} must be a finite number") from None
    if not math.isfinite(result) or abs(result) > MAX_ABSOLUTE_VALUE:
        raise ValueError(f"{label} must be finite and within +/- {MAX_ABSOLUTE_VALUE:g}")
    if positive and result <= 0:
        raise ValueError(f"{label} must be greater than zero")
    return result


def _point(value: Any, label: str) -> Point:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{label} must have exactly three coordinates")
    return tuple(_number(item, f"{label}[{axis}]") for axis, item in enumerate(value))


def _mapping(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _box(value: Any, label: str, identifier: str) -> _Box:
    record = _mapping(value, label)
    minimum = _point(record.get("min"), f"{label}.min")
    maximum = _point(record.get("max"), f"{label}.max")
    if any(low >= high for low, high in zip(minimum, maximum)):
        raise ValueError(f"{label} must have min < max on every axis")
    return _Box(identifier, minimum, maximum)


def _prepare_obstacle(record: dict, label: str, margin: float) -> _Box:
    """Validate a source obstacle and apply moving pipe clearance once."""
    record = _mapping(record, label)
    margin = _number(margin, "obstacle margin")
    if margin < 0:
        raise ValueError("obstacle margin must be nonnegative")
    identifier = record.get("id")
    if not isinstance(identifier, str) or not identifier.strip():
        raise ValueError("Every obstacle must have a nonempty string id")
    box = _box(record, label, identifier)
    capsules = []
    if "capsules" in record:
        items = record["capsules"]
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_CAPSULES_PER_OBSTACLE:
            raise ValueError(f"{label}.capsules must contain 1 to {MAX_CAPSULES_PER_OBSTACLE} capsule records")
        for index, item in enumerate(items):
            prefix = f"{label}.capsules[{index}]"
            item = _mapping(item, prefix)
            start = _point(item.get("start"), f"{prefix}.start")
            end = _point(item.get("end"), f"{prefix}.end")
            radius = _number(item.get("radius"), f"{prefix}.radius", positive=True)
            effective_min = tuple(min(a, b) - radius for a, b in zip(start, end))
            effective_max = tuple(max(a, b) + radius for a, b in zip(start, end))
            clip_min = clip_max = None
            if "clip_min" in item or "clip_max" in item:
                clip_min = _point(item.get("clip_min"), f"{prefix}.clip_min")
                clip_max = _point(item.get("clip_max"), f"{prefix}.clip_max")
                if any(low > high for low, high in zip(clip_min, clip_max)):
                    raise ValueError(f"{prefix} must have clip_min <= clip_max on every axis")
                effective_min = tuple(max(a, b) for a, b in zip(effective_min, clip_min))
                effective_max = tuple(min(a, b) for a, b in zip(effective_max, clip_max))
                if any(low > high for low, high in zip(effective_min, effective_max)):
                    raise ValueError(f"{prefix} clipping bounds do not overlap its capsule envelope")
            if any(
                effective_min[axis] < box.minimum[axis] - EPSILON
                or effective_max[axis] > box.maximum[axis] + EPSILON
                for axis in range(3)
            ):
                raise ValueError(f"{prefix} envelope must fit inside its obstacle min/max bounds")
            capsules.append(collision.Capsule(
                start, end, radius + margin,
                tuple(value - margin for value in clip_min) if clip_min is not None else None,
                tuple(value + margin for value in clip_max) if clip_max is not None else None,
            ))
    return _Box(
        box.id,
        tuple(value - margin for value in box.minimum),
        tuple(value + margin for value in box.maximum),
        tuple(capsules),
    )


def _prepare_layout(layout: dict) -> _Layout:
    layout = _mapping(layout, "layout")
    if layout.get("provenance") != "synthetic":
        raise ValueError("This prototype requires provenance='synthetic'. Real CAD import is not implemented")
    if layout.get("units") != "mm":
        raise ValueError("This prototype requires units='mm'. It does not guess or convert drawing units")
    if layout.get("system_type") != "pressurized":
        raise ValueError("Only system_type='pressurized' is supported. Drain slope checking is not implemented")

    pipe = _mapping(layout.get("pipe"), "pipe")
    rules = _mapping(layout.get("rules"), "rules")
    bounds = _box(layout.get("bounds"), "bounds", "bounds")
    start = _point(pipe.get("start"), "pipe.start")
    end = _point(pipe.get("end"), "pipe.end")
    if start == end:
        raise ValueError("pipe.start and pipe.end must be different")
    diameter = _number(pipe.get("diameter"), "pipe.diameter", positive=True)
    clearance = _number(rules.get("clearance"), "rules.clearance")
    bend_penalty = _number(rules.get("bend_penalty"), "rules.bend_penalty")
    grid_step = _number(rules.get("grid_step"), "rules.grid_step", positive=True)
    if clearance < 0 or bend_penalty < 0:
        raise ValueError("rules.clearance and rules.bend_penalty must be nonnegative")
    margin = diameter / 2 + clearance
    interior = _Box(
        "bounds",
        tuple(value + margin for value in bounds.minimum),
        tuple(value - margin for value in bounds.maximum),
    )

    obstacle_records = layout.get("obstacles")
    if not isinstance(obstacle_records, list) or len(obstacle_records) > MAX_OBSTACLES:
        raise ValueError(f"obstacles must be a list with at most {MAX_OBSTACLES} obstacles")
    obstacles = []
    identifiers = set()
    for number, record in enumerate(obstacle_records):
        record = _mapping(record, f"obstacles[{number}]")
        identifier = record.get("id")
        if not isinstance(identifier, str) or not identifier.strip() or identifier in identifiers:
            raise ValueError("Every obstacle must have a unique nonempty string id")
        identifiers.add(identifier)
        obstacles.append(_prepare_obstacle(record, f"obstacles[{number}]", margin))
    if sum(len(obstacle.capsules) for obstacle in obstacles) > MAX_TOTAL_CAPSULES:
        raise ValueError(f"obstacles must contain at most {MAX_TOTAL_CAPSULES} capsules in total")
    assumptions = layout.get("assumptions", [])
    if not isinstance(assumptions, list) or not all(isinstance(item, str) for item in assumptions):
        raise ValueError("assumptions must be a list of strings")
    return _Layout(
        bounds, interior, tuple(obstacles), start, end, diameter, clearance,
        grid_step, bend_penalty, pipe.get("original_route"), tuple(assumptions),
    )


def _inside(point: Point, box: _Box) -> bool:
    return all(low <= value <= high for value, low, high in zip(point, box.minimum, box.maximum))


def _segment_intersects_box(start: Point, end: Point, box: _Box) -> bool:
    """Continuous closed obstacle check with optional capsule refinement."""
    if collision.segment_box_interval(start, end, box.minimum, box.maximum, tolerance=EPSILON) is None:
        return False
    return not box.capsules or any(
        collision.segment_intersects_capsule(start, end, capsule, tolerance=EPSILON)
        for capsule in box.capsules
    )


def _segment_obstacle_intervals(start: Point, end: Point, box: _Box) -> list[tuple[float, float]]:
    """Return every continuous contact interval for boxes or capsule unions."""
    broad_interval = collision.segment_box_interval(start, end, box.minimum, box.maximum, tolerance=EPSILON)
    if broad_interval is None:
        return []
    if not box.capsules:
        return [broad_interval]
    return collision.merge_intervals([
        interval
        for capsule in box.capsules
        for interval in collision.segment_capsule_intervals(start, end, capsule, tolerance=EPSILON)
    ])


def _direction(start: Point, end: Point) -> int | None:
    changed = [axis for axis in range(3) if start[axis] != end[axis]]
    if len(changed) != 1:
        return None
    axis = changed[0]
    return axis * 2 + (1 if end[axis] > start[axis] else 0)


def _validate(prepared: _Layout, raw_points: Any) -> dict:
    issues = []
    if not isinstance(raw_points, (list, tuple)) or not 2 <= len(raw_points) <= MAX_ROUTE_POINTS:
        return {
            "valid": False,
            "issues": [{"code": "invalid_points", "message": f"A route needs 2 to {MAX_ROUTE_POINTS} points"}],
            "length": 0.0,
            "bends": 0,
        }
    try:
        points = [_point(value, f"route point {number}") for number, value in enumerate(raw_points)]
    except ValueError as error:
        return {"valid": False, "issues": [{"code": "invalid_points", "message": str(error)}], "length": 0.0, "bends": 0}

    if points[0] != prepared.start:
        issues.append({"code": "start_mismatch", "message": "The route must preserve the fixed start point"})
    if points[-1] != prepared.end:
        issues.append({"code": "end_mismatch", "message": "The route must preserve the fixed end point"})

    length, bends = 0.0, 0
    previous_direction = None
    for segment, (start, end) in enumerate(zip(points, points[1:])):
        length += math.dist(start, end)
        direction = _direction(start, end)
        if start == end:
            issues.append({"code": "zero_length", "message": "The route contains a zero-length segment", "segment": segment})
        elif direction is None:
            issues.append({"code": "non_orthogonal", "message": "This prototype accepts only axis-aligned segments", "segment": segment})
        if direction is not None and previous_direction is not None and direction != previous_direction:
            bends += 1
        previous_direction = direction

        if not _inside(start, prepared.interior) or not _inside(end, prepared.interior):
            issues.append({
                "code": "outside_bounds",
                "message": "The pipe radius and demo clearance do not fit inside the supplied bounds",
                "segment": segment,
            })
        for obstacle in prepared.obstacles:
            if _segment_intersects_box(start, end, obstacle):
                issues.append({
                    "code": "obstacle_collision",
                    "message": f"The pipe or its demo clearance touches obstacle {obstacle.id}",
                    "segment": segment,
                    "obstacle": obstacle.id,
                })
    return {"valid": not issues, "issues": issues, "length": length, "bends": bends}


def validate_route(layout: dict, points: list) -> dict:
    """Check a route against the synthetic geometry and supplied demo rules.

    Malformed or unsupported layout inputs raise ValueError. Invalid route
    points produce a result with valid=False and explicit issues instead.
    Segment indexes in issues start at zero.
    """
    return _validate(_prepare_layout(layout), points)


def _axis_coordinates(prepared: _Layout, axis: int) -> list[float]:
    low, high = prepared.interior.minimum[axis], prepared.interior.maximum[axis]
    origin = prepared.bounds.minimum[axis]
    first_position = (low - origin) / prepared.grid_step
    last_position = (high - origin) / prepared.grid_step
    if not math.isfinite(first_position) or not math.isfinite(last_position):
        raise ValueError("Grid spacing is too small for the supplied bounds. Increase grid_step")
    first = math.ceil(first_position)
    last = math.floor(last_position)
    if last - first + 1 > MAX_AXIS_COORDINATES:
        raise ValueError(f"Grid axis exceeds {MAX_AXIS_COORDINATES} coordinates. Increase grid_step for this demo")
    values = {low, high, prepared.start[axis], prepared.end[axis]}
    for number in range(first, last + 1):
        value = origin + number * prepared.grid_step
        if low <= value <= high:
            values.add(value)
    if len(values) > MAX_AXIS_COORDINATES:
        raise ValueError(f"Grid axis exceeds {MAX_AXIS_COORDINATES} coordinates. Increase grid_step for this demo")
    return sorted(values)


def _compress(points: list[Point]) -> list[Point]:
    compressed = []
    for point in points:
        if len(compressed) >= 2 and _direction(compressed[-2], compressed[-1]) == _direction(compressed[-1], point):
            compressed[-1] = point
        else:
            compressed.append(point)
    return compressed


def _search(prepared: _Layout) -> tuple[list[Point] | None, dict]:
    summary = {"expanded_states": 0, "grid_nodes": 0}
    if not _inside(prepared.start, prepared.interior) or not _inside(prepared.end, prepared.interior):
        summary["reason"] = "A fixed endpoint does not fit inside the supplied bounds"
        return None, summary
    blocked = [
        {"endpoint": endpoint, "point": list(point), "obstacle": obstacle.id}
        for endpoint, point in (("start", prepared.start), ("end", prepared.end))
        for obstacle in prepared.obstacles
        if _segment_intersects_box(point, point, obstacle)
    ]
    if blocked:
        summary["reason"] = "A fixed endpoint touches an obstacle or its demo clearance"
        summary["blocked_endpoints"] = blocked
        return None, summary

    coordinates = [_axis_coordinates(prepared, axis) for axis in range(3)]
    sizes = [len(values) for values in coordinates]
    grid_nodes = math.prod(sizes)
    summary["grid_nodes"] = grid_nodes
    if grid_nodes > MAX_GRID_NODES:
        raise ValueError(f"Grid has {grid_nodes} nodes. This demo allows {MAX_GRID_NODES}. Increase grid_step")
    start_index = tuple(coordinates[axis].index(prepared.start[axis]) for axis in range(3))
    end_index = tuple(coordinates[axis].index(prepared.end[axis]) for axis in range(3))
    start_state = (*start_index, 6)
    sequence = itertools.count()
    distances: dict[State, float] = {start_state: 0.0}
    previous: dict[State, State] = {}
    edges: dict[tuple[Index, Index], bool] = {}
    heap = [(sum(abs(a - b) for a, b in zip(prepared.start, prepared.end)), 0.0, next(sequence), start_state)]

    def point_at(index: Index) -> Point:
        return tuple(coordinates[axis][index[axis]] for axis in range(3))

    while heap:
        _, cost, _, state = heapq.heappop(heap)
        if cost != distances.get(state):
            continue
        if summary["expanded_states"] >= MAX_EXPANDED_STATES:
            raise RuntimeError("Search limit reached. No conclusion about route existence is available. Increase grid_step")
        summary["expanded_states"] += 1
        index = state[:3]
        if index == end_index:
            route = [point_at(index)]
            while state in previous:
                state = previous[state]
                route.append(point_at(state[:3]))
            route.reverse()
            summary["reason"] = "Found a route on the finite search grid"
            return _compress(route), summary

        current_point = point_at(index)
        for direction, (axis, increment) in enumerate(DIRECTIONS):
            next_axis = index[axis] + increment
            if not 0 <= next_axis < sizes[axis]:
                continue
            changed = list(index)
            changed[axis] = next_axis
            neighbor = tuple(changed)
            neighbor_point = point_at(neighbor)
            edge_key = (index, neighbor) if index < neighbor else (neighbor, index)
            clear = edges.get(edge_key)
            if clear is None:
                clear = not any(_segment_intersects_box(current_point, neighbor_point, obstacle) for obstacle in prepared.obstacles)
                edges[edge_key] = clear
            if not clear:
                continue
            next_cost = cost + abs(neighbor_point[axis] - current_point[axis])
            if state[3] != 6 and state[3] != direction:
                next_cost += prepared.bend_penalty
            next_state = (*neighbor, direction)
            if next_cost >= distances.get(next_state, math.inf):
                continue
            distances[next_state] = next_cost
            previous[next_state] = state
            heuristic = sum(abs(a - b) for a, b in zip(neighbor_point, prepared.end))
            heapq.heappush(heap, (next_cost + heuristic, next_cost, next(sequence), next_state))
    summary["reason"] = "No route was found after exhausting the finite search grid"
    return None, summary


def _route_record(prepared: _Layout, points: Any) -> dict:
    check = _validate(prepared, points)
    return {"points": points, **check}


def solve_layout(layout: dict) -> dict:
    """Find and independently recheck one route under the demo assumptions.

    The objective is length + bend_penalty * direction_changes. A valid
    original is retained when the grid offers no strictly better objective.
    no_route means there is no candidate on this grid and no valid original.
    Resource limits raise an explicit error instead of claiming no route.
    """
    prepared = _prepare_layout(layout)
    original = _route_record(prepared, prepared.original_route)
    malformed = [issue for issue in original["issues"] if issue["code"] == "invalid_points"]
    if malformed:
        raise ValueError(f"pipe.original_route is malformed: {malformed[0]['message']}")
    points, search = _search(prepared)
    candidate = _route_record(prepared, [list(point) for point in points]) if points is not None else None
    if candidate is not None and not candidate["valid"]:
        raise RuntimeError("The generated route failed its independent geometry check. No route result will be returned")

    status = "no_route" if candidate is None else "improved"
    if original["valid"]:
        original_cost = original["length"] + prepared.bend_penalty * original["bends"]
        candidate_cost = math.inf if candidate is None else candidate["length"] + prepared.bend_penalty * candidate["bends"]
        if candidate_cost >= original_cost - EPSILON:
            candidate = {**original, "points": [list(point) for point in prepared.original_route], "issues": list(original["issues"])}
            status = "unchanged"

    assumptions = list(prepared.assumptions)
    assumptions.extend([
        "All input dimensions and rule values are synthetic demo values in millimeters.",
        "Pipe diameter and the two endpoint positions are fixed.",
        "Pipe radius plus demo clearance is applied to every obstacle and every boundary face.",
        "Touching an inflated obstacle counts as a collision. Equality at an inset outer boundary is allowed.",
    ])
    return {
        "status": status,
        "original": original,
        "candidate": candidate,
        "assumptions": list(dict.fromkeys(assumptions)),
        "method": "CPU A* on an orthogonal 3D grid with incoming direction. Cost is length plus bend_penalty for each direction change.",
        "search": search,
        "limitations": list(LIMITATIONS),
    }
