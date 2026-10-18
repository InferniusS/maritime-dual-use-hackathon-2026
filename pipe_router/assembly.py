"""Rounded, full-width geometric proposals with explicit manufacturing limits.

``ready`` means a circular-fillet envelope fits the supplied geometric checks.
It does not mean a vendor fitting exists or that the assembly is approved for
fabrication. Meshes are capped outer envelopes, not hollow manufactured solids.
"""
from __future__ import annotations

from copy import deepcopy
import math

from . import core
from .assembly_checks import assess_generated_components
from .model_context import prepare_allowed_space_predicate, validate_allowed_spaces
from .workflow import normalize_layout, _connection_obstacle_allowed

ARC_CHORD_ERROR_MM = 0.5
MAX_STRAIGHT_PIECE_MM = 6096.0
MIN_PROPOSED_JOINT_SPACING_MM = 25.0
MAX_ASSEMBLY_POINTS = 512
MAX_COMPONENTS = 1024
MAX_ARC_SEGMENTS = 2048
MAX_MESH_VERTICES = 100_000
RING_SEGMENTS = 24
_TOL = 1e-7


def _add(a, b):
    return tuple(x + y for x, y in zip(a, b))


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _mul(a, factor):
    return tuple(x * factor for x in a)


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(vector):
    length = math.sqrt(_dot(vector, vector))
    if length <= 0:
        raise ValueError("A direction cannot have zero length")
    return _mul(vector, 1 / length)


def _points(points):
    if not isinstance(points, (list, tuple)) or not 2 <= len(points) <= core.MAX_ROUTE_POINTS:
        raise ValueError(f"Assembly needs 2 to {core.MAX_ROUTE_POINTS} route points")
    result = [core._point(point, "assembly route point") for point in points]
    if any(a == b for a, b in zip(result, result[1:])):
        raise ValueError("Assembly route contains a zero-length segment")
    return result


def _finding(code, message, **details):
    return {"code": code, "status": "fail", "message": message, **details}


def _blocked(result, code=None, message=None, **details):
    result["status"] = "blocked"
    if code:
        result["findings"].append(_finding(code, message, **details))
    # Never expose an invalid rounded shape as an accepted pipe proposal.
    result["meshes"] = []
    return result


def _simplify(points):
    """Remove only forward-collinear points; retain reversals and real bends."""
    reduced = []
    for point in points:
        while len(reduced) > 1:
            incoming, outgoing = _unit(_sub(reduced[-1], reduced[-2])), _unit(_sub(point, reduced[-1]))
            if math.dist(incoming, outgoing) > 1e-10:
                break
            reduced.pop()
        reduced.append(point)
    return reduced


# This is a conservative recognition tolerance for sampled source geometry,
# not a fabrication tolerance or an allowance to change protected connections.
SAMPLED_ARC_FIT_TOLERANCE_MM = 0.001
MAX_SAMPLED_ARC_WORK = 50_000


def reconstruct_sampled_arcs(points, protected_endpoints=()):
    """Recognize finely sampled circular arcs between tangent straight runs.

    Return sharp construction vertices as a *proposal seed*, never an accepted
    original assembly. Each recognized arc needs at least five coplanar,
    concyclic points, consistent turns no larger than 30 degrees, and tangent
    straight runs at both ends. The caller still uses the requested bend radius
    and independently validates the resulting physical assembly. Observed arc
    radii do not establish a nominal size, fitting count or catalogue identity.
    """
    route = _points(points)
    protected_indices = set()
    for record in protected_endpoints:
        count = len(record["points"])
        protected_indices.update(range(count) if record["endpoint"] == "start"
                                 else range(len(route) - count, len(route)))
    answer, arcs, index, work = [route[0]], [], 1, 0
    while index < len(route) - 1:
        # An arc must have a straight segment on both sides, and >= 4 chords.
        if index + 4 >= len(route) - 1:
            answer.extend(route[index:])
            break
        a, b, c = route[index:index + 3]
        u, v = _sub(b, a), _sub(c, a)
        cross = _cross(u, v)
        squared = _dot(cross, cross)
        if squared <= _dot(u, u) * _dot(v, v) * 1e-12:
            answer.append(route[index]); index += 1
            continue
        center = _add(a, _mul(_add(_mul(_cross(v, cross), _dot(u, u)),
                                  _mul(_cross(cross, u), _dot(v, v))), 1 / (2 * squared)))
        radius, normal = math.dist(a, center), _unit(cross)
        # A radius-dependent allowance only covers floating-point roundoff.
        tolerance = max(SAMPLED_ARC_FIT_TOLERANCE_MM, radius * 1e-9)
        end, angle, residual = index, 0.0, 0.0
        previous = _unit(_sub(a, center))
        for next_index in range(index + 1, len(route)):
            work += 1
            if work > MAX_SAMPLED_ARC_WORK:
                return {"points": None, "arcs": arcs, "limit_reached": True,
                        "fit_tolerance_mm": SAMPLED_ARC_FIT_TOLERANCE_MM,
                        "reason": "Sampled-arc recognition exceeded its bounded work limit; no reconstructed seed was used."}
            radial = _sub(route[next_index], center)
            error = max(abs(math.sqrt(_dot(radial, radial)) - radius), abs(_dot(radial, normal)))
            if error > tolerance:
                break
            current = _unit(radial)
            step = math.atan2(_dot(normal, _cross(previous, current)), _dot(previous, current))
            if step <= 1e-6 or step > math.radians(30) + 1e-8 or angle + step >= math.radians(175):
                break
            angle += step
            end, previous = next_index, current
            residual = max(residual, error)
        if end - index < 4 or end >= len(route) - 1:
            answer.append(route[index]); index += 1
            continue
        incoming = _unit(_sub(a, route[index - 1]))
        outgoing = _unit(_sub(route[end + 1], route[end]))
        tangent_start = _unit(_cross(normal, _sub(a, center)))
        tangent_end = _unit(_cross(normal, _sub(route[end], center)))
        trim = radius * math.tan(angle / 2)
        vertex = _add(a, _mul(incoming, trim))
        other_vertex = _sub(route[end], _mul(outgoing, trim))
        # Tight directional agreement prevents a near-circle with a kink from
        # silently becoming a tangent elbow. Uncertain curves stay untouched.
        if (math.dist(incoming, tangent_start) > 1e-8 or math.dist(outgoing, tangent_end) > 1e-8
                or math.dist(vertex, other_vertex) > tolerance):
            answer.append(route[index]); index += 1
            continue
        inside_protected = any(i in protected_indices for i in range(index + 1, end))
        arcs.append({"source_start_point_index": index, "source_end_point_index": end,
                     "sample_count": end - index + 1, "observed_radius_mm": radius,
                     "observed_angle_deg": math.degrees(angle), "maximum_fit_error_mm": residual,
                     "construction_vertex": list(vertex), "protected_samples": inside_protected,
                     "identity_verified": False})
        if inside_protected:
            answer.extend(route[index:end + 1])
        else:
            # Exact protected boundary points must survive even when they are
            # collinear with the construction vertex. The assembly planner
            # separately refuses a larger bend that would consume that stub.
            if index in protected_indices:
                answer.append(route[index])
            answer.append(vertex)
            if end in protected_indices:
                answer.append(route[end])
        index = end + 1
    if index == len(route) - 1:
        answer.append(route[-1])
    usable = bool(arcs) and not any(arc["protected_samples"] for arc in arcs)
    return {"points": [list(point) for point in answer] if usable else None,
            "arcs": arcs, "limit_reached": False,
            "fit_tolerance_mm": SAMPLED_ARC_FIT_TOLERANCE_MM,
            "reason": ("Recognized circular samples provide construction vertices only; proposed radius and all physical checks remain independent."
                       if usable else "No fully movable, tangent circular-sample reconstruction was established.")}


def _on_segment(point, start, end):
    vector = _sub(end, start)
    squared = _dot(vector, vector)
    if squared == 0:
        return 0.0 if math.dist(point, start) <= _TOL else None
    parameter = _dot(_sub(point, start), vector) / squared
    nearest = _add(start, _mul(vector, parameter))
    if -_TOL / math.sqrt(squared) <= parameter <= 1 + _TOL / math.sqrt(squared) and math.dist(point, nearest) <= _TOL:
        return max(0.0, min(1.0, parameter))
    return None


def _segment_samples(start, end, landmarks):
    """Retain fixed stub coordinates exactly even on a longer straight piece."""
    entries = [(0.0, start), (1.0, end)]
    for point in landmarks:
        parameter = _on_segment(point, start, end)
        if parameter is not None and 0 < parameter < 1:
            entries.append((parameter, point))
    ordered = []
    for _, point in sorted(entries, key=lambda item: item[0]):
        if not ordered or point != ordered[-1]:
            ordered.append(point)
    return ordered


def _tube_mesh(identifier, kind, points, tangents, diameter, *, plane_normal=None):
    """A closed triangular outer envelope. Every edge belongs to two faces."""
    vertices, faces = [], []
    tangent0 = _unit(tangents[0])
    if plane_normal is None:
        seed = min(((1.0, 0, 0), (0, 1.0, 0), (0, 0, 1.0)), key=lambda axis: abs(_dot(axis, tangent0)))
        plane_normal = _unit(_cross(tangent0, seed))
    for point, tangent in zip(points, tangents):
        tangent = _unit(tangent)
        first = _unit(_cross(plane_normal, tangent))
        second = _unit(_cross(tangent, first))
        for number in range(RING_SEGMENTS):
            angle = 2 * math.pi * number / RING_SEGMENTS
            offset = _mul(_add(_mul(first, math.cos(angle)), _mul(second, math.sin(angle))), diameter / 2)
            vertices.append(list(_add(point, offset)))
    for ring in range(len(points) - 1):
        for side in range(RING_SEGMENTS):
            a = ring * RING_SEGMENTS + side
            b = ring * RING_SEGMENTS + (side + 1) % RING_SEGMENTS
            c, d = b + RING_SEGMENTS, a + RING_SEGMENTS
            faces.extend(([a, b, c], [a, c, d]))
    start_center, end_center = len(vertices), len(vertices) + 1
    vertices.extend((list(points[0]), list(points[-1])))
    last = (len(points) - 1) * RING_SEGMENTS
    for side in range(RING_SEGMENTS):
        next_side = (side + 1) % RING_SEGMENTS
        faces.append([start_center, next_side, side])
        faces.append([end_center, last + side, last + next_side])
    return {"id": identifier, "kind": kind, "vertices": vertices, "faces": faces,
            "geometry_type": "capped_outer_envelope", "wall_thickness_modeled": False}


def render_reference(points, diameter):
    """Full-width original polyline reference, without fitting or clash approval.

    Straight cylinders may meet with gaps or overlaps at polyline corners. This
    helper deliberately does not call that result an assembled or checked pipe.
    """
    route = _points(points)
    diameter = core._number(diameter, "reference diameter", positive=True)
    reduced = _simplify(route)
    if len(reduced) > MAX_ASSEMBLY_POINTS:
        raise ValueError(f"Reference rendering supports at most {MAX_ASSEMBLY_POINTS} non-collinear points")
    meshes = []
    for number, (a, b) in enumerate(zip(reduced, reduced[1:]), 1):
        tangent = _unit(_sub(b, a))
        meshes.append(_tube_mesh(f"reference-{number}", "reference_straight", [a, b], [tangent, tangent], diameter))
    return {"status": "reference_only", "units": "mm", "outer_diameter_mm": diameter,
            "centerline_points": [list(point) for point in route], "meshes": meshes,
            "catalog_verified": False, "geometry_checked": False,
            "note": "Original polyline width reference; corners are not fitted bends and this rendering is not a manufacturing model."}


def _protected(layout, route):
    segments, landmarks, start_length, end_length = [], [], 0.0, 0.0
    for record in layout.get("protected_endpoints", []):
        points = [tuple(point) for point in record["points"]]
        actual = route[:len(points)] if record["endpoint"] == "start" else route[-len(points):]
        if actual != points:
            return None
        landmarks.extend(points)
        length = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
        if record["endpoint"] == "start":
            start_length = length
        else:
            end_length = length
        allowed = {identifier for identifier in record["obstacle_ids"] if _connection_obstacle_allowed(layout, identifier)}
        segments.extend((a, b, allowed) for a, b in zip(points, points[1:]))
    return segments, landmarks, start_length, end_length


def _collision_findings(layout, components, fixed_segments):
    findings = []
    diameter, clearance = layout["pipe"]["diameter"], layout["rules"]["clearance"]
    base_margin = diameter / 2 + clearance
    prepared_by_error, predicates = {}, {}
    for component in components:
        error = component.get("chord_error_mm", 0.0)
        if error not in prepared_by_error:
            prepared_by_error[error] = [core._prepare_obstacle(item, "assembly obstacle", base_margin + error) for item in layout["obstacles"]]
            inset_layout = deepcopy(layout)
            # The allowed-space predicate already uses pipe radius. Increasing
            # diameter by twice the sagitta conservatively covers the real arc.
            inset_layout["pipe"]["diameter"] = diameter + 2 * error
            predicates[error] = prepare_allowed_space_predicate(inset_layout)
        bounds = layout["bounds"]
        interior_min = tuple(value + base_margin + error for value in bounds["min"])
        interior_max = tuple(value - base_margin - error for value in bounds["max"])
        points = [tuple(point) for point in component["centerline_points"]]
        hit_ids, outside, forbidden = set(), False, False
        for start, end in zip(points, points[1:]):
            if any(not all(low <= coordinate <= high for coordinate, low, high in zip(point, interior_min, interior_max)) for point in (start, end)):
                outside = True
            if not predicates[error](start, end):
                forbidden = True
            exempt = set()
            if component["kind"] == "straight":
                for a, b, allowed in fixed_segments:
                    if _on_segment(start, a, b) is not None and _on_segment(end, a, b) is not None:
                        exempt.update(allowed)
            for obstacle in prepared_by_error[error]:
                if obstacle.id not in exempt and core._segment_intersects_box(start, end, obstacle):
                    hit_ids.add(obstacle.id)
        if outside:
            findings.append(_finding("assembly_outside_bounds", "Rounded pipe and clearance leave the supplied routing bounds", component_id=component["id"]))
        if forbidden:
            findings.append(_finding("assembly_outside_allowed_space", "Rounded pipe and arc error allowance leave the declared permitted spaces", component_id=component["id"]))
        for identifier in sorted(hit_ids):
            findings.append(_finding("assembly_obstacle_collision", "Rounded pipe or its clearance touches a supplied obstacle envelope", component_id=component["id"], obstacle_id=identifier))
    return findings


def _closest_segments(a, b, c, d):
    """Closest points on finite segments, including parallel pairs."""
    u, v, w = _sub(b, a), _sub(d, c), _sub(a, c)
    aa, bb, cc, dd, ee = _dot(u, u), _dot(u, v), _dot(v, v), _dot(u, w), _dot(v, w)
    denominator = aa * cc - bb * bb
    # Segment lengths are positive; use a relative parallel tolerance.
    first = max(0.0, min(1.0, (bb * ee - cc * dd) / denominator)) if denominator > aa * cc * 1e-12 else 0.0
    second = (bb * first + ee) / cc
    if second < 0:
        second, first = 0.0, max(0.0, min(1.0, -dd / aa))
    elif second > 1:
        second, first = 1.0, max(0.0, min(1.0, (bb - dd) / aa))
    return first, second, math.dist(_add(a, _mul(u, first)), _add(c, _mul(v, second)))


def _self_interference_findings(components, diameter):
    """Reject distant centerline envelopes that cross or return into the pipe.

    Adjacent components and contacts less than two diameters apart along the
    path are local joint continuity, not proof of self-clash. The screen does
    not certify detailed joint mating or replace a full exact-solid review.
    """
    records, position = [], 0.0
    for component in components:
        points = [tuple(point) for point in component["centerline_points"]]
        error = component["chord_error_mm"]
        margin = diameter / 2 + error
        low = tuple(min(point[axis] for point in points) - margin for axis in range(3))
        high = tuple(max(point[axis] for point in points) + margin for axis in range(3))
        segments = []
        for a, b in zip(points, points[1:]):
            length = component["length_mm"] / (len(points) - 1) if component["kind"] == "bend" else math.dist(a, b)
            segments.append((a, b, position, length))
            position += length
        records.append((component["id"], error, low, high, segments))
    checked = 0
    for first_index, first in enumerate(records):
        for second in records[first_index + 2:]:
            if any(first[2][axis] > second[3][axis] or second[2][axis] > first[3][axis] for axis in range(3)):
                continue
            limit = diameter + first[1] + second[1]
            for a, b, first_position, first_length in first[4]:
                for c, d, second_position, second_length in second[4]:
                    checked += 1
                    if checked > 250_000:
                        return [_finding("assembly_self_check_budget", "The nonlocal self-interference check exceeded its bounded work limit")]
                    u, v, distance = _closest_segments(a, b, c, d)
                    path_separation = second_position + v * second_length - first_position - u * first_length
                    if path_separation > 2 * limit and distance <= limit + _TOL:
                        return [_finding("assembly_self_interference", "Distant parts of the proposed pipe intersect or touch their full-width envelopes", component_id=first[0], other_component_id=second[0])]
    return []


def plan_assembly(layout_mm, points, *, bend_radius_mm=None):
    """Plan pressure-pipe straights and tangent circular bends in millimeters.

    Invalid input raises ValueError. A geometrically infeasible proposal returns
    status=blocked and no meshes. The radius is a declared geometric assumption;
    no nominal size or vendor catalog is guessed from the outer diameter.
    """
    core._mapping(layout_mm, "layout_mm")
    if layout_mm.get("units") != "mm":
        raise ValueError("plan_assembly requires a normalized layout with units='mm'")
    layout = normalize_layout(layout_mm)
    route = _points(points)
    diameter = layout["pipe"]["diameter"]
    supplied_radius = bend_radius_mm if bend_radius_mm is not None else layout["rules"].get("bend_radius")
    radius = max(304.8, 1.5 * diameter) if supplied_radius is None else core._number(supplied_radius, "bend_radius_mm", positive=True)
    result = {"status": "blocked", "units": "mm", "outer_diameter_mm": diameter,
              "bend_radius_mm": radius, "radius_source": "geometric_assumption" if supplied_radius is None else "explicit_geometric_radius",
              "components": [], "meshes": [], "centerline_points": [], "findings": [], "metrics": {},
              "catalog_verified": False, "manufacturing_approved": False, "engineering_compliance_certified": False,
              "geometry_scope": "Circular-fillet outer envelopes against supplied obstacles, bounds and permitted spaces, plus a bounded nonlocal self-interference screen",
              "limitations": ["No wall thickness, material, pressure rating, support design or purchased fitting catalog is modeled.",
                              "The cut list proposes straight pieces and geometric bends; it is not a purchased-parts bill of materials.",
                              "Exact joint mating, local self-contact and equipment connection orientation still require engineering review.",
                              "Only supplied obstacle envelopes are checked. Missing surroundings remain unverified."]}
    if tuple(layout["pipe"]["start"]) != route[0] or tuple(layout["pipe"]["end"]) != route[-1]:
        return _blocked(result, "assembly_endpoint_changed", "Assembly must preserve both fixed route endpoints")
    if layout["system_type"] != "pressurized":
        return _blocked(result, "gravity_assembly_unsupported", "Rounded fabrication geometry is not generated for gravity pipes until slope through each bend is verified")
    if radius <= diameter / 2:
        return _blocked(result, "assembly_radius_too_small", "Bend centerline radius must be greater than the pipe outer radius")
    protected = _protected(layout, route)
    if protected is None:
        return _blocked(result, "assembly_fixed_stub_changed", "The proposal does not exactly retain the declared original endpoint stub points")
    fixed_segments, landmarks, start_fixed, end_fixed = protected
    reduced = _simplify(route)
    if len(reduced) > MAX_ASSEMBLY_POINTS:
        return _blocked(result, "assembly_size_limit", f"At most {MAX_ASSEMBLY_POINTS} non-collinear route points can be assembled")
    lengths = [math.dist(a, b) for a, b in zip(reduced, reduced[1:])]
    total = sum(lengths)
    trims = [0.0] * len(reduced)
    corners = {}
    travelled = 0.0
    arc_sample_count = 0
    for index in range(1, len(reduced) - 1):
        travelled += lengths[index - 1]
        incoming = _unit(_sub(reduced[index], reduced[index - 1]))
        outgoing = _unit(_sub(reduced[index + 1], reduced[index]))
        dot = max(-1.0, min(1.0, _dot(incoming, outgoing)))
        angle = math.acos(dot)
        if angle >= math.pi - 1e-8:
            return _blocked(result, "assembly_reversal", "A reversing corner cannot be replaced by a tangent circular elbow", point_index=index)
        if angle <= 1e-10:
            continue
        trim = radius * math.tan(angle / 2)
        if travelled - trim < start_fixed - _TOL or travelled + trim > total - end_fixed + _TOL:
            return _blocked(result, "assembly_bend_enters_fixed_stub", "A rounded bend would consume part of an unchanged endpoint connection stub", point_index=index)
        normal = _unit(_cross(incoming, outgoing))
        tangent_start = _sub(reduced[index], _mul(incoming, trim))
        tangent_end = _add(reduced[index], _mul(outgoing, trim))
        center = _add(tangent_start, _mul(_cross(normal, incoming), radius))
        # Stable small-angle expression: sagitta/r = 2*sin(theta/4)^2.
        max_angle = 4 * math.asin(math.sqrt(min(1.0, ARC_CHORD_ERROR_MM / (2 * radius))))
        count = max(1, math.ceil(angle / max_angle))
        arc_sample_count += count + 1
        if count > MAX_ARC_SEGMENTS or arc_sample_count > core.MAX_ROUTE_POINTS:
            return _blocked(result, "assembly_size_limit", "The requested radius requires too many arc samples at the 0.5 mm chord-error bound")
        radial = _sub(tangent_start, center)
        axis = _cross(normal, radial)
        sampled, tangents = [], []
        for sample in range(count + 1):
            theta = angle * sample / count
            r = _add(_mul(radial, math.cos(theta)), _mul(axis, math.sin(theta)))
            sampled.append(_add(center, r))
            tangents.append(_unit(_cross(normal, r)))
        sampled[0], sampled[-1] = tangent_start, tangent_end
        tangents[0], tangents[-1] = incoming, outgoing
        trims[index] = trim
        corners[index] = {"kind": "bend", "start": list(tangent_start), "end": list(tangent_end),
                          "center": list(center), "normal": list(normal), "radius_mm": radius,
                          "angle_deg": math.degrees(angle), "length_mm": radius * angle,
                          "tangent_length_mm": trim, "centerline_points": [list(point) for point in sampled],
                          "chord_error_mm": 2 * radius * math.sin(angle / count / 4) ** 2,
                          "_tangents": tangents}
    for segment, length in enumerate(lengths):
        needed = trims[segment] + trims[segment + 1]
        if length < needed - _TOL:
            return _blocked(result, "assembly_insufficient_tangent_length", "This straight leg cannot fit the tangent lengths of its adjacent circular bends", segment_index=segment, available_length_mm=length, required_length_mm=needed)

    components = []
    for segment, (a, b) in enumerate(zip(reduced, reduced[1:])):
        start = tuple(corners[segment]["end"]) if segment in corners else a
        end = tuple(corners[segment + 1]["start"]) if segment + 1 in corners else b
        length = math.dist(start, end)
        if length > _TOL:
            count = max(1, math.ceil(length / MAX_STRAIGHT_PIECE_MM))
            if len(components) + count > MAX_COMPONENTS:
                return _blocked(result, "assembly_size_limit", f"Assembly exceeds {MAX_COMPONENTS} components")
            direction = _unit(_sub(end, start))
            for cut in range(count):
                cut_start = start if cut == 0 else _add(start, _mul(direction, length * cut / count))
                cut_end = end if cut + 1 == count else _add(start, _mul(direction, length * (cut + 1) / count))
                samples = _segment_samples(cut_start, cut_end, landmarks)
                components.append({"kind": "straight", "start": list(cut_start), "end": list(cut_end),
                                   "length_mm": math.dist(cut_start, cut_end), "centerline_points": [list(p) for p in samples],
                                   "chord_error_mm": 0.0, "_tangents": [direction, direction]})
        if segment + 1 in corners:
            components.append(corners[segment + 1])
    if len(components) > MAX_COMPONENTS:
        return _blocked(result, "assembly_size_limit", f"Assembly exceeds {MAX_COMPONENTS} components")
    for index, component in enumerate(components, 1):
        component["id"] = f"proposal-{index}"
        component["outer_diameter_mm"] = diameter
        component["catalog_verified"] = False
        component["part_number"] = None
    result["findings"].extend(_collision_findings(layout, components, fixed_segments))
    result["findings"].extend(_self_interference_findings(components, diameter))
    combined = []
    for component in components:
        for point in component["centerline_points"]:
            if not combined or point != combined[-1]:
                combined.append(point)
    if len(combined) > core.MAX_ROUTE_POINTS:
        return _blocked(result, "assembly_size_limit", "Rounded centerline exceeds the supported sample count")
    # Prove required-space visits with the same sagitta inset. A chord passing
    # through a tiny room is not enough if the true circular arc could miss it.
    required_spaces = set(layout.get("model_context", {}).get("required_space_ids", []))
    visited_spaces = set()
    for component in components if required_spaces else []:
        visit_layout = deepcopy(layout)
        visit_layout["pipe"]["diameter"] = diameter + 2 * component["chord_error_mm"]
        for finding in validate_allowed_spaces(visit_layout, component["centerline_points"]):
            if finding["rule_id"] == "required_space" and finding["status"] == "pass":
                visited_spaces.add(finding["space_id"])
    for identifier in sorted(required_spaces - visited_spaces):
        result["findings"].append(_finding("assembly_required_space",
            "The rounded route cannot establish a visit to this required space with its arc-error allowance", space_id=identifier))
    result["centerline_points"] = combined
    result["metrics"] = {"original_polyline_length_mm": total,
                         "centerline_length_mm": sum(c["length_mm"] for c in components),
                         "straight_cut_length_mm": sum(c["length_mm"] for c in components if c["kind"] == "straight"),
                         "bend_count": len(corners), "straight_piece_count": sum(c["kind"] == "straight" for c in components),
                         "proposed_internal_joint_count": max(0, len(components) - 1),
                         "max_arc_chord_error_mm": max((c["chord_error_mm"] for c in components), default=0.0)}
    # Measure the generated pieces themselves. Source fabrication observations
    # must not silently certify a changed route; unknown nominal/catalogue
    # evidence stays visible without turning every prototype into a failure.
    assessment = assess_generated_components(layout, components)
    result["manufacturing_assessment"] = assessment
    for finding in assessment["findings"]:
        if finding["status"] == "fail" and finding["severity"] == "error":
            code = "assembly_joint_spacing" if finding["rule_id"] == "proposed_joint_spacing" else f"assembly_{finding['rule_id']}"
            result["findings"].append(_finding(code, finding["message"],
                manufacturing_rule_id=finding["rule_id"], component_ids=finding["component_ids"],
                measured=finding["measured"], required=finding["required"]))
    estimated_vertices = sum((len(c["centerline_points"]) if c["kind"] == "bend" else 2) * RING_SEGMENTS + 2 for c in components)
    if estimated_vertices > MAX_MESH_VERTICES:
        result["findings"].append(_finding("assembly_size_limit", f"The assembly exceeds the {MAX_MESH_VERTICES} mesh-vertex limit"))
    if not result["findings"]:
        for component in components:
            mesh_points = [tuple(p) for p in component["centerline_points"]] if component["kind"] == "bend" else [tuple(component["start"]), tuple(component["end"])]
            result["meshes"].append(_tube_mesh(component["id"], component["kind"], mesh_points, component["_tangents"], diameter,
                                                 plane_normal=tuple(component["normal"]) if component["kind"] == "bend" else None))
        result["status"] = "ready"
    for component in components:
        component.pop("_tangents", None)
    result["components"] = components
    result["metrics"]["mesh_vertex_count"] = sum(len(mesh["vertices"]) for mesh in result["meshes"])
    result["findings"].append({"code": "assembly_catalog_unverified", "status": "needs_review",
                               "message": "Bend radius and proposed cut pieces are geometric assumptions. Standard fitting availability and full manufacturing compliance are unverified."})
    return result
