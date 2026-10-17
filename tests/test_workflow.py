"""Workflow behavior, provenance, conversion, and sloped geometry checks."""

from copy import deepcopy
import math

import pytest

from pipe_router.workflow import normalize_layout, run_workflow, validate_layout_route, with_endpoint_connections


@pytest.fixture
def layout():
    return {
        "name": "Explicit test geometry",
        "provenance": "synthetic",
        "units": "mm",
        "system_type": "pressurized",
        "bounds": {"min": [0, 0, 0], "max": [100, 100, 100]},
        "obstacles": [],
        "pipe": {
            "start": [10, 10, 50], "end": [90, 10, 50], "diameter": 4,
            "original_route": [[10, 10, 50], [10, 90, 50], [90, 90, 50], [90, 10, 50]],
        },
        "rules": {"clearance": 3, "grid_step": 10, "bend_penalty": 5},
        "assumptions": ["Invented geometry for tests"],
    }


def as_gravity(layout, *, start=None, end=None):
    layout["system_type"] = "gravity"
    layout["pipe"]["start"] = start or [10, 10, 70]
    layout["pipe"]["end"] = end or [90, 10, 30]
    layout["pipe"]["original_route"] = [layout["pipe"]["start"], layout["pipe"]["end"]]
    layout["rules"]["min_slope"] = 0.1
    return layout


def test_pressure_delegation_preserves_behavior_and_input(layout):
    before = deepcopy(layout)
    result = run_workflow(layout)
    assert layout == before
    assert result["status"] == "improved"
    assert result["candidate"]["length"] == pytest.approx(80)
    assert result["candidate"]["bends"] == 0
    assert result["candidate"]["valid"]
    assert result["rule_profile"] == "prototype-unverified"
    assert not result["checks"]["official_standards_compliance"]


def test_inch_conversion_scales_all_length_fields_but_not_slope(layout):
    as_gravity(layout)
    layout["units"] = "in"
    layout["obstacles"] = [{"id": "box", "min": [40, 40, 40], "max": [60, 60, 60]}]
    layout["rules"]["bend_radius"] = 2
    result = normalize_layout(layout)
    assert result["pipe"]["start"] == pytest.approx([254, 254, 1778])
    assert result["pipe"]["original_route"][1] == pytest.approx([2286, 254, 762])
    assert result["bounds"]["max"] == pytest.approx([2540] * 3)
    assert result["obstacles"][0]["min"] == pytest.approx([1016] * 3)
    assert result["pipe"]["diameter"] == pytest.approx(101.6)
    assert result["rules"]["clearance"] == pytest.approx(76.2)
    assert result["rules"]["grid_step"] == pytest.approx(254)
    assert result["rules"]["bend_penalty"] == pytest.approx(127)
    assert result["rules"]["bend_radius"] == pytest.approx(50.8)
    assert result["rules"]["min_slope"] == 0.1


def test_equivalent_meter_and_millimeter_layouts_have_same_result(layout):
    expected = run_workflow(layout)
    meters = deepcopy(layout)
    meters["units"] = "m"
    for key in ("min", "max"):
        meters["bounds"][key] = [value / 1000 for value in meters["bounds"][key]]
    for key in ("start", "end"):
        meters["pipe"][key] = [value / 1000 for value in meters["pipe"][key]]
    meters["pipe"]["original_route"] = [[value / 1000 for value in point] for point in meters["pipe"]["original_route"]]
    meters["pipe"]["diameter"] /= 1000
    for key in meters["rules"]:
        meters["rules"][key] /= 1000
    actual = run_workflow(meters)
    assert actual["candidate"] == expected["candidate"]
    assert actual["input_units"] == "m"
    assert actual["units"] == "mm"


def test_public_validator_uses_input_units_and_reports_mm(layout):
    layout["units"] = "in"
    record = validate_layout_route(layout, [layout["pipe"]["start"], layout["pipe"]["end"]])
    assert record["valid"]
    assert record["length"] == pytest.approx(80 * 25.4)


def test_cad_provenance_and_import_caveats_are_not_rewritten(layout):
    layout["provenance"] = "cad-derived"
    layout["assumptions"] = ["Coordinates manually extracted from the supplied drawing"]
    layout["source"] = {"filename": "cooling.dwg", "method": "manual points"}
    result = run_workflow(layout)
    assert result["provenance"] == "cad-derived"
    assert result["normalized_layout"]["provenance"] == "cad-derived"
    assert result["normalized_layout"]["source"] == layout["source"]
    assert layout["assumptions"][0] in result["assumptions"]
    assert not any("synthetic" in text.lower() for text in result["assumptions"] + result["limitations"])
    assert any("extraction fidelity" in text.lower() for text in result["limitations"])


def test_missing_units_are_explicit_assumptions(layout):
    del layout["units"]
    result = run_workflow(layout)
    assert result["input_units"] == "mm"
    assert any("provisionally assumed" in text for text in result["assumptions"])


def test_default_rules_are_mm_even_when_input_is_inches(layout):
    layout["units"] = "in"
    layout["rules"] = {}
    result = normalize_layout(layout)
    assert result["rules"] == {"clearance": 50, "grid_step": 250, "bend_penalty": 300}


@pytest.mark.parametrize("units", ["feet", None, [], 4])
def test_unknown_units_raise_instead_of_guessing(layout, units):
    layout["units"] = units
    with pytest.raises(ValueError, match="units"):
        run_workflow(layout)


def test_gravity_accepts_a_correct_sloped_original(layout):
    as_gravity(layout)
    result = run_workflow(layout)
    assert result["status"] == "unchanged"
    assert result["original"]["valid"]
    assert result["candidate"]["minimum_segment_slope"] == pytest.approx(0.5)
    assert result["candidate"]["length"] == pytest.approx(math.hypot(80, 40))
    assert result["checks"]["gravity_slope"]


def test_gravity_detours_around_collision_at_actual_height(layout):
    as_gravity(layout)
    layout["obstacles"] = [{"id": "equipment", "min": [40, 5, 40], "max": [60, 20, 60]}]
    result = run_workflow(layout)
    assert not result["original"]["valid"]
    assert any(issue["code"] == "obstacle_collision" for issue in result["original"]["issues"])
    candidate = result["candidate"]
    assert candidate is not None and candidate["valid"]
    assert candidate["points"][0] == layout["pipe"]["start"]
    assert candidate["points"][-1] == layout["pipe"]["end"]
    assert any(point[1] > 25 for point in candidate["points"])
    for start, end in zip(candidate["points"], candidate["points"][1:]):
        fall = start[2] - end[2]
        horizontal = math.hypot(end[0] - start[0], end[1] - start[1])
        assert fall >= horizontal * 0.1 - 1e-8
        # Independent dense samples corroborate the continuous production check.
        for step in range(101):
            sample = [a + (b - a) * step / 100 for a, b in zip(start, end)]
            assert not all(low <= value <= high for value, low, high in zip(sample, [35, 0, 35], [65, 25, 65]))


def test_gravity_does_not_treat_overhead_box_as_blocking_xy_projection(layout):
    as_gravity(layout)
    layout["obstacles"] = [{"id": "overhead", "min": [40, 5, 80], "max": [60, 20, 90]}]
    result = run_workflow(layout)
    assert result["status"] == "unchanged"
    assert result["candidate"]["valid"]
    assert result["candidate"]["bends"] == 0


def test_continuous_sloped_collision_is_caught_between_endpoints(layout):
    as_gravity(layout)
    layout["obstacles"] = [{"id": "thin-wall", "min": [49, 9, 49], "max": [51, 11, 51]}]
    record = validate_layout_route(layout, layout["pipe"]["original_route"])
    assert not record["valid"]
    assert any(issue["code"] == "obstacle_collision" for issue in record["issues"])
    assert not any(issue["code"] == "non_orthogonal" for issue in record["issues"])


def test_insufficient_endpoint_fall_has_no_accepted_candidate(layout):
    as_gravity(layout, end=[90, 10, 69])
    result = run_workflow(layout)
    assert result["status"] == "no_route"
    assert result["candidate"] is None
    assert any(issue["code"] == "insufficient_slope" for issue in result["original"]["issues"])
    assert "endpoint fall is insufficient" in result["search"]["reason"]


def test_uphill_segments_fail_even_when_total_fall_is_positive(layout):
    as_gravity(layout)
    record = validate_layout_route(layout, [[10, 10, 70], [40, 10, 80], [90, 10, 30]])
    assert not record["valid"]
    assert any(issue["code"] == "uphill_flow" for issue in record["issues"])


def test_downward_vertical_drain_is_supported(layout):
    as_gravity(layout, end=[10, 10, 30])
    result = run_workflow(layout)
    assert result["candidate"]["valid"]
    assert result["candidate"]["minimum_segment_slope"] is None
    assert result["candidate"]["length"] == 40


def test_sealed_partition_returns_honest_bounded_search_failure(layout):
    as_gravity(layout)
    layout["obstacles"] = [{"id": "wall", "min": [45, 0, 0], "max": [55, 100, 100]}]
    result = run_workflow(layout)
    assert result["status"] == "no_route"
    assert result["candidate"] is None
    assert "Other routes may exist" in result["search"]["reason"]


def test_bend_radius_checks_two_adjacent_tangent_allowances(layout):
    layout["rules"]["bend_radius"] = 8
    points = [[10, 10, 50], [10, 20, 50], [20, 20, 50], [20, 10, 50], [90, 10, 50]]
    record = validate_layout_route(layout, points)
    assert not record["valid"]
    issues = [issue for issue in record["issues"] if issue["code"] == "insufficient_bend_space"]
    assert any(issue["required_length_mm"] == pytest.approx(16) for issue in issues)


def test_fitting_failure_never_produces_a_valid_candidate(layout):
    layout["pipe"]["end"] = [90, 90, 50]
    layout["pipe"]["original_route"] = [[10, 10, 50], [10, 90, 50], [90, 90, 50]]
    layout["rules"]["bend_radius"] = 1000
    result = run_workflow(layout)
    assert result["candidate"] is None
    assert result["status"] == "no_route"
    assert result["search"]["rejected_candidate_issues"]


def test_no_radius_is_unchecked_not_fitting_compliant(layout):
    result = run_workflow(layout)
    assert not result["checks"]["bend_tangent_space"]
    assert not result["checks"]["fitting_catalog_compliance"]
    assert any("have not been checked" in item for item in result["limitations"])


def test_fixed_waypoints_are_rejected_instead_of_silently_removed(layout):
    layout["pipe"]["fixed_waypoints"] = [[10, 90, 50]]
    with pytest.raises(ValueError, match="waypoints"):
        run_workflow(layout)


@pytest.mark.parametrize("points", [None, [], [[10, 10, 50]], [[10, 10, 50], [float("nan"), 10, 50]]])
def test_malformed_public_route_returns_invalid(layout, points):
    assert not validate_layout_route(layout, points)["valid"]


def test_gravity_default_slope_is_explicitly_provisional(layout):
    as_gravity(layout)
    del layout["rules"]["min_slope"]
    result = run_workflow(layout)
    assert result["normalized_layout"]["rules"]["min_slope"] == 0.01
    assert any("not a verified engineering standard" in item for item in result["assumptions"])


def test_cad_pressure_original_allows_arbitrary_direction_without_snapping(layout):
    layout["provenance"] = "cad-derived"
    layout["pipe"]["end"] = [90, 90, 50]
    layout["pipe"]["original_route"] = [[10, 10, 50], [30, 30, 50], [90, 90, 50]]
    before = deepcopy(layout)
    result = run_workflow(layout)
    assert layout == before
    assert result["original"]["valid"]
    assert result["status"] == "unchanged"
    assert result["candidate"]["points"] == before["pipe"]["original_route"]
    assert result["candidate"]["length"] == pytest.approx(math.hypot(80, 80))
    assert result["original"]["bend_count_kind"] == "polyline_direction_changes_not_fitting_count"


def test_cad_sampled_curve_is_retained_without_axis_aligned_false_error(layout):
    layout["provenance"] = "cad-derived"
    layout["pipe"]["end"] = [90, 90, 50]
    layout["pipe"]["route_representation"] = "sampled_cad_centerline"
    layout["pipe"]["original_route"] = [[10, 10, 50], [25, 15, 50], [50, 35, 50], [70, 60, 50], [90, 90, 50]]
    result = run_workflow(layout)
    assert result["original"]["valid"]
    assert result["status"] == "unchanged"
    assert result["original"]["route_representation"] == "sampled_cad_centerline"
    assert result["candidate"]["points"] == layout["pipe"]["original_route"]
    assert any("between samples is not checked exactly" in text for text in result["limitations"])


def test_cad_diagonal_collisions_are_checked_continuously(layout):
    layout["provenance"] = "cad-derived"
    layout["pipe"]["end"] = [90, 90, 50]
    layout["pipe"]["original_route"] = [[10, 10, 50], [90, 90, 50]]
    layout["obstacles"] = [{"id": "middle", "min": [49, 49, 49], "max": [51, 51, 51]}]
    result = run_workflow(layout)
    codes = {issue["code"] for issue in result["original"]["issues"]}
    assert "obstacle_collision" in codes
    assert "non_orthogonal" not in codes
    assert result["candidate"]["valid"]
    # Existing CAD may be non-orthogonal. Newly proposed pressure edges are not.
    for start, end in zip(result["candidate"]["points"], result["candidate"]["points"][1:]):
        assert sum(a != b for a, b in zip(start, end)) == 1


def test_cad_fewer_sampled_direction_changes_alone_are_not_improvement(layout):
    layout["provenance"] = "cad-derived"
    layout["pipe"]["end"] = [90, 90, 50]
    layout["pipe"]["original_route"] = [
        [10, 10, 50], [30, 10, 50], [30, 30, 50], [50, 30, 50],
        [50, 50, 50], [90, 50, 50], [90, 90, 50],
    ]
    result = run_workflow(layout)
    assert result["status"] == "unchanged"
    assert result["comparison_objective"] == "polyline_length_only"
    assert result["candidate"]["points"] == layout["pipe"]["original_route"]
    assert result["candidate"]["length"] == 160
    assert result["candidate"]["bends"] == 5


def test_cad_context_warnings_and_assumptions_survive_workflow(layout):
    layout["provenance"] = "cad-derived"
    layout["cad_context"] = {
        "selected_run": "run-17", "region": {"padding_mm": 500},
        "assumptions": ["Only local neighbors were extracted"],
        "limitations": ["Hull geometry is missing from this selection"],
        "warnings": ["The pipe diameter is inferred from the selected surface"],
        "source_warnings": ["Unsupported entities remain outside the check"],
    }
    result = run_workflow(layout)
    assert result["cad_context"] == layout["cad_context"]
    assert result["normalized_layout"]["cad_context"] == layout["cad_context"]
    assert "Only local neighbors were extracted" in result["assumptions"]
    assert "Hull geometry is missing from this selection" in result["limitations"]
    assert "The pipe diameter is inferred from the selected surface" in result["limitations"]
    assert "Unsupported entities remain outside the check" in result["limitations"]


def test_synthetic_pressure_still_rejects_diagonal_original(layout):
    layout["pipe"]["end"] = [90, 90, 50]
    layout["pipe"]["original_route"] = [[10, 10, 50], [90, 90, 50]]
    result = run_workflow(layout)
    assert not result["original"]["valid"]
    assert any(issue["code"] == "non_orthogonal" for issue in result["original"]["issues"])


def test_public_cad_route_validator_accepts_non_orthogonal_geometry(layout):
    layout["provenance"] = "cad-derived"
    layout["pipe"]["end"] = [90, 90, 50]
    assert validate_layout_route(layout, [[10, 10, 50], [90, 90, 50]])["valid"]


def connector_layout(layout):
    layout["provenance"] = "cad-derived"
    layout["obstacles"] = [
        {"id": "start-connector", "min": [0, 0, 40], "max": [12, 20, 60]},
        {"id": "end-connector", "min": [88, 0, 40], "max": [100, 20, 60]},
    ]
    return layout


def protect_connectors(layout):
    return with_endpoint_connections(connector_layout(layout), {
        "start": ["start-connector"], "end": ["end-connector"],
    }, max_stub_length=25)


def test_explicit_connectors_preserve_stubs_and_route_only_free_middle(layout):
    protected = protect_connectors(layout)
    result = run_workflow(protected)
    assert result["original"]["valid"]
    assert result["candidate"]["valid"]
    assert result["status"] == "improved"
    assert len(result["normalized_layout"]["obstacles"]) == 2
    for stub in protected["protected_endpoints"]:
        actual = result["candidate"]["points"][:len(stub["points"])] if stub["endpoint"] == "start" else result["candidate"]["points"][-len(stub["points"]):]
        assert actual == stub["points"]
        assert sum(math.dist(a, b) for a, b in zip(actual, actual[1:])) <= 25
    assert result["candidate"]["points"][0] == [10, 10, 50]
    assert result["candidate"]["points"][-1] == [90, 10, 50]
    assert result["candidate"]["endpoint_contact_assumptions_applied"]
    assert "All obstacle boxes remain active" in result["search"]["scope"]


def test_unselected_endpoint_contact_stays_an_error(layout):
    connector_layout(layout)
    layout["obstacles"].append({"id": "unknown-equipment", "min": [5, 0, 45], "max": [15, 30, 55]})
    protected = with_endpoint_connections(layout, {"start": ["start-connector"], "end": ["end-connector"]}, 25)
    result = run_workflow(protected)
    assert result["candidate"] is None
    assert not result["original"]["valid"]
    assert any(issue.get("obstacle") == "unknown-equipment" for issue in result["original"]["issues"])


def test_endpoint_exemption_does_not_allow_middle_to_enter_same_connector(layout):
    protected = protect_connectors(layout)
    start = next(item for item in protected["protected_endpoints"] if item["endpoint"] == "start")
    end = next(item for item in protected["protected_endpoints"] if item["endpoint"] == "end")
    points = start["points"] + [[10, 50, 50], [10, 10, 50], [90, 50, 50]] + end["points"]
    record = validate_layout_route(protected, points)
    assert not record["valid"]
    assert any(issue.get("obstacle") == "start-connector" and issue["segment"] >= len(start["points"]) - 1 for issue in record["issues"])


def test_endpoint_geometry_cannot_be_changed_under_contact_assumption(layout):
    protected = protect_connectors(layout)
    points = deepcopy(protected["pipe"]["original_route"])
    points[1][0] += 1
    record = validate_layout_route(protected, points)
    assert not record["valid"]
    assert any(issue["code"] == "protected_endpoint_changed" for issue in record["issues"])


def test_contact_assumptions_are_off_until_explicitly_selected(layout):
    result = run_workflow(connector_layout(layout))
    assert result["candidate"] is None
    assert not result["original"]["valid"]


def test_connection_stub_adds_points_without_changing_original_geometry(layout):
    connector_layout(layout)
    before = deepcopy(layout)
    protected = with_endpoint_connections(layout, {"start": ["start-connector"], "end": ["end-connector"]}, 25)
    assert layout == before
    original = before["pipe"]["original_route"]
    expanded = protected["pipe"]["original_route"]
    assert all(point in expanded for point in original)
    assert sum(math.dist(a, b) for a, b in zip(expanded, expanded[1:])) == pytest.approx(sum(math.dist(a, b) for a, b in zip(original, original[1:])))


@pytest.mark.parametrize("maximum", [1, 1001, float("inf"), -5])
def test_invalid_or_insufficient_stub_length_is_rejected(layout, maximum):
    connector_layout(layout)
    with pytest.raises(ValueError):
        with_endpoint_connections(layout, {"start": ["start-connector"]}, maximum)


def test_arbitrary_far_obstacle_cannot_be_declared_endpoint_connector(layout):
    connector_layout(layout)
    with pytest.raises(ValueError, match="touch"):
        with_endpoint_connections(layout, {"start": ["end-connector"]}, 25)


def test_connector_covering_whole_route_is_not_blanket_ignored(layout):
    layout["provenance"] = "cad-derived"
    layout["obstacles"] = [{"id": "whole-body", "min": [0, 0, 0], "max": [100, 100, 100]}]
    with pytest.raises(ValueError, match="whole original route"):
        with_endpoint_connections(layout, {"start": ["whole-body"]}, 250)


def test_changed_stub_limit_cannot_expand_exemption_past_1000mm(layout):
    protected = protect_connectors(layout)
    protected["protected_endpoints"][0]["max_distance"] = 1001
    with pytest.raises(ValueError, match="1000"):
        run_workflow(protected)


@pytest.mark.parametrize("connections", [False, [], "", 0, {"middle": []}, {"start": False}, {"start": "connector"}, {"start": [False]}])
def test_malformed_connection_options_raise_value_error(layout, connections):
    with pytest.raises(ValueError):
        with_endpoint_connections(layout, connections)


@pytest.mark.parametrize("connections", [None, {}, {"start": [], "end": []}])
def test_empty_connection_selections_create_no_waiver(layout, connections):
    result = with_endpoint_connections(layout, connections)
    assert not result.get("protected_endpoints")
    assert not any("Explicit endpoint connector" in text for text in result["assumptions"])


@pytest.mark.parametrize("maximum", [False, 0, -1, 1001, float("nan"), "250"])
def test_stub_limit_is_validated_even_without_selected_contacts(layout, maximum):
    with pytest.raises(ValueError):
        with_endpoint_connections(layout, None, maximum)


def test_protected_endpoint_coordinates_and_limits_scale_with_input_units(layout):
    protected = protect_connectors(layout)
    inches = deepcopy(protected)
    inches["units"] = "in"
    for field in ("min", "max"):
        inches["bounds"][field] = [value / 25.4 for value in inches["bounds"][field]]
        for obstacle in inches["obstacles"]:
            obstacle[field] = [value / 25.4 for value in obstacle[field]]
    for field in ("start", "end"):
        inches["pipe"][field] = [value / 25.4 for value in inches["pipe"][field]]
    inches["pipe"]["original_route"] = [[value / 25.4 for value in point] for point in inches["pipe"]["original_route"]]
    inches["pipe"]["diameter"] /= 25.4
    for field in ("clearance", "grid_step", "bend_penalty"):
        inches["rules"][field] /= 25.4
    for record in inches["protected_endpoints"]:
        record["points"] = [[value / 25.4 for value in point] for point in record["points"]]
        record["max_distance"] /= 25.4
    result = run_workflow(inches)
    assert result["input_units"] == "in"
    assert result["candidate"]["valid"]
    assert result["protected_endpoints"][0]["max_distance"] == pytest.approx(25)
    for actual, expected in zip(result["candidate"]["points"], run_workflow(protected)["candidate"]["points"]):
        assert actual == pytest.approx(expected)


def test_full_route_retention_overrides_inner_success_message(layout, monkeypatch):
    import pipe_router.workflow as workflow
    protected = protect_connectors(layout)
    protected["cad_context"] = {"bounds_scope": "assumed_local_search_box", "ship_boundary_verified": False}
    original_solver = workflow._run_without_protected_search

    def worse_middle(inner):
        result = original_solver(inner)
        points = [inner["pipe"]["start"], [10, 95, 50], [90, 95, 50], inner["pipe"]["end"]]
        result["candidate"] = {"points": points, **validate_layout_route(inner, points)}
        result["status"] = "improved"
        result["search"]["reason"] = "Found an inner route"
        return result

    monkeypatch.setattr(workflow, "_run_without_protected_search", worse_middle)
    result = workflow.run_workflow(protected)
    assert result["status"] == "unchanged"
    assert result["candidate"]["points"] == protected["pipe"]["original_route"]
    assert result["search"]["reason"].startswith("Original route retained")
    assert result["search"]["middle_search_reason"] == "Found an inner route"
    assert result["search"]["bounds_scope"] == "assumed_local_search_box"
    assert result["cad_context"]["ship_boundary_verified"] is False


def test_rejected_full_route_reason_does_not_claim_inner_search_success(layout):
    connector_layout(layout)
    layout["obstacles"].append({"id": "unselected", "min": [5, 0, 45], "max": [15, 15, 55]})
    protected = with_endpoint_connections(layout, {"start": ["start-connector"], "end": ["end-connector"]}, 25)
    result = run_workflow(protected)
    assert result["candidate"] is None
    assert result["search"]["reason"].startswith("No accepted full route")
