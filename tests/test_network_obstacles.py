"""Hypothetical obstacle editing computes effects without changing source CAD."""
from copy import deepcopy
import json
import math
from pathlib import Path

import pytest

from pipe_router.connected import optimize_network
from pipe_router.network_fittings import fitting_envelopes
from pipe_router.network_model import network_digest, normalize_network
from pipe_router.network_obstacles import edit_obstacle

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def network():
    return json.loads((ROOT/"examples/connected/a-already-good.json").read_text())


def obstacle(identifier="review-block", **extra):
    return {"id":identifier, "kind":"equipment", "min":[3000,1200,1200], "max":[4000,1800,1800],
            "editable":True, "fixed":False, "protected":False, "simulation":True, **extra}


def add(network, item=None):
    item = item or obstacle()
    return edit_obstacle(network, {"action":"add", "obstacle_id":item["id"], "obstacle":item})


def check(report, identifier):
    return next(item for item in report["checks"] if item["id"] == identifier)


def test_add_blocker_reanalyzes_exact_finite_clearance_and_preserves_source(network):
    snapshot = deepcopy(network)
    response = add(network)
    edited = response["network"]
    assert network == snapshot
    assert edited["drawing"] == network["drawing"]
    assert edited["spans"] == normalize_network(network)["spans"]
    assert edited["nodes"] == normalize_network(network)["nodes"]
    assert edited["environment_revision"] == 1
    assert response["source_digest"] == network_digest(edited)
    assert response["source_digest"] != response["previous_source_digest"] == network_digest(network)
    assert response["affected_span_ids"] == ["supply"]
    assert response["original_validation"]["hard_failures"] == 0
    assert check(response["validation"], "collision.pipe_to_structure")["status"] == "FAIL"
    delta = response["collision_delta"]
    assert delta["before"]["collision_pair_count"] == 0
    assert delta["after"]["collision_pair_count"] == 1
    assert delta["after"]["minimum_surface_gap_mm"] == -30
    assert delta["new_collision_pairs"][0]["component_ids"] == ["review-block", "supply"]
    assert response["proposal_invalidated"] and not response["native_cad_modified"]


def test_move_translates_capsule_clips_and_floor_geometry_without_resizing(network):
    item = obstacle(capsules=[{"start":[3200,1500,1500],"end":[3800,1500,1500],"radius":200,
                               "clip_min":[3000,1400,1400],"clip_max":[4000,1600,1600]}],
                    floor_point=[3000,1200,1200], floor_elevation_mm=1200)
    created = add(network, item)["network"]
    snapshot = deepcopy(created)
    moved = edit_obstacle(created,{"action":"move","obstacle_id":item["id"],"translation_mm":[100,1000,300]})
    assert created == snapshot
    output = moved["network"]["obstacles"][0]
    assert output["min"] == [3100,2200,1500]
    assert output["max"] == [4100,2800,2100]
    assert output["capsules"][0]["start"] == [3300,2500,1800]
    assert output["capsules"][0]["clip_min"] == [3100,2400,1700]
    assert output["capsules"][0]["radius"] == 200
    assert output["floor_point"] == [3100,2200,1500]
    assert output["floor_elevation_mm"] == 1500
    assert moved["environment_revision"] == 2
    assert moved["collision_delta"]["after"]["collision_pair_count"] == 0
    assert moved["collision_delta"]["resolved_collision_pairs"]


@pytest.mark.parametrize("flags", [
    {"kind":"structure"}, {"kind":"wall"}, {"kind":"bulkhead"}, {"kind":"deck"},
    {"kind":"equipment","fixed":True}, {"kind":"equipment","protected":True},
    {"kind":"equipment","source_handle":"SYN-CAD-OBJECT"},
    {"kind":"equipment","type":"bulkhead"},
    {"kind":"structure","origin":"designer-review","environment_edit_created":True,"simulation":True},
])
@pytest.mark.parametrize("action", ["move","remove"])
def test_fixed_imported_protected_and_unrecorded_ship_geometry_is_immutable(network, flags, action):
    network["obstacles"] = [obstacle(**flags)]
    before = deepcopy(network)
    operation = {"action":action,"obstacle_id":"review-block","translation_mm":[0,1000,0]}
    with pytest.raises(ValueError, match="immutable|Protected|editable|protected"):
        edit_obstacle(network, operation)
    assert network == before


def test_hypothetical_wall_requires_recorded_simulation_creation(network):
    created = add(network, obstacle(kind="wall"))["network"]
    assert created["obstacles"][0]["origin"] == "designer-review"
    assert created["obstacles"][0]["environment_edit_created"] is True
    moved = edit_obstacle(created,{"action":"move","obstacle_id":"review-block","translation_mm":[0,1000,0]})
    removed = edit_obstacle(moved["network"],{"action":"remove","obstacle_id":"review-block"})
    assert removed["network"]["obstacles"] == []
    assert removed["network"]["drawing"] == network["drawing"]
    assert removed["environment_revision"] == 3


def test_add_does_not_grant_structural_edit_permissions_without_simulation(network):
    created = add(network, obstacle(kind="wall", simulation=False))["network"]
    with pytest.raises(ValueError, match="protected"):
        edit_obstacle(created,{"action":"remove","obstacle_id":"review-block"})


def test_unknown_geometry_remains_active_and_coverage_stays_unverified_after_removal(network):
    created = add(network, obstacle(kind="unknown"))
    assert created["network"]["coverage"]["complete"] is False
    assert created["network"]["coverage"]["unknown_count"] == 1
    assert check(created["validation"], "environment.coverage")["status"] == "UNVERIFIED"
    assert check(created["validation"], "collision.pipe_to_structure")["status"] == "FAIL"
    removed = edit_obstacle(created["network"],{"action":"remove","obstacle_id":"review-block"})
    assert check(removed["validation"], "environment.coverage")["status"] == "UNVERIFIED"
    assert not removed["network"]["coverage"]["complete"]


def test_removed_blocker_identifies_detoured_route_via_direct_corridor(network):
    network["spans"][0]["points"] = [[500,1500,1500],[500,3500,1500],[7500,3500,1500],[7500,1500,1500]]
    created = add(network)
    assert created["directly_affected_span_ids"] == []
    removed = edit_obstacle(created["network"],{"action":"remove","obstacle_id":"review-block"})
    assert removed["directly_affected_span_ids"] == ["supply"]
    assert any("direct endpoint corridor" in reason for reason in removed["affected_detection"]["reasons_by_span"]["supply"])


def test_physical_elbow_sweep_affects_route_when_polyline_segments_do_not(network):
    span = network["spans"][0]
    span["points"] = [[500,1500,1500],[500,3500,1500],[7500,3500,1500],[7500,1500,1500]]
    span["fitting_catalog"]["radius_mm"] = 1000
    fitting = fitting_envelopes(span)["fittings"][0]
    cap = fitting["capsules"][8]
    center = [(a+b)/2 for a,b in zip(cap["start"], cap["end"])]
    item = obstacle(min=[value-5 for value in center], max=[value+5 for value in center])
    response = add(network,item)
    assert response["directly_affected_span_ids"] == ["supply"]
    assert check(response["validation"], "collision.fitting_envelopes")["status"] == "FAIL"


def test_finite_valve_body_and_unknown_component_dimensions_are_not_ignored(network):
    network["nodes"][0]["type"] = "valve"
    response = add(network,obstacle(min=[500,1650,1480],max=[600,1700,1520]))
    assert response["directly_affected_span_ids"] == ["supply"]
    assert any("component envelope" in reason for reason in response["affected_detection"]["reasons_by_span"]["supply"])
    network["nodes"][0]["dimensions"] = {}
    unknown = add(network,obstacle(min=[6000,5000,3000],max=[6500,5500,3500]))
    assert "pump" in unknown["affected_detection"]["unknown_component_ids"]
    assert unknown["directly_affected_span_ids"] == ["supply"]
    assert unknown["validation"]["unverified"] > 0


def test_unknown_elbow_geometry_cannot_be_excluded_from_affected_routes(network):
    span = network["spans"][0]
    span["points"] = [[500,1500,1500],[500,3500,1500],[7500,3500,1500],[7500,1500,1500]]
    span["fitting_catalog"].pop("body_radius_mm")
    result = add(network, obstacle(min=[6000,5000,3000],max=[6500,5500,3500]))
    assert result["affected_detection"]["unknown_fitting_span_ids"] == ["supply"]
    assert result["directly_affected_span_ids"] == ["supply"]
    assert result["validation"]["unverified"] > 0


def test_unknown_unmapped_physical_support_retains_whole_network_review_scope(network):
    network["supports"] = [{"id":"unmapped-support","type":"support","position":[6000,5000,3000]}]
    result = add(network, obstacle(min=[6000,5000,3000],max=[6500,5500,3500]))
    assert "support:unmapped-support" in result["affected_detection"]["unknown_component_ids"]
    assert result["affected_span_ids"] == ["supply"]


def test_affected_graph_neighbors_are_included_and_all_context_is_retained(network):
    span = network["spans"][0]
    lead, tail = deepcopy(span), deepcopy(span)
    lead.update(id="lead",to_node="junction",points=[[500,1500,1500],[4000,1500,1500]])
    tail.update(id="tail",from_node="junction",points=[[4000,1500,1500],[7500,1500,1500]])
    network["spans"] = [lead,tail]
    network["nodes"].append({"id":"junction","type":"junction","system_id":"cooling","position":[4000,1500,1500],"fixed":True,"dimensions":{"body_radius_mm":50}})
    network["obstacles"] = [{"id":"retained-cad-equipment","kind":"equipment","source_handle":"SYN-KEEP","min":[6000,4000,2000],"max":[6500,4500,2500]}]
    response = add(network,obstacle(min=[1000,1300,1300],max=[1500,1700,1700]))
    assert response["directly_affected_span_ids"] == ["lead"]
    assert response["connected_neighbor_span_ids"] == ["tail"]
    assert response["affected_span_ids"] == ["lead","tail"]
    assert response["network"]["obstacles"][0] == normalize_network(network)["obstacles"][0]


def test_creation_proof_survives_bounded_recent_history(network):
    edited = add(network,obstacle(kind="wall",min=[3000,4500,3000],max=[4000,5000,3500]))["network"]
    for unused in range(66):
        edited = edit_obstacle(edited,{"action":"move","obstacle_id":"review-block","translation_mm":[1,0,0]})["network"]
    assert len(edited["environment_edits"]) == 65
    assert edited["environment_edits"][0]["action"] == "add"
    assert len(json.dumps(edited["environment_edits"])) < 128000
    removed = edit_obstacle(edited,{"action":"remove","obstacle_id":"review-block"})
    assert removed["environment_revision"] == 68


def test_active_review_object_budget_preserves_existing_work(network):
    network["obstacles"], network["environment_edits"] = [], []
    for index in range(64):
        item = obstacle(identifier=f"review-{index}",min=[5000+index,4500,3000],max=[5100+index,4600,3100],origin="designer-review",environment_edit_created=True)
        network["obstacles"].append(item)
        network["environment_edits"].append({"action":"add","obstacle_id":item["id"],"origin":"designer-review","simulation":True})
    before = deepcopy(network)
    with pytest.raises(ValueError, match="64 active"):
        add(network, obstacle(identifier="review-65"))
    assert network == before


@pytest.mark.parametrize("operation", [
    {"action":"unknown","obstacle_id":"x"},
    {"action":"add","obstacle_id":"x","obstacle":{"id":"x"}},
    {"action":"add","obstacle_id":"x","obstacle":obstacle(identifier="other")},
    {"action":"add","obstacle_id":"x","obstacle":obstacle(identifier="x",editable="true")},
    {"action":"add","obstacle_id":"x","obstacle":obstacle(identifier="x",source_handle="SYN-CAD")},
    {"action":"remove","obstacle_id":"absent"},
])
def test_invalid_operations_fail_without_mutating_source(network, operation):
    before = deepcopy(network)
    with pytest.raises(ValueError):
        edit_obstacle(network,operation)
    assert network == before


@pytest.mark.parametrize("delta", [[0,0,0],[1,2],[True,0,0],[math.inf,0,0]])
def test_invalid_translation_cannot_advance_environment_revision(network, delta):
    created = add(network)["network"]
    before = deepcopy(created)
    with pytest.raises(ValueError, match="translation_mm"):
        edit_obstacle(created,{"action":"move","obstacle_id":"review-block","translation_mm":delta})
    assert created == before


def test_operation_coordinates_are_mm_when_source_drawing_declares_metres(network):
    network["units"] = "m"
    for key in ("min","max"):
        network["bounds"][key] = [value/1000 for value in network["bounds"][key]]
    for node in network["nodes"]:
        node["position"] = [value/1000 for value in node["position"]]
    for span in network["spans"]:
        span["points"] = [[value/1000 for value in point] for point in span["points"]]
    result = add(network)
    assert result["network"]["input_units"] == "m"
    assert result["network"]["obstacles"][0]["min"] == [3000,1200,1200]
    assert result["affected_span_ids"] == ["supply"]


def test_blocking_wall_computes_and_independently_validates_a_real_detour(network):
    response = add(network,obstacle(kind="wall"))
    result = optimize_network(response["network"],selected_span_ids=response["affected_span_ids"],max_evaluations=4,max_seconds=10,strategy="feasibility")
    assert result["status"] == "IMPROVED"
    assert result["changed_span_ids"] == ["supply"]
    assert result["validation"]["hard_failures"] == 0
    assert result["after"]["length_mm"] > result["before"]["length_mm"]
    assert result["proposed_network"]["obstacles"] == response["network"]["obstacles"]


def test_sealed_wall_preserves_source_and_reports_bounded_unsuccessful_search(network):
    response = add(network,obstacle(kind="wall",min=[3000,0,0],max=[4000,6000,4000]))
    before = deepcopy(response["network"])
    result = optimize_network(response["network"],selected_span_ids=response["affected_span_ids"],max_evaluations=2,max_seconds=10)
    assert response["network"] == before
    assert result["proposed_network"] is None
    assert result["status"] == "NO FEASIBLE ROUTE FOUND"
    assert result["validation"]["hard_failures"] > 0
    assert any("not a proof" in item for item in result["search"]["limitations"])
    assert result["optimization_quality"] != "globally_optimal"
