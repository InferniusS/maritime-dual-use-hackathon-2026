"""Adversarial whole-network interface and fabrication engineering checks."""
from copy import deepcopy
import math

import pytest

from pipe_router.network_validation import validate_network
from pipe_router.network_manufacturing import manufacture_network
from pipe_router.network_standards import requirement, standards_profile


def network(points=None):
    points=points or [[500,1000,1000],[4500,1000,1000]]
    return {"schema_version":"pipeforge.network/4.0","id":"synthetic-engineering-v5",
            "provenance":"synthetic","units":"mm","drawing":{"id":"fixture","revision":"r1"},
            "standards_profile":"fincantieri-track2-user-transcription-v1",
            "coordinate_system":{"frame":"WCS","up":[0,0,1],"fore_aft":[1,0,0],"athwartship":[0,1,0]},
            "bounds":{"min":[0,0,0],"max":[10000,10000,10000]},
            "coverage":{"complete":True},"nodes":[
                {"id":"a","type":"equipment_port","system_id":"s","position":points[0],"fixed":True,
                 "nominal_diameter_mm":50,"dimensions":{"body_radius_mm":30}},
                {"id":"b","type":"equipment_port","system_id":"s","position":points[-1],"fixed":True,
                 "nominal_diameter_mm":50,"dimensions":{"body_radius_mm":30}}],
            "spans":[{"id":"p","system_id":"s","system_type":"pressurized","from_node":"a","to_node":"b",
                      "nominal_diameter_mm":50,"outer_diameter_mm":60,"points":points,"cad_handle":"A1",
                      "fitting_catalog":{"status":"verified","available":True,"source":"synthetic fixture; no commercial claim",
                                         "radius_mm":200,"body_radius_mm":35,"angles_deg":[45,90]},
                      "manufacturing":{"welds":[],"planarity_required":False,"process_verified":True},"constraints":{}}]}


def check(result, identifier):
    return next(item for item in result["checks"] if item["id"]==identifier)


def test_preserved_but_incompatible_component_size_is_rejected():
    source=network();source["nodes"][0]["nominal_diameter_mm"]=80
    result=validate_network(source,reference=source)
    assert check(result,"preservation.pipe_diameter")["status"]=="PASS"
    row=check(result,"network.connection_compatibility")
    assert row["status"]=="FAIL"
    assert row["evidence"]["violations"][0]["required_nominal_diameter_mm"]==80
    assert not result["engineering_constraint_feasible"]
    assert not result["manufacturability_verified"]


def test_explicit_component_port_size_and_outer_diameter_are_checked():
    source=network();node=source["nodes"][0]
    node["nominal_diameter_mm"]=80
    node["constraints"]={"port_nominal_diameters_mm":{"p":50},"port_outer_diameters_mm":{"p":60}}
    assert check(validate_network(source,reference=source),"network.connection_compatibility")["status"]=="PASS"
    node["constraints"]["port_outer_diameters_mm"]["p"]=61
    assert check(validate_network(source,reference=source),"network.connection_compatibility")["status"]=="FAIL"


def test_missing_size_or_port_direction_remains_unverified():
    source=network();source["nodes"][0].pop("nominal_diameter_mm")
    result=validate_network(source,reference=source)
    assert check(result,"network.connection_compatibility")["status"]=="UNVERIFIED"
    assert check(result,"connections.port_direction")["status"]=="UNVERIFIED"


def test_branch_cannot_silently_use_an_ordinary_endpoint_component():
    source=network()
    source["nodes"][0]["type"]="endpoint"
    for identifier,point in (("c",[500,4000,1000]),("d",[500,1000,4000])):
        source["nodes"].append({"id":identifier,"type":"endpoint","system_id":"s","position":point,
                                "nominal_diameter_mm":50,"dimensions":{"body_radius_mm":30}})
        span=deepcopy(source["spans"][0])
        span.update(id=identifier,to_node=identifier,points=[source["nodes"][0]["position"],point])
        source["spans"].append(span)
    row=check(validate_network(source,reference=source),"network.connectivity")
    assert row["status"]=="FAIL"
    assert "branch incidence" in str(row["evidence"])


def test_wrong_equipment_tangent_cannot_pass_when_endpoints_are_preserved():
    source=network();source["nodes"][0].update(port_direction=[0,1,0],constraints={"angular_tolerance_deg":1})
    result=validate_network(source,reference=source)
    row=check(result,"connections.port_direction")
    assert row["status"]=="FAIL"
    assert row["evidence"]["violations"][0]["angle_error_deg"]==pytest.approx(90)
    assert check(result,"preservation.fixed_ports")["status"]=="PASS"


@pytest.mark.parametrize("component_type",["tee","junction","endpoint","fitting"])
def test_movable_nonvalve_component_cannot_leave_its_permitted_region(component_type):
    source=network()
    source["nodes"][0].update(type=component_type,fixed=False,movement_region={"min":[400,900,900],"max":[600,1100,1100]})
    candidate=deepcopy(source)
    candidate["nodes"][0]["position"]=[800,1000,1000]
    candidate["spans"][0]["points"][0]=[800,1000,1000]
    assert check(validate_network(candidate,reference=source),"preservation.component_movement")["status"]=="FAIL"


def test_outward_port_direction_is_reversed_at_to_node():
    source=network()
    source["nodes"][0].update(port_direction=[1,0,0],constraints={"angular_tolerance_deg":0.1})
    source["nodes"][1].update(port_direction=[-1,0,0],constraints={"angular_tolerance_deg":0.1})
    assert check(validate_network(source,reference=source),"connections.port_direction")["status"]=="PASS"
    source["nodes"][1]["port_direction"]=[1,0,0]
    assert check(validate_network(source,reference=source),"connections.port_direction")["status"]=="FAIL"


def test_directional_component_flow_contract_is_not_just_endpoint_slope():
    source=network()
    source["spans"][0]["flow_direction"]="from_to"
    source["nodes"][0]["constraints"]={"permitted_flow_directions":{"p":"into_node"}}
    assert check(validate_network(source,reference=source),"connections.flow_direction")["status"]=="FAIL"
    source["spans"][0]["flow_direction"]="to_from"
    assert check(validate_network(source,reference=source),"connections.flow_direction")["status"]=="PASS"
    source["spans"][0].pop("flow_direction")
    assert check(validate_network(source,reference=source),"connections.flow_direction")["status"]=="UNVERIFIED"


def test_unavailable_required_elbow_is_a_hard_failure():
    source=network([[500,1000,1000],[2500,1000,1000],[2500,3000,1000]])
    source["spans"][0]["fitting_catalog"]["available"]=False
    result=validate_network(source,reference=source)
    assert check(result,"fittings.availability")["status"]=="FAIL"
    assert check(result,"fittings.bend_radius")["status"]=="FAIL"
    assert check(result,"manufacturing.fitting_geometry")["status"]=="FAIL"
    assert not result["manufacturability_verified"]


def test_cutback_permission_cannot_increase_stock_elbow_angle():
    source=network([[500,1000,1000],[2500,1000,1000],[2500-1000*math.sqrt(2),1000+1000*math.sqrt(2),1000]])
    source["spans"][0]["fitting_catalog"].update(angles_deg=[90],cutback_allowed=True)
    row=check(validate_network(source,reference=source),"fittings.bend_radius")
    assert row["status"]=="FAIL"
    assert any(item.get("code")=="unsupported_catalog_angle" for item in row["evidence"]["violations"])


def test_missing_availability_is_not_invented_from_verified_dimensions():
    source=network([[500,1000,1000],[2500,1000,1000],[2500,3000,1000]])
    source["spans"][0]["fitting_catalog"].pop("available")
    result=validate_network(source,reference=source)
    assert check(result,"fittings.availability")["status"]=="UNVERIFIED"
    assert not result["manufacturability_verified"]


def test_explicit_unavailable_equipment_connector_is_not_ignored():
    source=network()
    source["nodes"][0]["fitting_catalog"]={"status":"verified","source":"synthetic connector fixture","available":False}
    assert check(validate_network(source,reference=source),"fittings.availability")["status"]=="FAIL"
    source["nodes"][0].pop("fitting_catalog")
    source["nodes"][0]["constraints"]={"requires_fitting":True}
    assert check(validate_network(source,reference=source),"fittings.availability")["status"]=="UNVERIFIED"


def test_proposal_cannot_relax_source_connection_tolerance():
    source=network();source["tolerance_mm"]=0.1
    candidate=deepcopy(source);candidate["tolerance_mm"]=25
    row=check(validate_network(candidate,reference=source),"network.connectivity")
    assert row["status"]=="FAIL"
    assert "tolerance changed" in str(row["evidence"])


def test_proposal_cannot_remove_welds_or_change_port_specification():
    source=network();source["spans"][0]["manufacturing"]["welds"]=[{"station_mm":10},{"station_mm":15}]
    candidate=deepcopy(source)
    candidate["spans"][0]["manufacturing"]["welds"]=[]
    candidate["nodes"][0]["port_direction"]=[0,1,0]
    row=check(validate_network(candidate,reference=source),"network.connectivity")
    assert row["status"]=="FAIL"
    assert "weld evidence changed" in str(row["evidence"])


@pytest.mark.parametrize("field",["flow_rate_m3_s","roughness_mm","material_specification","catalog_revision"])
def test_all_source_engineering_metadata_is_immutable_not_only_known_fields(field):
    source=network();source["spans"][0][field]="original engineering evidence"
    candidate=deepcopy(source);candidate["spans"][0][field]="substituted evidence"
    row=check(validate_network(candidate,reference=source),"preservation.source_metadata")
    assert row["status"]=="FAIL"
    assert field in row["evidence"]["violations"][0]["changed_fields"]


def two_spans():
    source=network()
    source["nodes"].append({"id":"c","type":"endpoint","system_id":"s","position":[500,4500,1000],
                            "nominal_diameter_mm":50,"dimensions":{"body_radius_mm":30}})
    other=deepcopy(source["spans"][0])
    other.update(id="q",to_node="c",points=[source["nodes"][0]["position"],source["nodes"][-1]["position"]])
    source["spans"].append(other)
    return source


def test_independent_selection_scope_locks_otherwise_modifiable_spans():
    source=two_spans()
    baseline=validate_network(source,reference=source,permitted_span_ids={"p"})
    assert check(baseline,"preservation.optimization_scope")["status"]=="PASS"
    candidate=deepcopy(source)
    candidate["spans"][1]["points"].insert(1,[900,2500,1000])
    row=check(validate_network(candidate,reference=source,permitted_span_ids={"p"}),"preservation.optimization_scope")
    assert row["status"]=="FAIL"
    assert row["evidence"]["violations"][0]["id"]=="q"


def test_component_touching_any_unselected_span_is_locked_even_within_connection_tolerance():
    source=two_spans()
    source["nodes"][0].update(type="junction",fixed=False,movement_region={"min":[400,900,900],"max":[600,1100,1100]})
    candidate=deepcopy(source)
    candidate["nodes"][0]["position"]=[500.001,1000,1000]
    candidate["spans"][0]["points"][0]=[500.001,1000,1000]
    row=check(validate_network(candidate,reference=source,permitted_span_ids={"p"}),"preservation.optimization_scope")
    assert row["status"]=="FAIL"
    assert any(item.get("unselected_span_ids")==["q"] for item in row["evidence"]["violations"])


@pytest.mark.parametrize("permitted",[{"unknown"},"p"])
def test_independent_scope_rejects_unknown_or_ambiguous_identifiers(permitted):
    source=network()
    with pytest.raises(ValueError):
        validate_network(source,reference=source,permitted_span_ids=permitted)
    with pytest.raises(ValueError,match="reference"):
        validate_network(source,permitted_span_ids={"p"})


def test_impossible_fitting_cannot_become_only_an_unverified_piece():
    source=network([[500,1000,1000],[600,1000,1000],[600,1100,1000]])
    result=manufacture_network(source)
    row=check(result,"manufacturing.fitting_geometry")
    assert row["status"]=="FAIL"
    assert any(issue["code"]=="insufficient_tangent_length" for issue in row["evidence"]["violations"])


def test_incident_span_return_leg_is_checked_against_own_component_body():
    source=network([[1000,1000,1000],[3000,1000,1000],[3000,3000,1000],[1500,3000,1000],[1500,1500,1000]])
    source["nodes"][0].update(type="valve",dimensions={"body_radius_mm":750})
    result=validate_network(source,reference=source)
    row=check(result,"collision.fitting_envelopes")
    assert row["status"]=="FAIL"
    assert any(item.get("id")=="a" and item.get("other_id")=="p" for item in row["evidence"]["violations"])


def test_weld_schedule_uses_physical_arc_station_in_global_wcs():
    source=network([[500,1000,1000],[2500,1000,1000],[2500,3000,1000]])
    source["spans"][0]["manufacturing"]["welds"]=[{"station_mm":1800+50*math.pi}]
    result=manufacture_network(source)
    weld=result["welds"][0]
    assert weld["station_mm"]==pytest.approx(1800+50*math.pi)
    assert weld["position"]==pytest.approx([2300+200/math.sqrt(2),1200-200/math.sqrt(2),1000])
    assert weld["verification_status"]=="PASS"


def test_generated_cut_weld_schedule_preserves_global_origin():
    source=network([[500,1000,1000],[9500,1000,1000]])
    result=manufacture_network(source)
    assert len(result["welds"])==1
    assert result["welds"][0]["position"]==pytest.approx([6596,1000,1000])
    assert result["welds"][0]["status"]=="proposed"


def test_mutating_returned_standards_cannot_change_validator_authority():
    authority=standards_profile();authority["requirements"][0]["value"]=0
    record=requirement("manufacturing.bends");record["value"]=300
    assert requirement("clearance.shock")["value"]==50.8
    assert requirement("manufacturing.bends")["value"]==3
