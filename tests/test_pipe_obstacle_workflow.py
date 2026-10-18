"""Pipe envelopes stay consistent across preparation, units, connections and search."""
from copy import deepcopy

import pytest

from pipe_router.cad_layout import prepare_cad_layout
from pipe_router.workflow import normalize_layout, run_workflow, validate_layout_route, with_endpoint_connections


def obstacle(identifier, start, end, radius):
    return {"id": identifier,
            "min": [min(a, b) - radius for a, b in zip(start, end)],
            "max": [max(a, b) + radius for a, b in zip(start, end)],
            "capsules": [{"start": start, "end": end, "radius": radius}]}


def layout():
    return {"name": "Invented diagonal pipe test", "provenance": "synthetic", "units": "mm",
            "system_type": "pressurized", "bounds": {"min": [-100]*3, "max": [200]*3},
            "pipe": {"start": [10, 90, 0], "end": [90, 90, 0], "diameter": 4,
                     "original_route": [[10, 90, 0], [90, 90, 0]]},
            "obstacles": [obstacle("diagonal", [0, 0, 0], [100, 100, 0], 5)],
            "rules": {"clearance": 3, "grid_step": 10, "bend_penalty": 5}}


def test_empty_box_corner_does_not_block_search_but_real_crossing_does():
    case = layout()
    # Move the end outside the pipe too. The original segment crosses it.
    case["pipe"]["end"] = [120, 90, 0]
    case["pipe"]["original_route"][-1] = [120, 90, 0]
    before = deepcopy(case)
    result = run_workflow(case)
    assert case == before
    assert not result["original"]["valid"]
    assert result["search"]["expanded_states"] > 0
    assert result["status"] == "improved"
    assert validate_layout_route(case, result["candidate"]["points"])["valid"]
    boxes = deepcopy(case)
    boxes["obstacles"][0].pop("capsules")
    blocked = run_workflow(boxes)
    assert blocked["search"]["expanded_states"] == 0
    assert blocked["diagnostics"][0]["stage"] == "before_search"


def test_capsule_units_scale_once_and_result_matches():
    case = layout()
    case["pipe"]["end"] = [120, 90, 0]
    case["pipe"]["original_route"][-1] = [120, 90, 0]
    mm = run_workflow(case)
    metres = deepcopy(case)
    metres["units"] = "m"
    for shape in [metres["bounds"], *metres["obstacles"]]:
        for key in ("min", "max"):
            shape[key] = [v / 1000 for v in shape[key]]
        for cap in shape.get("capsules", []):
            for key in ("start", "end"):
                cap[key] = [v / 1000 for v in cap[key]]
            cap["radius"] /= 1000
    for key in ("start", "end"):
        metres["pipe"][key] = [v / 1000 for v in metres["pipe"][key]]
    metres["pipe"]["original_route"] = [[v / 1000 for v in p] for p in metres["pipe"]["original_route"]]
    metres["pipe"]["diameter"] /= 1000
    metres["rules"] = {k: v / 1000 for k, v in metres["rules"].items()}
    result = run_workflow(metres)
    assert result["candidate"]["points"] == mm["candidate"]["points"]
    assert result["normalized_layout"]["obstacles"] == mm["normalized_layout"]["obstacles"]
    assert normalize_layout(result["normalized_layout"])["obstacles"] == mm["normalized_layout"]["obstacles"]


def test_contact_exemption_uses_actual_shape_and_preserves_only_stub():
    case = layout()
    case["pipe"].update(start=[0, 0, 0], end=[90, 0, 0], original_route=[[0, 0, 0], [90, 0, 0]])
    case["obstacles"] = [obstacle("connector", [0, 0, -10], [0, 0, 10], 5)]
    fixed = with_endpoint_connections(case, {"start": ["connector"]}, 25)
    stub = fixed["protected_endpoints"][0]["points"]
    assert stub[-1] == pytest.approx([10.001, 0, 0])
    assert run_workflow(fixed)["original"]["valid"]
    bad = stub + [[0, 0, 20], [0, 0, 0], [90, 0, 0]]
    assert not validate_layout_route(fixed, bad)["valid"]
    outside = layout()
    with pytest.raises(ValueError, match="touch"):
        with_endpoint_connections(outside, {"start": ["diagonal"]}, 100)


def test_blocked_endpoint_names_source_and_location():
    case = layout()
    case["cad_context"] = {"obstacle_sources": [{"id": "diagonal", "source_handle": "AA", "instance_path": ["BB[0]"]}]}
    case["pipe"]["start"] = [50, 50, 0]
    case["pipe"]["original_route"][0] = [50, 50, 0]
    result = run_workflow(case)
    assert result["status"] == "no_route"
    finding = result["diagnostics"][0]
    assert finding["source_handle"] == "AA"
    assert finding["instance_path"] == ["BB[0]"]
    assert finding["point"] == [50, 50, 0]
    assert finding["obstacle_method"] == "supported_surface_capsules"
    assert "Search did not start" in finding["message"]


def test_cad_envelopes_expand_before_filter_and_partial_geometry_stays_box():
    from test_cad_layout import solid
    cad = {"name": "Coverage test", "provenance": "cad", "units": "mm",
           "objects": [solid("A", [0, -2, -2], [100, 2, 2]),
                       solid("B", [1000, 1000, 1000], [1010, 1010, 1010]),
                       solid("C", [20, 20, 0], [30, 30, 10])],
           "route_candidates": [{"id": "A:run", "points": [[0, 0, 0], [100, 0, 0]],
                                 "geometric_diameter": 4, "source_handles": ["A"]}],
           "solid_envelopes": [
               {"source_handle": "B", "instance_path": [], "eligible": True,
                "capsules": [{"start": [50, 10, 0], "end": [50, 20, 0], "radius": 5}]},
               {"source_handle": "C", "instance_path": [], "eligible": False,
                "capsules": [], "reason": "Unsupported face"}]}
    result = prepare_cad_layout(cad, "A:run", clearance=0, padding=40)
    by_source = {entry["source_handle"]: entry for entry in result["cad_context"]["obstacle_sources"]}
    assert set(by_source) == {"B", "C"}
    assert by_source["B"]["obstacle_method"] == "supported_surface_capsules"
    assert by_source["C"]["obstacle_method"] == "sample_box"
    assert result["cad_context"]["counts"]["pipe_shaped_obstacles"] == 1
    assert result["cad_context"]["counts"]["fallback_box_obstacles"] == 1
    cad["objects"] = [obj for obj in cad["objects"] if obj["source_handle"] != "B"]
    no_preview = prepare_cad_layout(cad, "A:run", clearance=0, padding=40)
    source = next(item for item in no_preview["cad_context"]["obstacle_sources"] if item["source_handle"] == "B")
    assert source["source_object_count"] == 0
    assert source["obstacle_method"] == "supported_surface_capsules"
    assert len(no_preview["obstacles"]) == 2


def test_gravity_checks_same_pipe_envelope_continuously():
    case = layout()
    case["system_type"] = "gravity"
    case["rules"]["min_slope"] = 0.01
    case["pipe"].update(start=[0, 50, 50], end=[100, 50, -50], original_route=[[0, 50, 50], [100, 50, -50]])
    assert not validate_layout_route(case, case["pipe"]["original_route"])["valid"]
    case["obstacles"][0] = obstacle("far-pipe", [0, 150, 0], [100, 150, 0], 5)
    assert validate_layout_route(case, case["pipe"]["original_route"])["valid"]


def test_flat_connector_end_does_not_require_a_long_round_stub():
    case = layout()
    case["pipe"].update(start=[0, 0, 0], end=[0, 0, 100], original_route=[[0, 0, 0], [0, 0, 100]])
    cap = {"start": [0, 0, -10], "end": [0, 0, 0], "radius": 20,
           "clip_min": [-20, -20, -10], "clip_max": [20, 20, 0]}
    case["obstacles"] = [{"id": "flat-connector", "min": [-20, -20, -10], "max": [20, 20, 0], "capsules": [cap]}]
    fixed = with_endpoint_connections(case, {"start": ["flat-connector"]}, 10)
    assert fixed["protected_endpoints"][0]["points"][-1] == pytest.approx([0, 0, 5.001])
    assert run_workflow(fixed)["original"]["valid"]
    inches = deepcopy(case)
    inches["units"] = "in"
    converted = normalize_layout(inches)
    assert converted["obstacles"][0]["capsules"][0]["clip_min"] == pytest.approx([-508, -508, -254])
    assert converted["obstacles"][0]["capsules"][0]["clip_max"] == pytest.approx([508, 508, 0])
