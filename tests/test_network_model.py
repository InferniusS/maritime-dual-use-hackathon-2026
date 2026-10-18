"""Explicit topology, source-instance preservation and bounded input handling."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from pipe_router.network_model import (confirm_connections, engineering_graph,
    graph_summary, network_digest, normalize_network, reconstruct_network)

ROOT = Path(__file__).resolve().parents[1]


def network():
    return json.loads((ROOT/"examples/connected/a-already-good.json").read_text())


def cad():
    return {"id":"synthetic-dxf","units":"mm","provenance":"cad",
        "drawing":{"id":"synthetic.dwg","revision":"r1"},
        "bounds":{"min":[0,0,0],"max":[10000,10000,10000]},
        "route_candidates":[
            {"id":"a","points":[[1000,2000,3000],[3000,2000,3000]],"geometric_diameter":60,"system_id":"cooling","source_instances":[{"source_handle":"AA","instance_path":["A0"]}]},
            {"id":"b","points":[[3000.5,2000,3000],[5000,2000,3000]],"geometric_diameter":60,"system_id":"cooling","source_instances":[{"source_handle":"BB","instance_path":["B0"]}]}]}


def test_reconstruction_never_promotes_proximity_to_confirmed_connectivity():
    source = cad()
    before = deepcopy(source)
    result = reconstruct_network(source,1.)
    assert source == before
    assert len(result["nodes"]) == 4
    assert len(result["spans"]) == 2
    assert graph_summary(result)["components"] == [["a:end","a:start"],["b:end","b:start"]]
    assert len(result["unresolved_connections"]) == 1
    assert result["unresolved_connections"][0]["status"] == "unresolved"
    assert result["spans"][0]["instance_path"] == ["A0"]
    assert result["spans"][1]["instance_path"] == ["B0"]


def test_confirmed_join_has_audit_and_exact_endpoint_coordinates():
    source = reconstruct_network(cad(),1.)
    result = confirm_connections(source,[{"connection_id":"junction-1","action":"connect"}])
    assert len(source["nodes"]) == 4
    assert len(result["nodes"]) == 3
    assert len(graph_summary(result)["components"]) == 1
    assert not result["unresolved_connections"]
    joint = next(n for n in result["nodes"] if n["id"] == "confirmed:junction-1")
    assert joint["position"] == [3000.,2000.,3000.]
    assert joint["constraints"]["fitting_compatibility"] == "UNVERIFIED"
    assert result["spans"][0]["points"][-1] == result["spans"][1]["points"][0] == joint["position"]
    assert result["connection_decisions"][0]["members"] == ["a:end","b:start"]


def test_branch_partial_confirmation_leaves_remaining_member_unresolved():
    raw = cad()
    raw["route_candidates"].append({"id":"c","points":[[3000,2000,3000],[3000,5000,3000]],"geometric_diameter":60,"system_id":"cooling","source_handles":["CC"]})
    net = reconstruct_network(raw)
    assert len(net["unresolved_connections"][0]["endpoint_ids"]) == 3
    partial = confirm_connections(net,[{"id":"junction-1","action":"confirm","node_ids":["a:end","b:start"]}])
    assert partial["unresolved_connections"][0]["status"] == "unresolved"
    complete = confirm_connections(partial,[{"id":"junction-1","action":"confirm","node_id":"tee-1","type":"tee"}])
    assert graph_summary(complete)["branches"] == ["tee-1"]
    assert len(engineering_graph(complete).edges) == 3


def test_designer_can_separate_geometrically_coincident_crossing():
    net = reconstruct_network(cad())
    result = confirm_connections(net,[{"id":"junction-1","action":"separate"}])
    assert not result["unresolved_connections"]
    assert result["resolved_connections"][0]["status"] == "separate"
    assert len(graph_summary(result)["components"]) == 2


def test_connect_different_systems_and_beyond_tolerance_fail_closed():
    net = reconstruct_network(cad())
    other = deepcopy(net)
    other["nodes"][2]["system_id"] = other["nodes"][3]["system_id"] = other["spans"][1]["system_id"] = "firemain"
    with pytest.raises(ValueError,match="Different systems"):
        confirm_connections(other,[{"id":"junction-1","action":"connect"}])
    with pytest.raises(ValueError,match="tolerance"):
        confirm_connections(net,[{"id":"junction-1","action":"connect","position":[9999,9999,9999]}])


def test_source_instances_are_not_deduplicated_by_handle_alone():
    raw=cad()
    raw["route_candidates"][1]["source_instances"]=[{"source_handle":"AA","instance_path":["B0"]}]
    result=reconstruct_network(raw)
    assert len(result["spans"]) == 2
    assert result["spans"][0]["cad_handle"] == result["spans"][1]["cad_handle"] == "AA"
    assert result["spans"][0]["instance_path"] != result["spans"][1]["instance_path"]


def test_identical_repeated_candidate_id_retains_one_traceable_span():
    raw=cad()
    raw["route_candidates"].append(deepcopy(raw["route_candidates"][0]))
    result=reconstruct_network(raw)
    assert [s["id"] for s in result["spans"]] == ["a","b"]


def test_unknown_cad_geometry_stays_obstacle_and_coverage_unverified():
    raw=cad()
    raw["objects"]=[{"type":"MYSTERY","source_handle":"DD","geometry":{"kind":"mesh","vertices":[[4000,4000,1000],[5000,5000,2000]]}},
                    {"type":"CUSTOM","source_handle":"EE","geometry":{"kind":"unread"}}]
    result=reconstruct_network(raw)
    assert result["obstacles"][0]["kind"] == "unknown"
    assert result["obstacles"][0]["min"] == [4000.,4000.,1000.]
    assert not result["coverage"]["complete"]
    assert result["coverage"]["unknown_count"] == 1
    assert result["coverage"]["omitted_count"] == 1


def test_selected_source_is_excluded_but_other_placements_retained():
    raw=cad()
    raw["objects"]=[{"type":"3DSOLID","source_handle":"AA","instance_path":[path],"geometry":{"kind":"mesh","vertices":[[1000,2000,3000],[3000,2000,3000]]}} for path in ("A0","C0")]
    result=reconstruct_network(raw)
    assert len(result["obstacles"]) == 1
    assert result["obstacles"][0]["instance_path"] == ["C0"]
    assert result["coverage"]["unknown_count"] == 1


def test_meter_coordinate_normalization_keeps_explicit_mm_dimensions():
    raw=network()
    raw["units"]="m"
    for key in ("min","max"): raw["bounds"][key]=[v/1000 for v in raw["bounds"][key]]
    for node in raw["nodes"]: node["position"]=[v/1000 for v in node["position"]]
    for span in raw["spans"]: span["points"]=[[v/1000 for v in p] for p in span["points"]]
    normalized=normalize_network(raw)
    assert normalized["spans"][0]["points"] == network()["spans"][0]["points"]
    assert normalized["spans"][0]["outer_diameter_mm"] == raw["spans"][0]["outer_diameter_mm"]
    assert normalized["units"] == "mm"
    assert normalized["input_units"] == "m"
    assert normalize_network(normalized) == normalized


@pytest.mark.parametrize("mutation",["revision","constraints","coordinates","coverage","handle"])
def test_digest_covers_geometry_metadata_constraints_and_revision(mutation):
    original=network(); modified=deepcopy(original)
    if mutation == "revision": modified["drawing"]["revision"]="next"
    if mutation == "constraints": modified["spans"][0].setdefault("constraints",{})["required_compartments"]=["room"]
    if mutation == "coordinates": modified["spans"][0]["points"][0][0]+=1
    if mutation == "coverage": modified["coverage"]["complete"]=False
    if mutation == "handle": modified["spans"][0]["cad_handle"]="FFF"
    assert network_digest(original) != network_digest(modified)


@pytest.mark.parametrize("bad",[True,0,-1,float("nan"),1e30])
def test_invalid_diameter_rejected_before_geometry_search(bad):
    raw=network();raw["spans"][0]["outer_diameter_mm"]=bad
    with pytest.raises(ValueError): normalize_network(raw)


def test_missing_units_oversized_graph_and_false_boolean_rejected():
    raw=network();raw.pop("units")
    with pytest.raises(ValueError,match="units"):normalize_network(raw)
    raw=network();raw["nodes"]*=257
    with pytest.raises(ValueError,match="256"):normalize_network(raw)
    raw=network();raw["nodes"][0]["fixed"]="false"
    with pytest.raises(ValueError,match="boolean"):normalize_network(raw)


def test_movable_valve_requires_permitted_region_and_equipment_is_fixed():
    raw=network();raw["nodes"][0]["fixed"]=False
    with pytest.raises(ValueError,match="Equipment"):normalize_network(raw)
    raw["nodes"][0]["type"]="valve"
    with pytest.raises(ValueError,match="movement_region"):normalize_network(raw)
