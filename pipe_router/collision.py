"""Continuous intersection helpers for conservative capsule envelopes.

A capsule is the closed volume within ``radius`` of a finite axis segment.
The caller supplies complete obstacle envelopes and applies clearance once.
These predicates do not infer whether an envelope represents a CAD solid.
"""

from __future__ import annotations

from dataclasses import dataclass
import math


Point = tuple[float, float, float]


@dataclass(frozen=True)
class Capsule:
    start: Point
    end: Point
    radius: float
    clip_min: Point | None = None
    clip_max: Point | None = None


def segment_box_interval(start: Point, end: Point, minimum: Point, maximum: Point, *, tolerance: float = 1e-8) -> tuple[float, float] | None:
    """Intersect a continuous segment with closed axis-aligned slabs."""
    enter, leave = 0.0, 1.0
    for axis in range(3):
        delta = end[axis] - start[axis]
        low, high = minimum[axis] - tolerance, maximum[axis] + tolerance
        if delta == 0:
            if not low <= start[axis] <= high:
                return None
            continue
        t1, t2 = (low - start[axis]) / delta, (high - start[axis]) / delta
        if t1 > t2:
            t1, t2 = t2, t1
        enter, leave = max(enter, t1), min(leave, t2)
        if enter > leave:
            return None
    return enter, leave


def _capsule_bounds_touch(start: Point, end: Point, capsule: Capsule, tolerance: float) -> bool:
    radius = capsule.radius + tolerance
    return all(
        min(start[axis], end[axis]) <= max(capsule.start[axis], capsule.end[axis]) + radius
        and max(start[axis], end[axis]) >= min(capsule.start[axis], capsule.end[axis]) - radius
        for axis in range(3)
    )


def _clip_interval(start: Point, end: Point, capsule: Capsule, tolerance: float) -> tuple[float, float] | None:
    if capsule.clip_min is None and capsule.clip_max is None:
        return 0.0, 1.0
    if capsule.clip_min is None or capsule.clip_max is None:
        raise ValueError("A clipped capsule needs both clip_min and clip_max")
    return segment_box_interval(start, end, capsule.clip_min, capsule.clip_max, tolerance=tolerance)


def _point_at(start: Point, end: Point, position: float) -> Point:
    if position == 0.0:
        return start
    if position == 1.0:
        return end
    return tuple(math.fsum((a, (b - a) * position)) for a, b in zip(start, end))


def _dot(left: Point, right: Point) -> float:
    return math.fsum(a * b for a, b in zip(left, right))


def _subtract(left: Point, right: Point) -> Point:
    return tuple(a - b for a, b in zip(left, right))


def _distance_pieces(start: Point, end: Point, capsule: Capsule):
    """Yield exact quadratic distance pieces in local [0, 1] parameters.

Point-to-segment distance changes formula only when the projection crosses
one of the capsule axis endpoints. Splitting there avoids sample-based
collision checks and handles end caps, parallel axes and degenerate axes.
    """
    direction = _subtract(end, start)
    axis = _subtract(capsule.end, capsule.start)
    origin = _subtract(start, capsule.start)
    axis_squared = _dot(axis, axis)
    cuts = [0.0, 1.0]
    if axis_squared:
        projection_start = _dot(origin, axis) / axis_squared
        projection_delta = _dot(direction, axis) / axis_squared
        if projection_delta:
            for endpoint in (0.0, 1.0):
                position = (endpoint - projection_start) / projection_delta
                if 0.0 < position < 1.0:
                    cuts.append(position)
    else:
        projection_start = projection_delta = 0.0
    cuts = sorted(set(cuts))
    for low, high in zip(cuts, cuts[1:]):
        projection_mid = projection_start + projection_delta * ((low + high) / 2)
        relative = tuple(math.fsum((value, delta * low)) for value, delta in zip(origin, direction))
        delta = tuple(value * (high - low) for value in direction)
        if not axis_squared or projection_mid <= 0.0:
            residual, velocity = relative, delta
        elif projection_mid >= 1.0:
            residual, velocity = _subtract(relative, axis), delta
        else:
            along = _dot(relative, axis) / axis_squared
            along_delta = _dot(delta, axis) / axis_squared
            residual = tuple(math.fsum((value, -along * component)) for value, component in zip(relative, axis))
            velocity = tuple(math.fsum((value, -along_delta * component)) for value, component in zip(delta, axis))
        yield low, high, residual, velocity


def _distance_squared(residual: Point, velocity: Point, position: float) -> float:
    vector = tuple(math.fsum((value, delta * position)) for value, delta in zip(residual, velocity))
    return _dot(vector, vector)


def _minimum_position(residual: Point, velocity: Point) -> float:
    squared = _dot(velocity, velocity)
    return min(1.0, max(0.0, -_dot(residual, velocity) / squared)) if squared else 0.0


def segment_intersects_capsule(start: Point, end: Point, capsule: Capsule, *, tolerance: float = 1e-8) -> bool:
    """Return whether a segment touches the closed, optionally clipped capsule."""
    if not _capsule_bounds_touch(start, end, capsule, tolerance):
        return False
    clipping = _clip_interval(start, end, capsule, tolerance)
    if clipping is None:
        return False
    # Both shapes must be hit at the same position along the query segment.
    # The intersection of separately inflated envelopes stays conservative.
    start, end = _point_at(start, end, clipping[0]), _point_at(start, end, clipping[1])
    limit = (capsule.radius + tolerance) ** 2
    for _, _, residual, velocity in _distance_pieces(start, end, capsule):
        position = _minimum_position(residual, velocity)
        if _distance_squared(residual, velocity, position) <= limit:
            return True
    return False


def merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Union overlapping closed intervals without filling genuine gaps."""
    merged = []
    for low, high in sorted(intervals):
        if merged and low <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(high, merged[-1][1]))
        else:
            merged.append((low, high))
    return merged


def segment_capsule_intervals(start: Point, end: Point, capsule: Capsule, *, tolerance: float = 1e-8) -> list[tuple[float, float]]:
    """Return closed contact intervals along the segment parameter [0, 1].

Every distance piece is convex. Its analytic minimum brackets both roots.
Root refinement therefore cannot skip a thin crossing between samples.
Returned roots are rounded outward, keeping boundary contacts conservative.
    """
    if not _capsule_bounds_touch(start, end, capsule, tolerance):
        return []
    clipping = _clip_interval(start, end, capsule, tolerance)
    if clipping is None:
        return []
    limit = (capsule.radius + tolerance) ** 2
    intervals = []
    for low, high, residual, velocity in _distance_pieces(start, end, capsule):
        minimum = _minimum_position(residual, velocity)
        if _distance_squared(residual, velocity, minimum) > limit:
            continue
        enter, leave = 0.0, 1.0
        if _distance_squared(residual, velocity, 0.0) > limit:
            outside, inside = 0.0, minimum
            for _ in range(64):
                mid = (outside + inside) / 2
                if mid == outside or mid == inside:
                    break
                if _distance_squared(residual, velocity, mid) <= limit:
                    inside = mid
                else:
                    outside = mid
            enter = outside
        if _distance_squared(residual, velocity, 1.0) > limit:
            inside, outside = minimum, 1.0
            for _ in range(64):
                mid = (inside + outside) / 2
                if mid == inside or mid == outside:
                    break
                if _distance_squared(residual, velocity, mid) <= limit:
                    inside = mid
                else:
                    outside = mid
            leave = outside
        contact_low = max(clipping[0], low + (high - low) * enter)
        contact_high = min(clipping[1], low + (high - low) * leave)
        if contact_low <= contact_high:
            intervals.append((contact_low, contact_high))
    return merge_intervals(intervals)
