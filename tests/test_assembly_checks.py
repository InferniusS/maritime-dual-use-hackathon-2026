from copy import deepcopy
import math

import pytest

from pipe_router.assembly import plan_assembly
from pipe_router.assembly_checks import assess_generated_components
from pipe_router.workflow import with_endpoint_connections


def layout(points, *, nominal=None):
    result = {"units": "mm", "provenance": "synthetic", "system_type": "pressurized",
              "bounds": {"min": [-20000] * 3, "max": [20000] * 3},
              "pipe": {"start": points[0], "end": points[-1], "diameter": 40, "original_route": points},
              "obstacles": [], "rules": {"clearance": 0, "grid_step": 250, "bend_penalty": 300}}
    if nominal is not None:
        result["engineering"] = {"units": "mm", "nominal_diameter_mm": nominal,
            "nominal_size_basis_verified": True, "source_refs": ["reviewed fixture pipe DN"],
            "route_points": deepcopy(points)}
    return result


def findings(plan):
    return {f["rule_id"]: f for f in plan["manufacturing_assessment"]["findings"]}


def test_long_route_is_checked_as_proposed_pieces_without_claiming_weld_or_catalog_approval():
    points = [[0, 0, 0], [15000, 0, 0]]
    plan = plan_assembly(layout(points), points)
    assessment = plan["manufacturing_assessment"]
    assert plan["status"] == "ready"
    assert len(assessment["planned_pieces"]) == 3
    assert findings(plan)["piece_length"]["status"] == "pass"
    assert assessment["joint_locations"][1]["point_mm"] == [5000, 0, 0]
    assert assessment["joint_locations"][2]["point_mm"] == [10000, 0, 0]
    assert assessment["mandatory_geometry_passed"]
    assert assessment["status"] == "needs_review"
    for rule in ("joint_design", "fitting_catalog", "material_wall_pressure", "supports_and_installation"):
        assert findings(plan)[rule]["status"] == "needs_review"
    assert not assessment["manufacturing_approved"]
    assert not assessment["catalog_verified"]
    assert not assessment["engineering_compliance_certified"]


@pytest.mark.parametrize("length, accepted", [(6096, True), (6097, False)])
def test_actual_arc_piece_length_is_limited_not_just_straight_cut_length(length, accepted):
    points = [[0, 0, 0], [10000, 0, 0], [10000, 10000, 0]]
    plan = plan_assembly(layout(points), points, bend_radius_mm=length / (math.pi / 2))
    assert (plan["status"] == "ready") == accepted
    assert findings(plan)["piece_length"]["status"] == ("pass" if accepted else "fail")
    if not accepted:
        assert not plan["meshes"]
        assert plan["manufacturing_assessment"]["blocking_failure_count"] == 1


@pytest.mark.parametrize("remaining, accepted", [(25, True), (24.99, False)])
def test_proposed_interfaces_include_fixed_start_when_checking_25mm_boundary(remaining, accepted):
    points = [[0, 0, 0], [100 + remaining, 0, 0], [100 + remaining, 1000, 0]]
    plan = plan_assembly(layout(points), points, bend_radius_mm=100)
    assert (plan["status"] == "ready") == accepted
    assert findings(plan)["proposed_joint_spacing"]["status"] == ("pass" if accepted else "fail")
    assert findings(plan)["joint_design"]["status"] == "needs_review"


@pytest.mark.parametrize("nominal, radius", [(50, 152.4), (80, 228.6), (100, 304.8)])
def test_verified_nominal_radius_boundary_blocks_real_geometry_below_limit(nominal, radius):
    points = [[0, 0, 0], [2000, 0, 0], [2000, 2000, 0]]
    source = layout(points, nominal=nominal)
    good = plan_assembly(source, points, bend_radius_mm=radius)
    bad = plan_assembly(source, points, bend_radius_mm=radius - 0.01)
    assert good["status"] == "ready"
    assert findings(good)["bend_radius"]["status"] == "pass"
    assert bad["status"] == "blocked"
    assert findings(bad)["bend_radius"]["status"] == "fail"
    assert not bad["meshes"]


@pytest.mark.parametrize("gap, accepted", [(101.6, True), (101.59, False)])
def test_actual_tangent_straight_between_consecutive_bends_is_checked(gap, accepted):
    radius = 152.4
    points = [[0, 0, 0], [2000, 0, 0], [2000, 2 * radius + gap, 0], [4000, 2 * radius + gap, 0]]
    plan = plan_assembly(layout(points, nominal=50), points, bend_radius_mm=radius)
    assert (plan["status"] == "ready") == accepted
    check = findings(plan)["straight_between_bends"]
    assert check["status"] == ("pass" if accepted else "fail")
    assert check["measured"][0]["length_mm"] == pytest.approx(gap)


def test_straight_piece_splits_do_not_reset_between_bends_distance():
    points = [[0, 0, 0], [1000, 0, 0], [1000, 15000, 0], [2000, 15000, 0]]
    plan = plan_assembly(layout(points, nominal=50), points, bend_radius_mm=152.4)
    check = findings(plan)["straight_between_bends"]
    assert plan["status"] == "ready"
    assert check["measured"][0]["length_mm"] == pytest.approx(15000 - 2 * 152.4)
    assert len(check["measured"][0]["straight_component_ids"]) == 3


@pytest.mark.parametrize("missing", ["nominal", "verification", "references", "route_binding"])
def test_outer_diameter_or_unbound_nominal_never_certifies_bend_requirements(missing):
    points = [[0, 0, 0], [1000, 0, 0], [1000, 1000, 0]]
    source = layout(points, nominal=100)
    if missing == "nominal":
        del source["engineering"]["nominal_diameter_mm"]
    elif missing == "verification":
        source["engineering"]["nominal_size_basis_verified"] = False
    elif missing == "references":
        source["engineering"]["source_refs"] = []
    else:
        source["engineering"]["route_points"][1][0] += 1
    plan = plan_assembly(source, points, bend_radius_mm=100)
    assert plan["status"] == "ready"
    assert findings(plan)["bend_radius"]["status"] == "needs_review"
    assert not plan["manufacturing_assessment"]["mandatory_geometry_passed"]


def test_unspecified_nominal_category_is_review_not_invented_interpolation():
    points = [[0, 0, 0], [1000, 0, 0], [1000, 1000, 0]]
    plan = plan_assembly(layout(points, nominal=65), points, bend_radius_mm=200)
    assert plan["status"] == "ready"
    assert findings(plan)["bend_radius"]["status"] == "needs_review"
    assert "outside the size categories" in findings(plan)["bend_radius"]["message"]


def test_original_piece_measurements_are_not_reused_for_generated_radius():
    points = [[0, 0, 0], [1000, 0, 0], [1000, 1000, 0]]
    source = layout(points, nominal=100)
    source["engineering"].update({"inventory_complete": {"pieces": True}, "pieces": [
        {"id": "original-piece", "source_refs": ["original CAD"], "centerline_length_mm": 2000,
         "bends": [{"radius_mm": 500, "angle_deg": 90}], "straight_between_bends_mm": [],
         "weld_spacings_mm": [1000], "planarity_deviation_mm": 0, "planarity_tolerance_mm": 1}]})
    plan = plan_assembly(source, points, bend_radius_mm=200)
    assert plan["status"] == "blocked"
    assert findings(plan)["bend_radius"]["measured"][0]["radius_mm"] == 200


def test_angle_recommendation_is_measured_but_does_not_block_otherwise_valid_geometry():
    angle = math.radians(37)
    points = [[0, 0, 0], [1000, 0, 0], [1000 + 1000 * math.cos(angle), 1000 * math.sin(angle), 0]]
    plan = plan_assembly(layout(points), points, bend_radius_mm=200)
    assert plan["status"] == "ready"
    assert findings(plan)["bend_angle_increment"]["status"] == "fail"
    assert findings(plan)["bend_angle_increment"]["severity"] == "preference"
    assert plan["manufacturing_assessment"]["blocking_failure_count"] == 0


def test_separate_planar_pieces_do_not_claim_entire_3d_route_or_spool_is_planar():
    points = [[0, 0, 0], [2000, 0, 0], [2000, 2000, 0], [2000, 2000, 2000]]
    plan = plan_assembly(layout(points), points, bend_radius_mm=200)
    assert plan["status"] == "ready"
    assert findings(plan)["piece_planarity"]["status"] == "pass"
    assert findings(plan)["piece_planarity"]["required"]["reviewer_confirmation_required"]
    assert all(p["physical_bend_count"] <= 1 for p in plan["manufacturing_assessment"]["planned_pieces"])
    assert findings(plan)["joint_design"]["status"] == "needs_review"


def test_planarity_uses_generated_points_and_flags_a_nonplanar_piece():
    points = [[0, 0, 0], [1000, 0, 0], [1000, 1000, 0]]
    source = layout(points)
    components = deepcopy(plan_assembly(source, points, bend_radius_mm=200)["components"])
    bend = next(c for c in components if c["kind"] == "bend")
    bend["centerline_points"][len(bend["centerline_points"]) // 2][2] += 0.2
    assessment = assess_generated_components(source, components)
    finding = next(f for f in assessment["findings"] if f["rule_id"] == "piece_planarity")
    assert finding["status"] == "fail"
    assert finding["component_ids"] == [bend["id"]]
    assert finding["measured"]["maximum_deviation_mm"] > 0.01
    assert assessment["blocking_failure_count"] == 0


def test_nominal_metadata_rejects_false_boolean_and_untyped_size():
    points = [[0, 0, 0], [1000, 0, 0], [1000, 1000, 0]]
    source = layout(points, nominal=50)
    source["engineering"]["nominal_size_basis_verified"] = "true"
    with pytest.raises(ValueError, match="true or false"):
        plan_assembly(source, points)
    source["engineering"]["nominal_size_basis_verified"] = True
    source["engineering"]["nominal_diameter_mm"] = "50"
    with pytest.raises(ValueError):
        plan_assembly(source, points)


def test_connector_anchor_insertion_preserves_nominal_binding_without_rebinding_source_observations():
    points = [[0, 0, 0], [1000, 0, 0], [1000, 1000, 0]]
    source = layout(points, nominal=50)
    source["obstacles"] = [{"id": "connector", "min": [-20, -20, -20], "max": [30, 20, 20]}]
    anchored = with_endpoint_connections(source, {"start": ["connector"]}, 250)
    assert len(anchored["pipe"]["original_route"]) > len(points)
    assert anchored["engineering"]["route_points"] == points
    plan = plan_assembly(anchored, anchored["pipe"]["original_route"], bend_radius_mm=152.4)
    assert plan["status"] == "ready"
    assert plan["manufacturing_assessment"]["nominal_size_basis"]["usable"]
    assert findings(plan)["bend_radius"]["status"] == "pass"


@pytest.mark.parametrize("evidence", [
    [[0, 0, 0], [1000, 1000, 0]],  # Shortcut bypasses the real corner.
    [[1000, 1000, 0], [1000, 0, 0], [0, 0, 0]],  # Reversed direction.
    [[0, 0, 0], [500, 0, 0], [400, 0, 0], [1000, 0, 0], [1000, 1000, 0]],  # Backtracking.
    [[0, 0, 0], [500, 1, 0], [1000, 0, 0], [1000, 1000, 0]],  # Extra bend.
])
def test_equivalent_sampling_rule_does_not_bind_changed_directed_geometry(evidence):
    points = [[0, 0, 0], [1000, 0, 0], [1000, 1000, 0]]
    source = layout(points, nominal=100)
    source["engineering"]["route_points"] = evidence
    plan = plan_assembly(source, points, bend_radius_mm=100)
    assert not plan["manufacturing_assessment"]["nominal_size_basis"]["usable"]
    assert findings(plan)["bend_radius"]["status"] == "needs_review"


def test_extra_forward_samples_in_evidence_also_preserve_same_route_binding():
    points = [[0, 0, 0], [1000, 0, 0], [1000, 1000, 0]]
    source = layout(points, nominal=50)
    source["engineering"]["route_points"] = [[0, 0, 0], [123, 0, 0], [1000, 0, 0], [1000, 250, 0], [1000, 1000, 0]]
    plan = plan_assembly(source, points, bend_radius_mm=152.4)
    assert plan["manufacturing_assessment"]["nominal_size_basis"]["usable"]
