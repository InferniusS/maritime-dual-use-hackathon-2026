"""Safety and behavior checks for the synthetic pressure-pipe prototype."""

from copy import deepcopy
from math import inf, isfinite, nan

import pytest

import pipe_router.core as core
from pipe_router.core import solve_layout, validate_route


@pytest.fixture
def layout():
    return {
        "name": "Synthetic test room",
        "provenance": "synthetic",
        "units": "mm",
        "system_type": "pressurized",
        "bounds": {"min": [0, 0, 0], "max": [100, 100, 100]},
        "obstacles": [],
        "pipe": {
            "start": [10, 10, 50],
            "end": [90, 10, 50],
            "diameter": 4,
            "original_route": [
                [10, 10, 50],
                [10, 90, 50],
                [90, 90, 50],
                [90, 10, 50],
            ],
        },
        "rules": {"clearance": 3, "grid_step": 10, "bend_penalty": 5},
        "assumptions": ["All dimensions and rules are synthetic test inputs."],
    }


def with_central_obstacle(layout):
    layout["obstacles"] = [
        {"id": "equipment", "min": [40, 40, 40], "max": [60, 60, 60]}
    ]
    layout["pipe"]["start"] = [10, 50, 50]
    layout["pipe"]["end"] = [90, 50, 50]
    layout["pipe"]["original_route"] = [[10, 50, 50], [90, 50, 50]]
    return layout


def assert_independently_safe(layout, points):
    """Check continuous segments without calling the production validator."""
    assert points[0] == layout["pipe"]["start"]
    assert points[-1] == layout["pipe"]["end"]
    margin = layout["pipe"]["diameter"] / 2 + layout["rules"]["clearance"]
    for point in points:
        assert len(point) == 3
        assert all(isfinite(value) for value in point)
        for axis in range(3):
            assert layout["bounds"]["min"][axis] + margin <= point[axis]
            assert point[axis] <= layout["bounds"]["max"][axis] - margin

    for start, end in zip(points, points[1:]):
        changed_axes = [axis for axis in range(3) if start[axis] != end[axis]]
        assert len(changed_axes) == 1
        # For an axis-aligned segment the interval on each axis is exact.
        # The segment intersects a closed box only if all three overlap.
        for obstacle in layout["obstacles"]:
            overlapping_axes = [
                max(start[axis], end[axis]) >= obstacle["min"][axis] - margin
                and min(start[axis], end[axis]) <= obstacle["max"][axis] + margin
                for axis in range(3)
            ]
            assert not all(overlapping_axes)


def test_shorter_route_preserves_connections_and_input(layout):
    before = deepcopy(layout)

    result = solve_layout(layout)

    assert result["status"] == "improved"
    assert result["original"]["length"] == pytest.approx(240)
    assert result["candidate"]["length"] == pytest.approx(80)
    assert result["candidate"]["bends"] == 0
    assert result["candidate"]["valid"]
    assert_independently_safe(layout, result["candidate"]["points"])
    assert layout == before


def test_already_shortest_route_is_not_claimed_as_improved(layout):
    layout["pipe"]["original_route"] = [[10, 10, 50], [90, 10, 50]]

    result = solve_layout(layout)

    assert result["status"] == "unchanged"
    assert result["original"]["valid"]
    assert result["original"]["length"] == pytest.approx(80)
    if result["candidate"] is not None:
        assert result["candidate"]["length"] == pytest.approx(80)
        assert_independently_safe(layout, result["candidate"]["points"])


def test_detects_obstacle_between_clear_endpoints(layout):
    with_central_obstacle(layout)

    report = validate_route(layout, layout["pipe"]["original_route"])

    assert not report["valid"]
    assert report["issues"]


def test_solver_routes_around_obstacle_with_continuous_clearance(layout):
    with_central_obstacle(layout)

    result = solve_layout(layout)

    assert result["status"] != "no_route"
    assert not result["original"]["valid"]
    assert result["candidate"] is not None
    assert result["candidate"]["valid"]
    assert result["candidate"]["length"] > 80
    assert_independently_safe(layout, result["candidate"]["points"])


@pytest.mark.parametrize("offset, expected_valid", [(64, False), (65, False), (66, True)])
def test_pipe_radius_and_clearance_apply_outside_obstacle_surface(layout, offset, expected_valid):
    with_central_obstacle(layout)
    layout["pipe"]["start"] = [10, offset, 50]
    layout["pipe"]["end"] = [90, offset, 50]
    points = [[10, offset, 50], [90, offset, 50]]

    report = validate_route(layout, points)

    assert report["valid"] is expected_valid


def test_wall_clearance_uses_pipe_radius(layout):
    points = [[10, 10, 50], [10, 4, 50], [90, 4, 50], [90, 10, 50]]

    report = validate_route(layout, points)

    assert not report["valid"]
    assert report["issues"]


def test_bounds_allow_exact_inset_boundary(layout):
    points = [[10, 10, 50], [10, 5, 50], [90, 5, 50], [90, 10, 50]]

    assert validate_route(layout, points)["valid"]


def test_route_cannot_silently_move_fixed_endpoint(layout):
    report = validate_route(layout, [[20, 10, 50], [90, 10, 50]])

    assert not report["valid"]
    assert report["issues"]


def test_diagonal_segment_is_rejected(layout):
    points = [[10, 10, 50], [20, 20, 50], [90, 20, 50], [90, 10, 50]]

    assert not validate_route(layout, points)["valid"]


def test_collinear_subdivision_is_not_a_bend(layout):
    report = validate_route(layout, [[10, 10, 50], [50, 10, 50], [90, 10, 50]])

    assert report["valid"]
    assert report["length"] == pytest.approx(80)
    assert report["bends"] == 0


def test_solid_partition_has_no_route(layout):
    layout["pipe"]["start"] = [10, 50, 50]
    layout["pipe"]["end"] = [90, 50, 50]
    layout["pipe"]["original_route"] = [[10, 50, 50], [90, 50, 50]]
    layout["obstacles"] = [
        {"id": "sealed partition", "min": [45, 0, 0], "max": [55, 100, 100]}
    ]

    result = solve_layout(layout)

    assert result["status"] == "no_route"
    assert result["candidate"] is None
    assert not result["original"]["valid"]


def test_full_width_obstacle_requires_a_vertical_detour(layout):
    layout["pipe"]["start"] = [10, 50, 20]
    layout["pipe"]["end"] = [90, 50, 20]
    layout["pipe"]["original_route"] = [[10, 50, 20], [90, 50, 20]]
    layout["obstacles"] = [
        {"id": "wide equipment", "min": [40, 0, 0], "max": [60, 100, 60]}
    ]

    result = solve_layout(layout)

    candidate = result["candidate"]
    assert candidate is not None
    assert candidate["valid"]
    assert max(point[2] for point in candidate["points"]) > 65
    assert_independently_safe(layout, candidate["points"])


def test_valid_original_survives_when_coarse_grid_misses_a_corridor(layout):
    layout["rules"]["grid_step"] = 100
    layout["pipe"]["original_route"] = [
        [10, 10, 50], [10, 70, 50], [90, 70, 50], [90, 10, 50]
    ]
    layout["obstacles"] = [
        {"id": "lower partition", "min": [45, 0, 0], "max": [55, 60, 100]},
        {"id": "upper partition", "min": [45, 80, 0], "max": [55, 100, 100]},
    ]

    result = solve_layout(layout)

    assert result["status"] == "unchanged"
    assert result["candidate"]["points"] == layout["pipe"]["original_route"]
    assert_independently_safe(layout, result["candidate"]["points"])


def test_oversized_grid_is_rejected_before_search(layout):
    layout["rules"]["grid_step"] = 0.1

    with pytest.raises(ValueError, match="[Gg]rid"):
        solve_layout(layout)


def test_search_budget_does_not_report_no_route(layout, monkeypatch):
    monkeypatch.setattr(core, "MAX_EXPANDED_STATES", 0)

    with pytest.raises(RuntimeError, match="Search limit"):
        solve_layout(layout)


@pytest.mark.parametrize(
    "section, field, value",
    [
        ("rules", "clearance", -1),
        ("rules", "clearance", nan),
        ("rules", "grid_step", 0),
        ("rules", "grid_step", -1),
        ("rules", "grid_step", 5e-324),
        ("rules", "grid_step", inf),
        ("rules", "bend_penalty", -1),
        ("rules", "bend_penalty", nan),
        ("pipe", "diameter", 0),
        ("pipe", "diameter", -1),
        ("pipe", "diameter", inf),
        ("pipe", "start", [nan, 10, 50]),
    ],
)
def test_invalid_configuration_is_rejected(layout, section, field, value):
    layout[section][field] = value

    with pytest.raises(ValueError):
        solve_layout(layout)


@pytest.mark.parametrize("points", [[], [[10, 10]], [[10, 10, 50], [inf, 10, 50]], None])
def test_malformed_route_reports_invalid(layout, points):
    report = validate_route(layout, points)

    assert not report["valid"]
    assert report["issues"]


def test_solver_rejects_nonfinite_original_route(layout):
    layout["pipe"]["original_route"] = [[10, 10, 50], [nan, 10, 50], [90, 10, 50]]

    with pytest.raises(ValueError):
        solve_layout(layout)


def test_gravity_system_is_not_treated_as_a_pressure_system(layout):
    layout["system_type"] = "gravity"

    with pytest.raises(ValueError):
        solve_layout(layout)
