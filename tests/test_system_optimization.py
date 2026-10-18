"""Coordinated proposals never hide source geometry or accept mutual clashes."""
from copy import deepcopy
import json

import pytest

from pipe_router import system_optimization as system
from pipe_router.assembly import plan_assembly
from pipe_router.workflow import run_workflow
from test_cad_layout import solid


def drawing(routes, diameter=20, obstacles=()):
    candidates, objects, envelopes = [], [], []
    for index, points in enumerate(routes, 1):
        handle = f"H{index}"
        candidates.append({"id": f"run-{index}", "label": f"Section {index}", "points": points,
            "geometric_diameter": diameter, "source_handles": [handle], "instance_path": []})
        objects.append(solid(handle, [min(p[i] for p in points)-diameter/2 for i in range(3)],
                            [max(p[i] for p in points)+diameter/2 for i in range(3)]))
        envelopes.append({"source_handle": handle, "instance_path": [], "eligible": True,
            "reason": "Complete analytic test envelope", "capsules": [
                {"start": a, "end": b, "radius": diameter/2} for a,b in zip(points,points[1:])]})
    return {"name": "Independent routing fixture", "source_sha256": "fixed-source", "source": "test.dxf",
        "provenance": "cad", "units": "mm", "route_candidates": candidates,
        "objects": [*objects,*obstacles], "solid_envelopes": envelopes,
        "counts": {"solid_instances": len(objects)+len(obstacles)}}


def selections(count, **settings):
    return [{"candidate_id": f"run-{i+1}", "clearance": 50.8, "padding": 1500,
             "grid_step": 250, "bend_radius_mm": 304.8, "rule_profile": "fmg-v3", **settings} for i in range(count)]


def crossing_drawing():
    # Source paths are separated in Z. Individually valid direct shortcuts
    # intersect at the origin. Neither source path occupies that origin.
    return drawing([
        [[-2000,0,0],[-2000,0,1000],[2000,0,1000],[2000,0,0]],
        [[0,-2000,0],[0,-2000,-1000],[0,2000,-1000],[0,2000,0]],
    ])


def test_two_proposals_coordinate_using_full_width_and_preserve_source():
    cad, requests = crossing_drawing(), selections(2)
    original, original_requests = deepcopy(cad), deepcopy(requests)
    progress = []
    result = system.optimize_sections(cad, requests, progress=progress.append)
    assert cad == original and requests == original_requests
    assert result["counts"]["proposed"] >= 1
    accepted = [r for r in result["routes"] if r["coordination"]["accepted"]]
    for route in accepted:
        assert route["coordination"]["original_geometry_revalidated"]
        assert route["coordination"]["pair_clearance_revalidated"]
        assert route["result"]["assembly"]["outer_diameter_mm"] == 20
        source = cad["route_candidates"][route["index"]-1]["points"]
        assert route["result"]["assembly"]["centerline_points"][0] == source[0]
        assert route["result"]["assembly"]["centerline_points"][-1] == source[-1]
    if len(accepted) == 2:
        assert system._pair_conflict(*accepted) is None
    second = result["routes"][1]
    assert any(o["id"].startswith("system-proposal-") for o in second["layout"]["obstacles"])
    assert any(o["id"].startswith("cad-solid-") for o in second["layout"]["obstacles"])
    assert not result["complete_system_verified"]
    assert progress[-1]["completed"] == 2
    json.dumps(result, allow_nan=False)


def test_individually_valid_crossing_worker_results_cannot_both_be_accepted(monkeypatch):
    # Simulate a search worker that accidentally ignores reserved proposal
    # obstacles. The coordinator's independent pair check must catch it.
    def ignore_reserved(layout):
        unchecked = deepcopy(layout)
        unchecked["obstacles"] = [o for o in unchecked["obstacles"] if not o["id"].startswith("system-proposal-")]
        return run_workflow(unchecked)
    monkeypatch.setattr(system, "run_workflow", ignore_reserved)
    result = system.optimize_sections(crossing_drawing(), selections(2))
    assert result["routes"][0]["status"] == "proposed"
    later = result["routes"][1]
    assert later["status"] == "blocked"
    assert later["result"]["candidate"] is None
    assert later["result"]["assembly"]["meshes"] == []
    assert later["result"]["assembly"]["components"] == []
    assert later["coordination"]["conflict"]["clearance_mm"] == 50.8
    review = later["result"]["search_region_review"]
    assert review["boundary_review"] == "coordination_rejected"
    assert review["coordination_failure_code"] == "proposal_pair_conflict"
    assert review["message"] == later["message"]
    assert "not saved" in review["next_action"]


def test_pair_uses_larger_clearance_and_arc_error():
    def record(index, y, clearance, error=0):
        points = [[0,y,0],[1000,y,0]]
        return {"index": index, "layout": {"rules": {"clearance": clearance}}, "result": {"assembly": {
            "outer_diameter_mm": 20, "components": [{"outer_diameter_mm": 20,
            "centerline_points": points, "chord_error_mm": error}]}}}
    first, second = record(1,0,100), record(2,120.2,10,.3)
    conflict = system._pair_conflict(first,second)
    assert conflict["required_separation_mm"] == pytest.approx(120.3)
    obstacle = system._proposal_obstacles(first,10)[0]
    assert obstacle["capsules"][0]["radius"] == 100
    assert obstacle["min"] == [-100,-100,-100]
    assert obstacle["max"] == [1100,100,100]


def test_all_original_obstacles_rechecked_even_if_worker_drops_them(monkeypatch):
    cad = drawing([[[0,0,0],[4000,0,0]]], obstacles=[solid("wall",[1900,-100,-100],[2100,100,100])])
    def ignore_everything(layout):
        unchecked = deepcopy(layout); unchecked["obstacles"] = []
        return run_workflow(unchecked)
    monkeypatch.setattr(system, "run_workflow", ignore_everything)
    row = system.optimize_sections(cad,selections(1))["routes"][0]
    assert row["status"] == "blocked"
    assert row["result"]["assembly"]["meshes"] == []
    assert "original surroundings" in row["message"]
    review = row["result"]["search_region_review"]
    assert review["boundary_review"] == "coordination_rejected"
    assert review["coordination_failure_code"] == "original_geometry_recheck"
    assert review["message"] == row["message"]


def test_unchanged_is_retained_and_missing_section_is_visible():
    cad = drawing([[[0,0,0],[4000,0,0]]])
    requests = selections(1) + [{"candidate_id": "missing"}]
    result = system.optimize_sections(cad,requests)
    assert result["counts"]["unchanged"] == 1
    assert result["routes"][1]["status"] == "needs_review"
    assert "missing" in result["routes"][1]["message"]
    assert result["metrics"]["original_length_mm"] == 4000
    assert result["metrics"]["suggested_length_mm"] == 4000


def test_failed_connection_has_no_automatic_waiver_and_does_not_stop_next():
    cad = drawing([[[0,0,0],[4000,0,0]],[[0,3000,0],[4000,3000,0]]],
                  obstacles=[solid("port",[-50,-50,-50],[100,50,50])])
    result = system.optimize_sections(cad,selections(2))
    first = result["routes"][0]
    assert first["status"] == "blocked"
    assert not first["layout"].get("protected_endpoints")
    assert result["routes"][1]["status"] == "unchanged"
    assert first["result"]["assembly"]["meshes"] == []
    assert first["result"]["search_region_review"]["boundary_review"] in ("fixed_connection_conflict", "fixed_endpoint_contact")


def test_explicit_original_contact_preserved_and_proposal_waiver_forbidden():
    cad = drawing([[[0,0,0],[4000,0,0]]], obstacles=[solid("port",[-50,-50,-50],[100,50,50])])
    request = selections(1, endpoint_connections={"start": ["cad-solid-2"]}, max_stub_length=250)
    result = system.optimize_sections(cad,request)
    row = result["routes"][0]
    assert row["status"] == "unchanged", row["message"]
    protected = row["layout"]["protected_endpoints"][0]
    assert protected["obstacle_ids"] == ["cad-solid-2"]
    assert row["result"]["candidate"]["points"][:len(protected["points"])] == protected["points"]
    invalid = system.optimize_sections(cad,selections(1,endpoint_connections={"start":["system-proposal-1-1"]}))
    assert invalid["routes"][0]["status"] == "needs_review"
    assert "cannot be declared" in invalid["routes"][0]["message"]


@pytest.mark.parametrize("change", ["duplicate", "shared_source"])
def test_shared_or_duplicate_source_is_rejected_before_search(monkeypatch,change):
    cad = drawing([[[0,0,0],[4000,0,0]],[[0,3000,0],[4000,3000,0]]])
    requests = selections(2)
    if change == "duplicate": requests[1]["candidate_id"] = "run-1"
    else: cad["route_candidates"][1]["source_handles"] = ["H1"]
    monkeypatch.setattr(system,"run_workflow",lambda _: pytest.fail("Search must not start"))
    with pytest.raises(ValueError,match="Duplicate|overlap"):
        system.optimize_sections(cad,requests)


def test_repeated_handle_in_different_instances_is_not_source_overlap():
    cad = drawing([[[0,0,0],[4000,0,0]],[[0,3000,0],[4000,3000,0]]])
    for item in [cad["route_candidates"][1],cad["objects"][1],cad["solid_envelopes"][1]]:
        item["instance_path"] = ["INSERT-2"]
    cad["route_candidates"][1]["source_handles"] = ["H1"]
    cad["objects"][1]["source_handle"] = cad["solid_envelopes"][1]["source_handle"] = "H1"
    result = system.optimize_sections(cad,selections(2))
    assert result["counts"]["unchanged"] == 2


def test_deadline_reports_all_unprocessed_sections(monkeypatch):
    monkeypatch.setattr(system,"MAX_SECONDS",-1)
    result = system.optimize_sections(crossing_drawing(),selections(2))
    assert result["counts"]["not_processed"] == 2
    assert result["coverage"]["processed_sections"] == 0
    assert result["metrics"]["original_length_mm"] is None


def test_gravity_has_no_fabrication_acceptance_but_direction_is_preserved():
    cad = drawing([[[0,0,20],[4000,0,0]]])
    row = system.optimize_sections(cad,selections(1,system_type="gravity",reverse_flow=True,min_slope=.02))["routes"][0]
    assert row["status"] == "needs_review"
    assert row["layout"]["pipe"]["start"] == [4000,0,0]
    assert row["layout"]["rules"]["min_slope"] == .02
    assert "result" not in row


def test_invalid_per_section_options_are_visible_and_other_sections_continue():
    cad = drawing([[[0,0,0],[4000,0,0]],[[0,3000,0],[4000,3000,0]]])
    requests = selections(2); requests[0]["grid_step"] = -1
    result = system.optimize_sections(cad,requests)
    assert result["routes"][0]["status"] == "needs_review"
    assert result["routes"][1]["status"] == "unchanged"


@pytest.mark.parametrize("bad", [[],[{}],[{"candidate_id":"x"}]*13])
def test_global_invalid_selection_rejected(bad):
    with pytest.raises(ValueError): system.optimize_sections(crossing_drawing(),bad)


@pytest.mark.parametrize("kind", ["diameter","endpoint"])
def test_bad_worker_cannot_change_fixed_constraints(monkeypatch,kind):
    original_workflow = system.run_workflow
    def bad(layout):
        result = original_workflow(layout)
        if kind == "diameter": result["assembly"]["outer_diameter_mm"] += 10
        else: result["candidate"]["points"][0][0] += 1
        return result
    monkeypatch.setattr(system,"run_workflow",bad)
    row = system.optimize_sections(drawing([[[0,0,0],[4000,0,0]]]),selections(1))["routes"][0]
    assert row["status"] == "blocked" and row["result"]["assembly"]["meshes"] == []


def test_pair_work_limit_blocks_later_proposal_instead_of_skipping_verification(monkeypatch):
    monkeypatch.setattr(system,"MAX_PAIR_SEGMENT_CHECKS",0)
    cad = drawing([[[0,0,0],[4000,0,0]],[[0,3000,0],[4000,3000,0]]])
    result = system.optimize_sections(cad,selections(2))
    assert result['routes'][0]['status'] == 'unchanged'
    assert result['routes'][1]['status'] == 'blocked'
    assert result['routes'][1]['result']['assembly']['meshes'] == []
    assert 'work limit' in result['routes'][1]['message']


def test_all_capsule_parts_survive_chunking_and_cover_last_segment():
    points = [[i*10,0,0] for i in range(260)]
    row = {'index':1,'layout':{'rules':{'clearance':50}},'result':{'assembly':{
        'outer_diameter_mm':20,'components':[{'outer_diameter_mm':20,'centerline_points':points,'chord_error_mm':0}]}}}
    obstacles = system._proposal_obstacles(row,50)
    assert len(obstacles) == 2
    assert sum(len(o['capsules']) for o in obstacles) == 259
    assert obstacles[-1]['max'] == [2600,10,10]
    assert obstacles[0]['max'][0] < obstacles[-1]['max'][0]


def test_unrenderable_original_cannot_prevent_other_section_display(monkeypatch):
    original_workflow = system.run_workflow
    def missing_reference(layout):
        result = original_workflow(layout)
        if layout['cad_context']['candidate_id'] == 'run-1':
            result['assembly']['reference_meshes'] = []
        return result
    monkeypatch.setattr(system,'run_workflow',missing_reference)
    cad = drawing([[[0,0,0],[4000,0,0]],[[0,3000,0],[4000,3000,0]]])
    result = system.optimize_sections(cad,selections(2))
    assert result['routes'][0]['status'] == 'blocked'
    assert 'comparison meshes' in result['routes'][0]['message']
    assert result['routes'][1]['status'] == 'unchanged'
