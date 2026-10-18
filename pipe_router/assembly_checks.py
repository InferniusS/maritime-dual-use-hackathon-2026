"""Manufacturing evidence measured from a generated geometric assembly.

The proposed decomposition assigns one straight or one circular bend to each
piece. Interfaces are proposed joint locations, not observed or approved welds.
These checks never reuse original CAD piece measurements to approve a changed
route, and never promote geometric envelopes to purchased or buildable parts.
"""
from __future__ import annotations

import math

from . import core
from .challenge_rules import (
    ANGLE_TOLERANCE_DEG, DOCUMENT, LENGTH_TOLERANCE_MM, bend_requirements,
)

MAX_PIECE_LENGTH_MM = 6096.0
MIN_PROPOSED_JOINT_SPACING_MM = 25.0
# The brief says "generally lay flat" and supplies no numeric allowance. This
# is a declared prototype comparison for generated pieces, not a specification.
PLANARITY_PROTOTYPE_TOLERANCE_MM = 0.01
MAX_COMPONENTS = 1024


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _planarity_deviation(points):
    """Distance to a stable plane through supplied centerline points, O(n).

    Exactly collinear pieces have zero deviation. A nonplanar piece is assessed
    against a plane through its first point and two well-separated directions;
    this is a conservative comparison, not a least-squares best-fit tolerance.
    """
    origin = points[0]
    vectors = [_sub(point, origin) for point in points[1:]]
    first = max(vectors, key=lambda value: _dot(value, value), default=(0, 0, 0))
    normal = max((_cross(first, value) for value in vectors), key=lambda value: _dot(value, value), default=(0, 0, 0))
    magnitude = math.sqrt(_dot(normal, normal))
    if magnitude <= 1e-12:
        return 0.0
    return max((abs(_dot(value, normal)) / magnitude for value in vectors), default=0.0)


def _finding(rule_id, status, message, *, measured=None, required=None,
             component_ids=(), source_refs=(), severity="error"):
    return {"rule_id": rule_id, "status": status, "severity": severity,
            "message": message, "measured": measured, "required": required,
            "component_ids": list(component_ids), "source_refs": list(source_refs),
            "evidence_basis": "generated_component_geometry",
            "source": {"document": DOCUMENT, "page": 2}}


def _directed_vertices(points):
    """Remove only forward-collinear samples within the numeric tolerance.

    Each run uses its original fixed line, preventing many small deviations
    from accumulating into an unobserved bend. Reversals end a run even when
    their points lie on that same line. This changes nominal-size binding only;
    it does not rebind source weld, spool or other manufacturing observations.
    """
    vertices, origin, end, direction = [points[0]], points[0], points[0], None
    progress = 0.0
    for point in points[1:]:
        vector = _sub(point, origin)
        if direction is not None:
            distance = _dot(vector, direction)
            offset = tuple(value - distance * axis for value, axis in zip(vector, direction))
            if distance >= progress - LENGTH_TOLERANCE_MM and math.sqrt(_dot(offset, offset)) <= LENGTH_TOLERANCE_MM:
                end, progress = point, max(progress, distance)
                continue
            vertices.append(end)
            origin = end
            vector = _sub(point, origin)
        length = math.sqrt(_dot(vector, vector))
        if length <= LENGTH_TOLERANCE_MM:
            continue
        direction = tuple(value / length for value in vector)
        end, progress = point, length
    if math.dist(vertices[-1], end) > LENGTH_TOLERANCE_MM:
        vertices.append(end)
    return vertices


def _nominal_basis(layout):
    """Verified size must be traceable to the selected original route.

    A changed route retains that size, but old physical-piece observations are
    deliberately unused. Validate the few relevant fields without repeatedly
    scanning a potentially large source inventory during candidate search.
    """
    engineering = layout.get("engineering", {})
    core._mapping(engineering, "engineering")
    if engineering and engineering.get("units") != "mm":
        raise ValueError("engineering.units must explicitly be mm")
    nominal = engineering.get("nominal_diameter_mm")
    if nominal is not None:
        nominal = core._number(nominal, "engineering.nominal_diameter_mm", positive=True)
    verified = engineering.get("nominal_size_basis_verified", False)
    if not isinstance(verified, bool):
        raise ValueError("engineering.nominal_size_basis_verified must be true or false")
    refs = engineering.get("source_refs", [])
    if not isinstance(refs, list) or any(not isinstance(ref, str) or not ref.strip() for ref in refs):
        raise ValueError("engineering.source_refs must be a list of nonempty strings")
    points = engineering.get("route_points")
    original = layout["pipe"]["original_route"]
    bound = False
    if points is not None:
        if not isinstance(points, (tuple, list)) or not 2 <= len(points) <= core.MAX_ROUTE_POINTS:
            raise ValueError("engineering.route_points must contain a supported number of points")
        points = [core._point(point, "engineering.route_points") for point in points]
        evidence_vertices, original_vertices = _directed_vertices(points), _directed_vertices(original)
        bound = len(evidence_vertices) == len(original_vertices) and all(
            math.dist(a, b) <= LENGTH_TOLERANCE_MM for a, b in zip(evidence_vertices, original_vertices))
    usable = nominal is not None and verified and bool(refs) and bound
    limits = bend_requirements(nominal) if usable else None
    reason = ("The declared nominal size is verified, referenced and bound to the selected original route."
              if usable else "Supply a verified nominal diameter, source references and engineering.route_points matching the selected original route; outside diameter is not a nominal-size substitute.")
    if usable and limits is None:
        reason = "The verified nominal size falls outside the size categories explicitly covered by this brief; a designer must supply the applicable requirement."
    return {"nominal_diameter_mm": nominal, "verified": verified, "source_refs": refs,
            "bound_to_original_route": bound, "usable": usable and limits is not None,
            "limits": limits, "message": reason}


def assess_generated_components(layout, components):
    """Return a JSON-safe assessment of the actual proposed pieces.

    Mandatory measured failures block a proposal. Missing evidence and
    preferences do not silently become pass, and do not block all prototypes.
    Every assembly still requires catalogue, joint, material and installation
    review. The assessment is bounded and does not construct another mesh.
    """
    core._mapping(layout, "layout")
    if layout.get("units") != "mm":
        raise ValueError("Generated-component assessment requires millimeters")
    if not isinstance(components, list) or not 1 <= len(components) <= MAX_COMPONENTS:
        raise ValueError(f"Generated-component assessment requires 1 to {MAX_COMPONENTS} components")
    basis = _nominal_basis(layout)
    pieces, joints, bends, separations = [], [], [], []
    last_bend, straight_length, straight_ids = None, 0.0, []
    sampled_count = 0
    for index, component in enumerate(components):
        identity, kind = component["id"], component["kind"]
        if kind not in ("straight", "bend"):
            raise ValueError("Only generated straight and circular-bend components can be assessed")
        raw = component["centerline_points"]
        if not isinstance(raw, (list, tuple)) or len(raw) < 2:
            raise ValueError("Generated pieces require centerline points")
        sampled_count += len(raw)
        if sampled_count > core.MAX_ROUTE_POINTS + 2 * MAX_COMPONENTS:
            raise ValueError("Generated-component assessment exceeds its point budget")
        points = [core._point(point, "generated piece point") for point in raw]
        start, end = core._point(component["start"], "generated piece start"), core._point(component["end"], "generated piece end")
        if kind == "straight":
            length = math.dist(start, end)
            straight_length += length
            straight_ids.append(identity)
        else:
            radius = core._number(component["radius_mm"], "generated bend radius", positive=True)
            angle = core._number(component["angle_deg"], "generated bend angle", positive=True)
            length = radius * math.radians(angle)
            bends.append({"component_id": identity, "radius_mm": radius, "angle_deg": angle})
            if last_bend is not None:
                separations.append({"from_bend": last_bend, "to_bend": identity,
                                    "length_mm": straight_length, "straight_component_ids": straight_ids[:]})
            last_bend, straight_length, straight_ids = identity, 0.0, []
        pieces.append({"piece_id": identity, "component_ids": [identity], "kind": kind,
                       "centerline_length_mm": length, "physical_bend_count": int(kind == "bend"),
                       "planarity_deviation_mm": _planarity_deviation(points),
                       "planarity_tolerance_mm": PLANARITY_PROTOTYPE_TOLERANCE_MM,
                       "joint_design_approved": False})
        if index == 0:
            joints.append({"id": "interface-0", "point_mm": list(start), "kind": "fixed_start_interface"})
        joints.append({"id": f"interface-{index + 1}", "point_mm": list(end),
                       "kind": "fixed_end_interface" if index + 1 == len(components) else "proposed_internal_joint"})

    findings = []
    def check(rule, bad, message, measured, required, *, severity="error"):
        findings.append(_finding(rule, "fail" if bad else "pass", message,
                                 measured=measured, required=required,
                                 component_ids=bad, severity=severity))

    check("piece_length", [p["piece_id"] for p in pieces if p["centerline_length_mm"] > MAX_PIECE_LENGTH_MM + LENGTH_TOLERANCE_MM],
          "Generated piece lengths are measured analytically; each straight or circular bend is a separate proposed piece.",
          {"maximum_mm": max(p["centerline_length_mm"] for p in pieces), "piece_count": len(pieces)}, {"maximum_mm": MAX_PIECE_LENGTH_MM})
    check("proposed_joint_spacing", [p["piece_id"] for p in pieces if p["centerline_length_mm"] + LENGTH_TOLERANCE_MM < MIN_PROPOSED_JOINT_SPACING_MM],
          "Spacing between consecutive proposed interfaces, including fixed ends, is measured along the piece centerline. Actual weld design remains unverified.",
          {"minimum_along_centerline_mm": min(p["centerline_length_mm"] for p in pieces), "proposed_internal_joint_count": max(0, len(pieces) - 1)},
          {"minimum_mm": MIN_PROPOSED_JOINT_SPACING_MM, "interpretation": "proposed interface spacing; not observed weld spacing"})
    check("bends_per_piece", [p["piece_id"] for p in pieces if p["physical_bend_count"] > 3],
          "The explicit proposed decomposition gives each circular bend its own piece; this does not approve the resulting joint locations or a combined fabrication spool.",
          {"maximum": max(p["physical_bend_count"] for p in pieces)}, {"maximum": 3})
    if bends:
        check("bend_angle_increment", [b["component_id"] for b in bends if abs(b["angle_deg"] - 5 * round(b["angle_deg"] / 5)) > ANGLE_TOLERANCE_DEG],
              "Angles of generated circular bends are compared with the five-degree recommendation.",
              bends, {"preferred_increment_degrees": 5}, severity="preference")
    else:
        findings.append(_finding("bend_angle_increment", "not_applicable", "The generated route has no physical bends.", severity="preference"))
    check("piece_planarity", [p["piece_id"] for p in pieces if p["planarity_deviation_mm"] > PLANARITY_PROTOTYPE_TOLERANCE_MM + LENGTH_TOLERANCE_MM],
          "Each proposed piece is compared with a plane through its generated centerline points. The numeric tolerance is a prototype assumption, not a tolerance supplied in the brief.",
          {"maximum_deviation_mm": max(p["planarity_deviation_mm"] for p in pieces)},
          {"prototype_tolerance_mm": PLANARITY_PROTOTYPE_TOLERANCE_MM, "reviewer_confirmation_required": True}, severity="preference")

    for rule, records, minimum_key in (("bend_radius", bends, "min_radius_mm"),
                                      ("straight_between_bends", separations, "min_straight_between_bends_mm")):
        if not records:
            findings.append(_finding(rule, "not_applicable", "No physical bends apply." if rule == "bend_radius" else "There is no consecutive pair of physical bends."))
        elif not basis["usable"]:
            findings.append(_finding(rule, "needs_review", basis["message"], measured=records,
                                     required={"verified_nominal_size_required": True}, source_refs=basis["source_refs"]))
        else:
            key = "radius_mm" if rule == "bend_radius" else "length_mm"
            bad = [record for record in records if record[key] + LENGTH_TOLERANCE_MM < basis["limits"][minimum_key]]
            findings.append(_finding(rule, "fail" if bad else "pass",
                "Measured generated geometry was compared with the requirement for the verified nominal size. Consecutive-bend straight lengths include any proposed straight-piece joins.",
                measured=records, required={"nominal_diameter_mm": basis["nominal_diameter_mm"], "minimum_mm": basis["limits"][minimum_key]},
                component_ids=[record.get("component_id", record.get("to_bend")) for record in bad], source_refs=basis["source_refs"]))

    for rule, message in (
        ("joint_design", "Proposed interfaces are not approved welds or shop spool boundaries. Joint type, mating, weld spacing convention and assembly sequence need designer review."),
        ("fitting_catalog", "Generated bend shapes have no verified manufacturer or part number; availability of a matching commercial fitting is unverified."),
        ("material_wall_pressure", "Material, wall thickness and pressure rating have not been selected or checked."),
        ("supports_and_installation", "Support placement, handling, installation access and connection details require designer review."),
    ):
        finding = _finding(rule, "needs_review", message)
        finding["evidence_basis"] = "not_provided_for_generated_components"
        # These are limitations, not new numeric rules invented from PDF p2.
        finding["source"] = None
        findings.append(finding)
    counts = {status: sum(f["status"] == status for f in findings) for status in ("pass", "fail", "needs_review", "not_applicable")}
    blocking = sum(f["status"] == "fail" and f["severity"] == "error" for f in findings)
    geometric = findings[:7]
    return {"status": "blocked" if blocking else "needs_review", "counts": counts,
            "blocking_failure_count": blocking,
            "mandatory_geometry_passed": all(f["status"] in ("pass", "not_applicable") for f in geometric if f["severity"] == "error"),
            "findings": findings, "planned_pieces": pieces, "joint_locations": joints,
            "nominal_size_basis": basis,
            "piece_definition": "Each generated straight or circular-bend component is a separate proposed piece, with provisional interfaces at component boundaries.",
            "assumptions": ["The proposed piece decomposition and joint locations require designer approval.",
                            "Planarity uses a declared 0.01 mm prototype comparison; the brief provides no numeric tolerance.",
                            "Original CAD weld, fitting and spool observations do not certify generated pieces."],
            "catalog_verified": False, "manufacturing_approved": False,
            "engineering_compliance_certified": False}
