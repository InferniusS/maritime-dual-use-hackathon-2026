"""Controlled detours retain their baseline and never bypass real checks."""

from copy import deepcopy
import math

import pytest

from pipe_router.benchmarks import make_detour_case
from pipe_router.workflow import run_workflow, validate_layout_route


@pytest.fixture
def layout():
    # Explicit geometry stands in for a previously imported CAD route. No
    # supplied ship file is required or claimed by these behavior tests.
    return {
        "name": "Known straight CAD test route", "provenance": "cad-derived",
        "units": "mm", "system_type": "pressurized",
        "bounds": {"min": [0, 0, 0], "max": [1000, 1000, 1000]},
        "obstacles": [],
        "pipe": {"start": [100, 500, 500], "end": [900, 500, 500],
                 "diameter": 20, "original_route": [[100, 500, 500], [900, 500, 500]]},
        "rules": {"clearance": 0, "grid_step": 100, "bend_penalty": 300},
        "assumptions": ["Constructed geometry for the CAD benchmark tests"],
        "source": {"file": "fixture.dxf", "sha256": "fixture-source-hash"},
        "cad_context": {"candidate_id": "fixture-run", "cad_id": "a" * 32,
                        "requires_review": True, "source_handles": ["42"]},
    }


def is_subsequence(points, expected):
    remaining = iter(points)
    return all(any(point == item for point in remaining) for item in expected)


def test_detour_is_checked_deterministic_and_explicitly_synthetic(layout):
    before = deepcopy(layout)
    case = make_detour_case(layout)
    assert layout == before
    assert make_detour_case(layout) == case
    assert case["provenance"] == "cad-derived"
    assert case["benchmark"]["synthetic_mutation"]
    assert "Not an observed defect" in case["benchmark"]["label"]
    assert case["benchmark"]["baseline"]["points"] == layout["pipe"]["original_route"]
    assert case["benchmark"]["baseline"]["source"] == layout["source"]
    assert case["benchmark"]["baseline_layout"]["pipe"]["original_route"] == layout["pipe"]["original_route"]
    assert case["benchmark"]["baseline_layout"]["obstacles"] == layout["obstacles"]
    assert case["cad_context"] == layout["cad_context"]
    assert case["obstacles"] == layout["obstacles"]
    assert case["rules"] == layout["rules"]
    assert case["pipe"]["diameter"] == layout["pipe"]["diameter"]
    assert case["pipe"]["start"] == layout["pipe"]["start"]
    assert case["pipe"]["end"] == layout["pipe"]["end"]
    points = case["pipe"]["original_route"]
    assert is_subsequence(points, layout["pipe"]["original_route"])
    assert len(points) == 6
    assert sum(math.dist(a, b) for a, b in zip(points, points[1:])) == 850
    assert case["benchmark"]["mutation"]["added_length_mm"] == 50
    assert validate_layout_route(case, points)["valid"]


def test_baseline_control_stays_unchanged_and_detour_can_be_removed(layout):
    control = run_workflow(layout)
    assert control["status"] == "unchanged"
    assert control["candidate"]["length"] == 800
    case = make_detour_case(layout)
    result = run_workflow(case)
    assert result["status"] == "improved"
    assert result["original"]["length"] == 850
    assert result["candidate"]["length"] == control["candidate"]["length"]
    assert result["candidate"]["valid"]


def test_collision_trial_is_rejected_before_an_alternate_offset_is_used(layout):
    layout["obstacles"] = [{"id": "positive-side-equipment", "min": [450, 515, 480], "max": [550, 540, 520]}]
    case = make_detour_case(layout)
    assert case["benchmark"]["mutation"]["offset_axis"] == "y"
    assert case["benchmark"]["mutation"]["offset_sign"] == -1
    assert case["benchmark"]["mutation"]["trials"] == 2
    assert case["obstacles"] == layout["obstacles"]
    assert min(point[1] for point in case["pipe"]["original_route"]) == 475


def test_invalid_original_is_rejected_without_ignoring_obstacles(layout):
    layout["obstacles"] = [{"id": "crossing-wall", "min": [450, 0, 0], "max": [550, 1000, 1000]}]
    before = deepcopy(layout)
    with pytest.raises(ValueError, match="baseline must pass.*obstacle_collision"):
        make_detour_case(layout)
    assert layout == before


def test_sealed_tight_corridor_keeps_valid_baseline_but_has_no_test_detour(layout):
    layout["bounds"] = {"min": [0, 480, 480], "max": [1000, 520, 520]}
    before = deepcopy(layout)
    assert validate_layout_route(layout, layout["pipe"]["original_route"])["valid"]
    with pytest.raises(ValueError, match="No checked detour found"):
        make_detour_case(layout)
    assert layout == before


def test_all_baseline_curve_samples_are_preserved(layout):
    layout["pipe"]["original_route"] = [[100, 500, 500], [300, 500, 500], [400, 550, 500], [500, 600, 500], [900, 500, 500]]
    case = make_detour_case(layout)
    assert is_subsequence(case["pipe"]["original_route"], layout["pipe"]["original_route"])
    assert case["benchmark"]["mutation"]["segment_index"] == 0
    assert case["pipe"]["original_route"][-4:] == layout["pipe"]["original_route"][-4:]


def test_arbitrary_curve_is_not_replaced_by_a_straight_shortcut(layout):
    layout["pipe"]["original_route"] = [[100, 500, 500], [300, 550, 500], [500, 600, 500], [900, 500, 500]]
    with pytest.raises(ValueError, match="No unprotected axis-aligned straight segment"):
        make_detour_case(layout)


def test_fixed_endpoint_stubs_remain_exactly_unchanged(layout):
    points = [[100, 500, 500], [200, 500, 500], [800, 500, 500], [900, 500, 500]]
    layout["pipe"]["original_route"] = points
    layout["obstacles"] = [
        {"id": "start-connector", "min": [80, 480, 480], "max": [150, 520, 520]},
        {"id": "end-connector", "min": [850, 480, 480], "max": [920, 520, 520]},
    ]
    layout["protected_endpoints"] = [
        {"endpoint": "start", "points": points[:2], "obstacle_ids": ["start-connector"], "max_distance": 250},
        {"endpoint": "end", "points": points[-2:], "obstacle_ids": ["end-connector"], "max_distance": 250},
    ]
    case = make_detour_case(layout)
    assert case["pipe"]["original_route"][:2] == points[:2]
    assert case["pipe"]["original_route"][-2:] == points[-2:]
    assert case["protected_endpoints"] == layout["protected_endpoints"]
    assert case["benchmark"]["mutation"]["segment_index"] == 1


def test_no_mutation_is_allowed_inside_a_fully_protected_route(layout):
    layout["pipe"]["end"] = [200, 500, 500]
    layout["pipe"]["original_route"] = [layout["pipe"]["start"], layout["pipe"]["end"]]
    layout["obstacles"] = [{"id": "start-connector", "min": [80, 480, 480], "max": [150, 520, 520]}]
    points = layout["pipe"]["original_route"]
    layout["protected_endpoints"] = [{"endpoint": "start", "points": points, "obstacle_ids": ["start-connector"], "max_distance": 250}]
    with pytest.raises(ValueError, match="movable middle section"):
        make_detour_case(layout)


def test_bend_radius_check_rejects_detours_with_insufficient_tangent_room(layout):
    layout["rules"]["bend_radius"] = 30
    case = make_detour_case(layout)
    assert case["benchmark"]["mutation"]["offset_mm"] >= 100
    assert case["benchmark"]["validation"]["valid"]


def test_input_units_are_normalized_before_adding_fixed_millimeter_offsets(layout):
    layout["units"] = "in"
    for record in [layout["bounds"]]:
        for key in ("min", "max"):
            record[key] = [value / 25.4 for value in record[key]]
    for key in ("start", "end"):
        layout["pipe"][key] = [value / 25.4 for value in layout["pipe"][key]]
    layout["pipe"]["original_route"] = [[value / 25.4 for value in point] for point in layout["pipe"]["original_route"]]
    layout["pipe"]["diameter"] /= 25.4
    for key in layout["rules"]:
        layout["rules"][key] /= 25.4
    case = make_detour_case(layout)
    assert case["units"] == "mm"
    assert case["benchmark"]["source_input_units"] == "in"
    assert case["benchmark"]["mutation"]["added_length_mm"] == pytest.approx(50)
    assert case["pipe"]["diameter"] == pytest.approx(20)


@pytest.mark.parametrize("changed", [{"provenance": "synthetic"}, {"system_type": "gravity"}])
def test_unsupported_source_or_system_is_rejected(layout, changed):
    layout.update(changed)
    with pytest.raises(ValueError):
        make_detour_case(layout)


def test_a_second_synthetic_mutation_cannot_hide_the_baseline(layout):
    first = make_detour_case(layout)
    with pytest.raises(ValueError, match="already has a synthetic mutation"):
        make_detour_case(first)


def test_explicit_offset_is_honored_without_falling_back_to_a_smaller_one(layout):
    case = make_detour_case(layout, offset_mm=250)
    assert case["benchmark"]["mutation"]["offset_mm"] == 250
    assert case["benchmark"]["mutation"]["added_length_mm"] == 500
    assert case["benchmark"]["validation"]["length"] == 1300
    layout["bounds"] = {"min": [0, 450, 450], "max": [1000, 550, 550]}
    assert make_detour_case(layout)["benchmark"]["mutation"]["offset_mm"] == 25
    with pytest.raises(ValueError, match="No checked detour found"):
        make_detour_case(layout, offset_mm=250)


@pytest.mark.parametrize("offset", [0, -1, 1001, float("nan"), float("inf"), True, "250", []])
def test_explicit_offset_rejects_invalid_or_excessive_values(layout, offset):
    with pytest.raises(ValueError, match="offset_mm"):
        make_detour_case(layout, offset_mm=offset)
