from copy import deepcopy
import json

import pytest

from pipe_router.challenge_rules import (
    ANGLE_TOLERANCE_DEG,
    LENGTH_TOLERANCE_MM,
    PROFILE_ID,
    assess_layout,
    bend_requirements,
    directional_minimum_fall,
    directional_requirements,
    validate_engineering_metadata,
)


def layout():
    points = [[0, 0, 1000], [1000, 0, 1000]]
    return {
        "units": "mm", "provenance": "synthetic", "system_type": "pressurized",
        "bounds": {"min": [-1000, -1000, -1000], "max": [2000, 2000, 2000]},
        "pipe": {"start": points[0], "end": points[-1], "diameter": 100, "original_route": points},
        "obstacles": [{"id": "wall", "min": [400, 300, 900], "max": [500, 400, 1100]}],
        "rules": {"clearance": 50, "grid_step": 250, "bend_penalty": 300},
    }


def engineering_fixture(points):
    """Complete, source-bound observations for the public metadata contract."""
    return {
        "units": "mm", "route_points": deepcopy(points), "source_refs": ["review:assembly-A"],
        "nominal_diameter_mm": 50, "nominal_size_basis_verified": True,
        "ship_axes": {"longitudinal": "+X", "transverse": "+Y", "up": "+Z", "confirmed": True},
        "flow_direction": "start_to_end",
        "inventory_complete": {"pieces": True, "hangers": True, "walkways": True, "valves": True},
        "pieces": [{"id": "P1", "source_refs": ["drawing:P1"], "centerline_length_mm": 1000,
                    "bends": [{"radius_mm": 152.4, "angle_deg": 90}, {"radius_mm": 152.4, "angle_deg": 45}],
                    "straight_between_bends_mm": [101.6], "weld_spacings_mm": [25],
                    "planarity_deviation_mm": 1, "planarity_tolerance_mm": 1}],
        "hangers": [{"id": "HV", "source_refs": ["drawing:HV"], "orientation": "vertical", "distance_mm": 381},
                    {"id": "HH", "source_refs": ["drawing:HH"], "orientation": "horizontal", "distance_mm": 50.8}],
        "walkways": [{"id": "W1", "source_refs": ["drawing:W1"], "headroom_mm": 1960}],
        "valves": [{"id": "V1", "source_refs": ["drawing:V1"], "orientation": "horizontal", "height_above_deck_mm": 229}],
        "evidence": {key: {"verified": True, "source_refs": [f"review:{key}"]} for key in
                     ("ship_boundary", "required_spaces", "scene_coverage", "fitting_catalog", "component_sizes")},
    }


def configured():
    result = layout()
    result["engineering"] = engineering_fixture(result["pipe"]["original_route"])
    return result


def findings(value):
    return {item["id"]: item for item in value["findings"]}


def test_missing_engineering_never_passes_fabrication_or_whole_scene():
    report = assess_layout(layout())
    checks = findings(report)
    assert checks["shock_clearance"]["status"] == "pass"
    assert checks["piece_length"]["status"] == "needs_review"
    assert checks["bends_per_piece"]["status"] == "needs_review"
    assert checks["ship_boundary"]["status"] == "needs_review"
    assert checks["scene_coverage"]["status"] == "needs_review"
    assert checks["directional_drain_slope"]["status"] == "not_applicable"
    assert report["engineering_compliance_certified"] is False
    assert report["complete"] is False
    assert sum(report["counts"].values()) == len(report["findings"])


def test_complete_fixture_has_traceable_json_safe_results_and_no_mutation():
    value = configured()
    before = deepcopy(value)
    report = assess_layout(value)
    assert value == before
    assert report["profile"] == PROFILE_ID
    assert report["counts"]["needs_review"] == 8
    assert report["counts"]["fail"] == 0
    assert report["complete"] is False
    assert report["engineering_compliance_certified"] is False
    assert all(item["source"]["page"] >= 1 and item["source"]["document"].endswith(".pdf") for item in report["findings"])
    json.dumps(report, allow_nan=False)


def test_changed_candidate_cannot_inherit_original_measurements():
    value = configured()
    route = [[0, 0, 1000], [500, -200, 1000], [1000, 0, 1000]]
    checks = findings(assess_layout(value, route))
    for rule in ("piece_length", "bend_radius", "walkway_headroom", "valve_access_height", "component_sizes", "fitting_catalog"):
        assert checks[rule]["status"] == "needs_review"
    assert checks["shock_clearance"]["status"] == "pass"


def test_measurements_require_source_refs_and_complete_inventory():
    value = configured()
    value["engineering"]["pieces"][0]["source_refs"] = []
    assert findings(assess_layout(value))["piece_length"]["status"] == "needs_review"
    value = configured()
    value["engineering"]["inventory_complete"]["pieces"] = False
    assert findings(assess_layout(value))["piece_length"]["status"] == "needs_review"
    value["engineering"]["pieces"][0]["centerline_length_mm"] = 7000
    assert findings(assess_layout(value))["piece_length"]["status"] == "fail"


def test_explicit_empty_complete_inventory_is_not_applicable_not_pass():
    value = configured()
    value["engineering"]["valves"] = []
    assert findings(assess_layout(value))["valve_access_height"]["status"] == "not_applicable"
    del value["engineering"]["inventory_complete"]["valves"]
    assert findings(assess_layout(value))["valve_access_height"]["status"] == "needs_review"


def test_fifty_mm_prototype_gap_does_not_meet_fifty_point_eight():
    value = layout()
    value["obstacles"][0]["min"][1] = 100.5
    check = findings(assess_layout(value))["shock_clearance"]
    assert check["status"] == "fail"
    assert check["required"]["minimum_clearance_mm"] == 50.8
    assert check["measured"]["collisions"] == [{"segment": 0, "obstacle_id": "wall"}]
    value["obstacles"][0]["min"][1] = 100.81
    assert findings(assess_layout(value))["shock_clearance"]["status"] == "pass"


def test_thin_obstacle_intersection_is_continuous():
    value = layout()
    value["obstacles"] = [{"id": "thin", "min": [501, -0.01, 999], "max": [501.01, 0.01, 1001]}]
    assert "shock_clearance" in assess_layout(value)["hard_failures"]


def test_pipe_envelope_does_not_fall_back_to_whole_box():
    value = layout()
    value["obstacles"] = [{"id": "capsule", "min": [-20, -1000, 0], "max": [1020, 1000, 2000],
                           "capsules": [{"start": [0, 400, 1000], "end": [1000, 400, 1000], "radius": 20}]}]
    assert findings(assess_layout(value))["shock_clearance"]["status"] == "pass"
    value["obstacles"][0]["capsules"][0]["start"][1] = 0
    assert findings(assess_layout(value))["shock_clearance"]["status"] == "fail"


def test_no_obstacles_is_missing_context_not_clearance_success():
    value = layout()
    value["obstacles"] = []
    assert findings(assess_layout(value))["shock_clearance"]["status"] == "needs_review"


def gravity(points, *, flow="start_to_end", axes=None):
    value = layout()
    value["system_type"] = "gravity"
    value["pipe"].update(start=points[0], end=points[-1], original_route=points)
    value["engineering"] = {"units": "mm", "ship_axes": axes or {"longitudinal": "+X", "transverse": "+Y", "up": "+Z", "confirmed": True}, "flow_direction": flow}
    return value


@pytest.mark.parametrize("points,expected", [
    ([[0, 0, 1000], [1000, 0, 960]], "pass"),
    ([[0, 0, 1000], [1000, 0, 990]], "fail"),
    ([[0, 0, 1000], [0, 1000, 990]], "pass"),
    ([[0, 0, 1000], [0, 1000, 995]], "fail"),
    ([[0, 0, 1000], [1000, 1000, 900]], "needs_review"),
    ([[0, 0, 1000], [0, 0, 900]], "pass"),
    ([[0, 0, 1000], [0, 0, 1100]], "fail"),
])
def test_direction_specific_fall(points, expected):
    assert findings(assess_layout(gravity(points)))["directional_drain_slope"]["status"] == expected


def test_steeper_pitch_is_not_silently_exact_brief_compliance():
    report = findings(assess_layout(gravity([[0, 0, 1000], [1000, 0, 900]])))
    assert report["directional_drain_slope"]["status"] == "pass"
    assert report["directional_drain_pitch"]["status"] == "needs_review"


def test_flow_direction_reversal_and_swapped_axes():
    value = gravity([[0, 0, 960], [1000, 0, 1000]], flow="end_to_start")
    assert findings(assess_layout(value))["directional_drain_slope"]["status"] == "pass"
    value = gravity([[0, 0, 1000], [1000, 0, 990]], axes={"longitudinal": "-Y", "transverse": "-X", "up": "+Z", "confirmed": True})
    assert directional_minimum_fall(value, *value["pipe"]["original_route"]) == 10
    assert findings(assess_layout(value))["directional_drain_slope"]["status"] == "pass"


def test_direction_settings_can_use_confirmed_model_context_axes():
    value = gravity([[0, 0, 1000], [1000, 0, 960]])
    value["model_context"] = {"ship_axes": value["engineering"].pop("ship_axes")}
    settings = directional_requirements(value)
    assert settings["ready"] is True
    assert settings["longitudinal_axis"] == 0
    value["model_context"]["ship_axes"]["confirmed"] = False
    assert directional_requirements(value)["ready"] is False
    assert directional_minimum_fall(value, *value["pipe"]["original_route"]) is None


def test_missing_flow_or_unsupported_up_axis_requires_review():
    value = gravity([[0, 0, 1000], [1000, 0, 960]])
    del value["engineering"]["flow_direction"]
    assert findings(assess_layout(value))["directional_drain_slope"]["status"] == "needs_review"
    value["engineering"]["flow_direction"] = "start_to_end"
    value["engineering"]["ship_axes"] = {"longitudinal": "+Y", "transverse": "+Z", "up": "+X", "confirmed": True}
    assert directional_requirements(value)["ready"] is False


@pytest.mark.parametrize("size,radius,straight", [(20, 152.4, 101.6), (50, 152.4, 101.6), (80, 228.6, 177.8), (100, 304.8, 228.6), (200, 304.8, 228.6)])
def test_nominal_size_table(size, radius, straight):
    assert bend_requirements(size) == {"min_radius_mm": radius, "min_straight_between_bends_mm": straight}


def test_uncategorized_or_unverified_nominal_size_is_not_guessed():
    value = configured()
    value["engineering"]["nominal_diameter_mm"] = 65
    assert findings(assess_layout(value))["bend_radius"]["status"] == "needs_review"
    value["engineering"]["nominal_diameter_mm"] = 50
    value["engineering"]["nominal_size_basis_verified"] = False
    assert findings(assess_layout(value))["bend_radius"]["status"] == "needs_review"
    del value["engineering"]["nominal_diameter_mm"]
    assert bend_requirements(None) is None
    assert findings(assess_layout(value))["straight_between_bends"]["status"] == "needs_review"


@pytest.mark.parametrize("field,value,rule", [
    ("centerline_length_mm", 6096.01, "piece_length"),
    ("weld_spacings_mm", [24.99], "weld_spacing"),
    ("straight_between_bends_mm", [101.59], "straight_between_bends"),
])
def test_physical_piece_failures(field, value, rule):
    drawing = configured()
    drawing["engineering"]["pieces"][0][field] = value
    assert rule in assess_layout(drawing)["hard_failures"]


def test_bend_count_is_physical_inventory_not_route_vertex_count():
    value = configured()
    piece = value["engineering"]["pieces"][0]
    piece["bends"] *= 2
    piece["straight_between_bends_mm"] = [101.6] * 3
    assert "bends_per_piece" in assess_layout(value)["hard_failures"]
    value["pipe"]["original_route"] = [[i * 100, 0, 1000] for i in range(11)]
    value.pop("engineering")
    assert findings(assess_layout(value))["bends_per_piece"]["status"] == "needs_review"


def test_radius_failure_and_optional_preferences():
    value = configured()
    piece = value["engineering"]["pieces"][0]
    piece["bends"][0]["radius_mm"] = 150
    piece["bends"][0]["angle_deg"] = 91
    piece["planarity_deviation_mm"] = 2
    value["engineering"]["hangers"][0]["distance_mm"] = 500
    value["engineering"]["valves"][0]["height_above_deck_mm"] = 2000
    report = assess_layout(value)
    checks = findings(report)
    assert "bend_radius" in report["hard_failures"]
    for rule in ("bend_angle_increment", "piece_planarity", "vertical_hanger_distance", "valve_access_height"):
        assert checks[rule]["status"] == "fail"
        assert checks[rule]["severity"] == "preference"
        assert rule not in report["hard_failures"]


def test_planarity_has_no_fabricated_brief_tolerance():
    value = configured()
    del value["engineering"]["pieces"][0]["planarity_tolerance_mm"]
    assert findings(assess_layout(value))["piece_planarity"]["status"] == "needs_review"


def test_horizontal_hanger_and_headroom_are_hard_limits():
    value = configured()
    value["engineering"]["hangers"][1]["distance_mm"] = 51
    value["engineering"]["walkways"][0]["headroom_mm"] = 1959
    assert {"horizontal_hanger_distance", "walkway_headroom"} <= set(assess_layout(value)["hard_failures"])


@pytest.mark.parametrize("orientation,height,status", [
    ("horizontal", 229, "pass"), ("horizontal", 1829, "pass"), ("horizontal", 228, "fail"),
    ("vertical_up", 150, "pass"), ("vertical_up", 1295, "pass"), ("vertical_up", 1296, "fail"),
    ("vertical_down", 0, "pass"), ("vertical_down", 1980, "pass"), ("vertical_down", 1981, "fail"),
])
def test_valve_stem_height_recommendations(orientation, height, status):
    value = configured()
    value["engineering"]["valves"][0].update(orientation=orientation, height_above_deck_mm=height)
    assert findings(assess_layout(value))["valve_access_height"]["status"] == status


def test_explicit_numeric_tolerances():
    value = configured()
    piece = value["engineering"]["pieces"][0]
    piece["centerline_length_mm"] = 6096 + LENGTH_TOLERANCE_MM / 2
    piece["bends"][0]["angle_deg"] = 90 + ANGLE_TOLERANCE_DEG / 2
    checks = findings(assess_layout(value))
    assert checks["piece_length"]["status"] == "pass"
    assert checks["bend_angle_increment"]["status"] == "pass"


@pytest.mark.parametrize("mutate", [
    lambda e: e.update(units="in"),
    lambda e: e.update(nominal_diameter_mm=True),
    lambda e: e.update(nominal_size_basis_verified="yes"),
    lambda e: e.update(flow_direction="guess"),
    lambda e: e.update(typo_clearance=123),
    lambda e: e["ship_axes"].update(transverse="+X"),
    lambda e: e["inventory_complete"].update(pieces="yes"),
    lambda e: e["pieces"][0].update(centerline_length_mm=float("nan")),
    lambda e: e["pieces"][0].update(weld_spacings_mm=[-1]),
    lambda e: e["pieces"][0].update(source_refs="CAD:P1"),
    lambda e: e["pieces"][0].update(straight_between_bends_mm=[]),
    lambda e: e["pieces"][0]["bends"][0].update(angle_deg=181),
    lambda e: e["hangers"][0].update(orientation="diagonal"),
    lambda e: e["valves"][0].update(height_above_deck_mm=float("inf")),
    lambda e: e["evidence"]["fitting_catalog"].update(verified="true"),
])
def test_malformed_supplied_metadata_is_rejected(mutate):
    value = configured()
    mutate(value["engineering"])
    with pytest.raises(ValueError):
        assess_layout(value)


def test_only_normalized_layouts_are_accepted():
    value = layout()
    value["units"] = "m"
    with pytest.raises(ValueError, match="normalized"):
        assess_layout(value)


def test_assumed_fixed_contact_exemption_is_explicit_and_bounded():
    value = layout()
    points = [[0, 0, 1000], [200, 0, 1000], [1000, 0, 1000]]
    value["pipe"]["original_route"] = points
    value["obstacles"] = [{"id": "connector", "min": [-10, -10, 990], "max": [10, 10, 1010]}]
    value["protected_endpoints"] = [{"endpoint": "start", "points": points[:2], "obstacle_ids": ["connector"], "max_distance": 250}]
    checks = findings(assess_layout(value))
    assert checks["shock_clearance"]["status"] == "pass"
    assert checks["shock_clearance"]["measured"]["assumed_fixed_contacts"]
    assert checks["fixed_connection_identity"]["status"] == "needs_review"
    value["protected_endpoints"][0]["points"] = points
    value["protected_endpoints"][0]["max_distance"] = 1000
    with pytest.raises(ValueError, match="movable"):
        assess_layout(value)


def test_unimplemented_requirements_remain_visible_despite_complete_declarations():
    report = assess_layout(configured())
    expected = {"penetration_and_stiffener_clearance", "deck_bulkhead_offset", "physical_installability",
                "navigation_and_trip_access", "elbow_cutback_angles", "physical_routing_objective",
                "valve_compartment_preservation", "required_component_preservation"}
    assert {item["id"] for item in report["findings"] if item["status"] == "needs_review"} == expected
    assert report["complete"] is False


def test_metadata_can_be_validated_before_geometry_search():
    value = configured()
    validate_engineering_metadata(value)
    value["engineering"]["units"] = "m"
    with pytest.raises(ValueError, match="engineering.units"):
        validate_engineering_metadata(value)
