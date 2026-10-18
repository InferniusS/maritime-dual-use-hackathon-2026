from collections import Counter
from copy import deepcopy
import math

import pytest

from pipe_router import core
from pipe_router.assembly import (
    ARC_CHORD_ERROR_MM, MAX_MESH_VERTICES, MAX_STRAIGHT_PIECE_MM,
    plan_assembly, render_reference,
)


def layout(points=None, diameter=40):
    points = points or [[0, 0, 0], [1000, 0, 0], [1000, 1000, 0]]
    return {"units": "mm", "provenance": "synthetic", "system_type": "pressurized",
            "bounds": {"min": [-20000, -20000, -20000], "max": [20000, 20000, 20000]},
            "pipe": {"start": points[0], "end": points[-1], "diameter": diameter, "original_route": points},
            "obstacles": [], "rules": {"clearance": 0, "grid_step": 250, "bend_penalty": 300}}


def codes(plan):
    return {item["code"] for item in plan["findings"]}


def test_true_tangent_quarter_bend_with_cut_lengths_and_physical_count():
    source = layout()
    plan = plan_assembly(source, source["pipe"]["original_route"], bend_radius_mm=200)
    assert plan["status"] == "ready"
    assert [c["kind"] for c in plan["components"]] == ["straight", "bend", "straight"]
    first, bend, last = plan["components"]
    assert first["length_mm"] == pytest.approx(800)
    assert last["length_mm"] == pytest.approx(800)
    assert bend["start"] == pytest.approx([800, 0, 0])
    assert bend["end"] == pytest.approx([1000, 200, 0])
    assert bend["center"] == pytest.approx([800, 200, 0])
    assert bend["normal"] == pytest.approx([0, 0, 1])
    assert bend["angle_deg"] == pytest.approx(90)
    assert bend["length_mm"] == pytest.approx(100 * math.pi)
    assert all(math.dist(point, bend["center"]) == pytest.approx(200) for point in bend["centerline_points"])
    assert plan["metrics"]["bend_count"] == 1
    assert plan["metrics"]["centerline_length_mm"] == pytest.approx(1600 + 100 * math.pi)
    assert plan["centerline_points"][0] == source["pipe"]["start"]
    assert plan["centerline_points"][-1] == source["pipe"]["end"]


def test_default_radius_is_an_explicit_assumption_and_not_catalog_approval():
    source = layout(diameter=300)
    plan = plan_assembly(source, source["pipe"]["original_route"])
    assert plan["status"] == "ready"
    assert plan["bend_radius_mm"] == 450
    assert plan["radius_source"] == "geometric_assumption"
    assert not plan["catalog_verified"]
    assert not plan["manufacturing_approved"]
    assert not plan["engineering_compliance_certified"]
    assert all(c["part_number"] is None for c in plan["components"])
    assert "assembly_catalog_unverified" in codes(plan)


@pytest.mark.parametrize("points", [
    [[0, 0, 0], [1000, 0, 0], [1000, -1000, 0]],
    [[0, 0, 0], [0, 0, 1000], [500, 500, 1500]],
    [[0, 0, 0], [1000, 1000, 1000], [2000, 1000, 0]],
])
def test_arbitrary_planar_3d_corner_orientation(points):
    source = layout(points)
    plan = plan_assembly(source, points, bend_radius_mm=100)
    assert plan["status"] == "ready"
    bend = next(c for c in plan["components"] if c["kind"] == "bend")
    for point in bend["centerline_points"]:
        radial = [x - y for x, y in zip(point, bend["center"])]
        assert math.sqrt(sum(x * x for x in radial)) == pytest.approx(100)
        assert sum(x * y for x, y in zip(radial, bend["normal"])) == pytest.approx(0, abs=1e-8)


def test_meshes_are_closed_finite_triangles_and_preserve_diameter():
    source = layout(diameter=168.3)
    plan = plan_assembly(source, source["pipe"]["original_route"])
    assert plan["status"] == "ready"
    assert plan["metrics"]["mesh_vertex_count"] <= MAX_MESH_VERTICES
    for component, mesh in zip(plan["components"], plan["meshes"]):
        assert all(math.isfinite(value) for vertex in mesh["vertices"] for value in vertex)
        assert all(len(face) == 3 and all(0 <= index < len(mesh["vertices"]) for index in face) for face in mesh["faces"])
        edges = Counter(tuple(sorted(pair)) for face in mesh["faces"] for pair in zip(face, face[1:] + face[:1]))
        assert set(edges.values()) == {2}
        # First ring is a circle around the component's tangent start.
        assert all(math.dist(vertex, component["start"]) == pytest.approx(168.3 / 2) for vertex in mesh["vertices"][:24])
        assert mesh["geometry_type"] == "capped_outer_envelope"
        assert not mesh["wall_thickness_modeled"]


def test_insufficient_tangent_length_never_silently_miters_corner():
    points = [[0, 0, 0], [1000, 0, 0], [1000, 100, 0], [2000, 100, 0]]
    plan = plan_assembly(layout(points), points, bend_radius_mm=200)
    assert plan["status"] == "blocked"
    assert "assembly_insufficient_tangent_length" in codes(plan)
    assert not plan["meshes"]


def test_reversal_is_blocked():
    points = [[0, 0, 0], [1000, 0, 0], [500, 0, 0]]
    plan = plan_assembly(layout(points), points)
    assert plan["status"] == "blocked"
    assert "assembly_reversal" in codes(plan)


def test_radius_cannot_be_smaller_than_outer_radius():
    source = layout(diameter=100)
    plan = plan_assembly(source, source["pipe"]["original_route"], bend_radius_mm=50)
    assert plan["status"] == "blocked"
    assert "assembly_radius_too_small" in codes(plan)


def test_arc_chord_error_is_bounded_and_collision_uses_sagitta():
    # Put a tiny obstacle on the true arc midway between two samples. It is
    # outside the uninflated chord's capsule; sagitta inflation must catch it.
    points = [[-500, 0, 0], [0, 0, 0], [0, 500, 0]]
    source = layout(points, diameter=0.1)
    bare = plan_assembly(source, points, bend_radius_mm=100)
    bend = next(c for c in bare["components"] if c["kind"] == "bend")
    arc_points = bend["centerline_points"]
    step = math.pi / 2 / (len(arc_points) - 1)
    midpoint = [-100 + 100 * math.sin(step / 2), 100 - 100 * math.cos(step / 2), 0]
    obstacle = {"id": "on-arc", "min": [x - 0.01 for x in midpoint], "max": [x + 0.01 for x in midpoint]}
    uninflated = core._prepare_obstacle(obstacle, "test", 0.05)
    assert not core._segment_intersects_box(tuple(arc_points[0]), tuple(arc_points[1]), uninflated)
    assert 0 < bend["chord_error_mm"] <= ARC_CHORD_ERROR_MM
    source["obstacles"].append(obstacle)
    plan = plan_assembly(source, points, bend_radius_mm=100)
    assert plan["status"] == "blocked"
    assert "assembly_obstacle_collision" in codes(plan)
    assert not plan["meshes"]


def with_start_stub(points=None):
    points = points or [[0, 0, 0], [200, 0, 0], [1000, 0, 0], [1000, 1000, 0]]
    source = layout(points, diameter=20)
    source["obstacles"] = [{"id": "connector", "min": [-10, -10, -10], "max": [40, 10, 10]}]
    source["protected_endpoints"] = [{"endpoint": "start", "points": points[:2], "obstacle_ids": ["connector"], "max_distance": 250}]
    return source


def test_fixed_stub_vertices_and_collinear_extension_are_preserved():
    source = with_start_stub()
    plan = plan_assembly(source, source["pipe"]["original_route"], bend_radius_mm=100)
    assert plan["status"] == "ready"
    assert plan["centerline_points"][:2] == source["protected_endpoints"][0]["points"]
    assert plan["components"][0]["length_mm"] == pytest.approx(900)
    assert plan["components"][0]["centerline_points"][:2] == source["protected_endpoints"][0]["points"]


def test_bend_cannot_consume_protected_stub():
    source = with_start_stub([[0, 0, 0], [200, 0, 0], [250, 0, 0], [250, 1000, 0]])
    plan = plan_assembly(source, source["pipe"]["original_route"], bend_radius_mm=100)
    assert plan["status"] == "blocked"
    assert "assembly_bend_enters_fixed_stub" in codes(plan)


def test_fixed_stub_not_silently_rebound_to_modified_geometry():
    source = with_start_stub()
    points = deepcopy(source["pipe"]["original_route"])
    points[1] = [201, 0, 0]
    plan = plan_assembly(source, points, bend_radius_mm=100)
    assert plan["status"] == "blocked"
    assert "assembly_fixed_stub_changed" in codes(plan)


def test_connector_exemption_never_applies_to_movable_portion():
    source = with_start_stub()
    source["obstacles"][0]["max"][0] = 500
    plan = plan_assembly(source, source["pipe"]["original_route"], bend_radius_mm=100)
    assert plan["status"] == "blocked"
    assert "assembly_obstacle_collision" in codes(plan)


def test_bend_cannot_consume_fixed_end_stub():
    points = [[0, 0, 0], [1000, 0, 0], [1000, 250, 0], [1000, 400, 0]]
    source = layout(points, diameter=20)
    source["obstacles"] = [{"id": "end-port", "min": [990, 390, -10], "max": [1010, 410, 10]}]
    source["protected_endpoints"] = [{"endpoint": "end", "points": points[-2:], "obstacle_ids": ["end-port"], "max_distance": 250}]
    plan = plan_assembly(source, points, bend_radius_mm=300)
    assert plan["status"] == "blocked"
    assert "assembly_bend_enters_fixed_stub" in codes(plan)


def test_allowed_spaces_are_checked_after_rounding_with_error_margin():
    points = [[-500, 0, 0], [0, 0, 0], [0, 500, 0]]
    source = layout(points, diameter=20)
    # A thin L-shaped corridor contains the sharp route but not a radius-200 fillet.
    source["model_context"] = {"units": "mm", "allowed_spaces": [
        {"id": "first", "min": [-1000, -30, -30], "max": [30, 30, 30]},
        {"id": "last", "min": [-30, -30, -30], "max": [30, 1000, 30]}]}
    plan = plan_assembly(source, points, bend_radius_mm=200)
    assert plan["status"] == "blocked"
    assert "assembly_outside_allowed_space" in codes(plan)


def test_required_space_cannot_be_skipped_by_fillet():
    points = [[-500, 0, 0], [0, 0, 0], [0, 500, 0]]
    source = layout(points, diameter=2)
    source["model_context"] = {"units": "mm", "allowed_spaces": [
        {"id": "room", "min": [-1000, -1000, -1000], "max": [1000, 1000, 1000]},
        {"id": "corner", "min": [-5, -5, -5], "max": [5, 5, 5]}], "required_space_ids": ["corner"]}
    plan = plan_assembly(source, points, bend_radius_mm=200)
    assert plan["status"] == "blocked"
    assert "assembly_required_space" in codes(plan)


def test_long_straight_is_partitioned_into_bounded_cut_pieces():
    points = [[0, 0, 0], [15000, 0, 0]]
    source = layout(points)
    plan = plan_assembly(source, points)
    assert plan["status"] == "ready"
    assert len(plan["components"]) == 3
    assert all(25 <= c["length_mm"] <= MAX_STRAIGHT_PIECE_MM for c in plan["components"])
    assert plan["metrics"]["centerline_length_mm"] == 15000
    assert plan["metrics"]["bend_count"] == 0
    assert plan["metrics"]["proposed_internal_joint_count"] == 2


def test_tiny_remaining_cut_piece_does_not_hide_joint_spacing():
    points = [[0, 0, 0], [210, 0, 0], [210, 1000, 0]]
    plan = plan_assembly(layout(points), points, bend_radius_mm=200)
    assert plan["status"] == "blocked"
    assert "assembly_joint_spacing" in codes(plan)


def test_gravity_only_gets_explicit_reference_rendering():
    points = [[0, 0, 100], [1000, 0, 0]]
    source = layout(points)
    source["system_type"] = "gravity"
    source["rules"]["min_slope"] = 0.01
    plan = plan_assembly(source, points)
    assert plan["status"] == "blocked"
    assert "gravity_assembly_unsupported" in codes(plan)
    reference = render_reference(points, 40)
    assert reference["status"] == "reference_only"
    assert not reference["geometry_checked"]
    assert not reference["catalog_verified"]
    assert reference["meshes"]


@pytest.mark.parametrize("invalid", [math.inf, math.nan, True, "100"])
def test_nonfinite_or_untyped_geometry_is_rejected(invalid):
    source = layout()
    points = deepcopy(source["pipe"]["original_route"])
    points[1][0] = invalid
    with pytest.raises(ValueError):
        plan_assembly(source, points)
    with pytest.raises(ValueError):
        render_reference(source["pipe"]["original_route"], invalid)


def test_wrong_units_and_invalid_radius_raise_without_mutating_input():
    source = layout()
    snapshot = deepcopy(source)
    with pytest.raises(ValueError):
        plan_assembly(source, source["pipe"]["original_route"], bend_radius_mm=math.nan)
    source["units"] = "m"
    with pytest.raises(ValueError, match="normalized"):
        plan_assembly(source, source["pipe"]["original_route"])
    source["units"] = "mm"
    plan_assembly(source, source["pipe"]["original_route"])
    assert source == snapshot


def test_straight_repeated_samples_do_not_become_extra_parts_or_bends():
    points = [[0, 0, 0], [100, 0, 0], [200, 0, 0], [1000, 0, 0]]
    plan = plan_assembly(layout(points), points)
    assert plan["status"] == "ready"
    assert len(plan["components"]) == 1
    assert plan["metrics"]["bend_count"] == 0


def test_mesh_budget_blocks_without_allocating_unbounded_geometry():
    # A long zigzag needs many arc rings but stays below the input point cap.
    points = [[0, 0, 0]]
    for number in range(1, 221):
        points.append([number * 1000, (number % 2) * 1000, 0])
    source = layout(points, diameter=100)
    source["bounds"]["max"] = [300000, 300000, 300000]
    plan = plan_assembly(source, points, bend_radius_mm=500)
    assert plan["status"] == "blocked"
    assert "assembly_size_limit" in codes(plan)
    assert not plan["meshes"]


def test_required_space_visit_is_not_inferred_from_chord_alone():
    points = [[-500, 0, 0], [0, 0, 0], [0, 500, 0]]
    source = layout(points, diameter=0.1)
    unbounded = plan_assembly(source, points, bend_radius_mm=100)
    bend = next(c for c in unbounded["components"] if c["kind"] == "bend")
    midpoint = [(a + b) / 2 for a, b in zip(bend["centerline_points"][0], bend["centerline_points"][1])]
    source["model_context"] = {"units": "mm", "allowed_spaces": [
        {"id": "room", "min": [-1000] * 3, "max": [1000] * 3},
        {"id": "tiny", "min": [x - 0.2 for x in midpoint], "max": [x + 0.2 for x in midpoint]}],
        "required_space_ids": ["tiny"]}
    plan = plan_assembly(source, points, bend_radius_mm=100)
    assert plan["status"] == "blocked"
    assert "assembly_required_space" in codes(plan)


def test_closed_mesh_faces_have_consistent_outward_winding():
    source = layout()
    plan = plan_assembly(source, source["pipe"]["original_route"], bend_radius_mm=200)
    for mesh in plan["meshes"]:
        directed_edges = Counter(pair for face in mesh["faces"] for pair in zip(face, face[1:] + face[:1]))
        assert all(directed_edges[(b, a)] == 1 for (a, b) in directed_edges)
        volume = 0
        for a, b, c in mesh["faces"]:
            a, b, c = [mesh["vertices"][index] for index in (a, b, c)]
            volume += sum(a[i] * (b[(i + 1) % 3] * c[(i + 2) % 3] - b[(i + 2) % 3] * c[(i + 1) % 3]) for i in range(3)) / 6
        assert volume > 0


def test_reference_meshes_are_labeled_and_bounded():
    points = [[0, 0, 0], [1000, 0, 0], [1000, 1000, 0]]
    reference = render_reference(points, 100)
    assert len(reference["meshes"]) == 2
    assert all(mesh["kind"] == "reference_straight" for mesh in reference["meshes"])
    assert sum(len(mesh["vertices"]) for mesh in reference["meshes"]) <= MAX_MESH_VERTICES
    assert reference["centerline_points"] == points


def test_endpoint_change_is_never_accepted():
    source = layout()
    points = deepcopy(source["pipe"]["original_route"])
    points[-1][1] += 10
    plan = plan_assembly(source, points)
    assert plan["status"] == "blocked"
    assert "assembly_endpoint_changed" in codes(plan)


def test_nonlocal_crossing_is_blocked_even_without_scene_obstacles():
    points = [[0, 0, 0], [1000, 0, 0], [1000, 1000, 0], [500, 1000, 0], [500, -500, 0]]
    plan = plan_assembly(layout(points), points, bend_radius_mm=100)
    assert plan["status"] == "blocked"
    assert "assembly_self_interference" in codes(plan)
    assert not plan["meshes"]


def test_distant_parallel_return_with_insufficient_width_is_blocked():
    points = [[0, 0, 0], [2000, 0, 0], [2000, 1000, 0], [0, 1000, 0], [0, 25, 0], [1500, 25, 0]]
    plan = plan_assembly(layout(points, diameter=40), points, bend_radius_mm=100)
    assert plan["status"] == "blocked"
    assert "assembly_self_interference" in codes(plan)


def test_shallow_adjacent_bend_continuity_is_not_a_self_clash():
    angle = math.radians(15)
    points = [[0, 0, 0], [1000, 0, 0], [1000 + 1000 * math.cos(angle), 1000 * math.sin(angle), 0]]
    plan = plan_assembly(layout(points, diameter=40), points, bend_radius_mm=100)
    assert plan["status"] == "ready"
    assert "assembly_self_interference" not in codes(plan)
