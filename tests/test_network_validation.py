"""Synthetic, finite-volume regression tests for the independent 4.0 checker."""
from copy import deepcopy
import math
import pytest
from pipe_router.network_validation import validate_network
from pipe_router.network_fittings import fitting_envelopes, span_occupied_segments
from pipe_router.network_standards import bend_requirement, requirement


def network(points=None):
    points=points or [[500,1000,1000],[4500,1000,1000]]
    return {"schema_version":"pipeforge.network/4.0","id":"synthetic-checks","standards_profile":"fincantieri-track2-user-transcription-v1","units":"mm","provenance":"synthetic","drawing":{"id":"synthetic","revision":"r1"},"coordinate_system":{"frame":"WCS","up":[0,0,1],"fore_aft":[1,0,0],"athwartship":[0,1,0]},"bounds":{"min":[0,0,0],"max":[6000,6000,6000]},"nodes":[{"id":"a","type":"equipment_port","system_id":"s","position":points[0],"fixed":True,"nominal_diameter_mm":50,"dimensions":{"body_radius_mm":30}},{"id":"b","type":"equipment_port","system_id":"s","position":points[-1],"fixed":True,"nominal_diameter_mm":50,"dimensions":{"body_radius_mm":30}}],"spans":[{"id":"p","system_id":"s","system_type":"pressurized","from_node":"a","to_node":"b","points":points,"outer_diameter_mm":60,"nominal_diameter_mm":50,"cad_handle":"A1","instance_path":[],"fitting_catalog":{"status":"verified","source":"synthetic test dimensions; not commercial approval","radius_mm":200,"body_radius_mm":35,"angles_deg":[45,90]},"manufacturing":{"welds":[],"planarity_required":False,"process_verified":True},"constraints":{}}],"obstacles":[],"supports":[{"id":"sv","orientation":"vertical","center_to_structure_mm":381,"span_id":"p","pipe_center":points[0],"structure_point":[points[0][0],points[0][1],points[0][2]+381]},{"id":"sh","orientation":"horizontal","edge_to_structure_mm":50.8,"span_id":"p","pipe_center":points[0],"structure_point":[points[0][0],points[0][1]+80.8,points[0][2]]}],"coverage":{"complete":True,"omitted_count":0,"unknown_count":0},"accessibility_coverage":{"complete":True}}


def check(result, identifier):
    return next(item for item in result["checks"] if item["id"]==identifier)


def test_full_twenty_checks_have_explicit_status_and_evidence():
    value=network()
    result=validate_network(value,reference=value)
    assert len({item["id"] for item in result["checks"]})>=20
    assert all(item["status"] in {"PASS","FAIL","UNVERIFIED","NOT_APPLICABLE"} for item in result["checks"])
    assert result["hard_failures"]==0
    assert result["geometrically_feasible"]
    assert result["manufacturability_verified"]
    assert result["engineering_constraint_feasible"]


def test_missing_reference_and_coverage_are_unverified_not_pass():
    value=network();value.pop("coverage")
    result=validate_network(value)
    assert check(result,"preservation.fixed_ports")["status"]=="UNVERIFIED"
    assert check(result,"environment.coverage")["status"]=="UNVERIFIED"
    assert not result["geometrically_feasible"]
    assert not result["engineering_constraint_feasible"]


def test_outer_pipe_envelope_hits_duct_while_centerline_misses():
    value=network()
    value["obstacles"]=[{"id":"duct","kind":"hvac","min":[1500,1020,900],"max":[2000,1100,1100]}]
    result=validate_network(value,reference=value)
    assert check(result,"collision.pipe_to_duct")["status"]=="FAIL"
    row=check(result,"clearance.shock")["evidence"]["violations"][0]
    assert row["surface_gap_mm"]==pytest.approx(-10)


def test_fitting_arc_envelope_obstacle_not_centerline_vertex():
    value=network([[500,1000,1000],[2500,1000,1000],[2500,3000,1000]])
    # At the physical arc midpoint, far from either centerline segment.
    coordinate=2500-200+200/math.sqrt(2)
    y=1200-200/math.sqrt(2)
    value["obstacles"]=[{"id":"arc-only","kind":"structure","min":[coordinate-2,y-2,998],"max":[coordinate+2,y+2,1002]}]
    result=validate_network(value,reference=value)
    assert check(result,"collision.fitting_envelopes")["status"]=="FAIL"


def test_tangent_takeouts_consume_straight_between_bends():
    value=network([[500,1000,1000],[2500,1000,1000],[2500,1450,1000],[4500,1450,1000]])
    result=validate_network(value,reference=value)
    assert check(result,"fittings.straight_between_bends")["status"]=="FAIL"
    row=check(result,"fittings.straight_between_bends")["evidence"]["violations"][0]
    assert row["net_straight_mm"]==pytest.approx(50)
    assert row["required_net_straight_mm"]==101.6


def test_provisional_and_uncovered_catalog_never_certifies():
    value=network([[500,1000,1000],[2500,1000,1000],[2500,3000,1000]])
    span=value["spans"][0];span["nominal_diameter_mm"]=65
    span["fitting_catalog"]["status"]="provisional"
    result=validate_network(value,reference=value)
    assert check(result,"fittings.bend_radius")["status"]=="UNVERIFIED"
    assert not result["manufacturability_verified"]
    assert bend_requirement(65) is None
    assert bend_requirement(80)["radius_mm"]==228.6


def test_fixed_ports_diameter_and_source_identity_preserved():
    original=network();candidate=deepcopy(original)
    candidate["nodes"][0]["position"][0]+=100
    candidate["spans"][0]["outer_diameter_mm"]=40
    candidate["spans"][0]["cad_handle"]="OTHER"
    result=validate_network(candidate,reference=original)
    assert check(result,"preservation.fixed_ports")["status"]=="FAIL"
    assert check(result,"preservation.pipe_diameter")["status"]=="FAIL"
    assert check(result,"network.connectivity")["status"]=="FAIL"


def test_shared_port_is_only_bounded_local_exception():
    value=network()
    value["nodes"].append({"id":"c","type":"endpoint","system_id":"s","position":[4500,1040,1000],"nominal_diameter_mm":50,"dimensions":{"body_radius_mm":30}})
    other=deepcopy(value["spans"][0]);other.update(id="q",to_node="c",points=[[500,1000,1000],[4500,1040,1000]])
    value["spans"].append(other)
    result=validate_network(value,reference=value)
    assert check(result,"collision.pipe_to_pipe")["status"]=="FAIL"
    exempt=check(result,"collision.pipe_to_pipe")["evidence"]["bounded_port_exemptions"][0]
    assert exempt["radius_mm"]==110.8


def test_shared_port_nonoverlapping_join_is_allowed():
    value=network()
    value["nodes"].append({"id":"c","type":"endpoint","system_id":"s","position":[500,4000,1000],"nominal_diameter_mm":50,"dimensions":{"body_radius_mm":30}})
    other=deepcopy(value["spans"][0]);other.update(id="q",to_node="c",points=[[500,1000,1000],[500,4000,1000]])
    value["spans"].append(other)
    result=validate_network(value,reference=value)
    assert check(result,"collision.pipe_to_pipe")["status"]=="PASS"
    assert check(result,"clearance.shock")["status"]=="PASS"


def test_directional_drainage_rejects_hidden_rise_despite_endpoint_fall():
    value=network([[500,1000,2000],[1500,1000,1900],[2500,1000,1950],[4500,1000,1800]])
    span=value["spans"][0];span.update(system_type="drainage",flow_direction="from_to")
    result=validate_network(value,reference=value)
    assert check(result,"drainage.pitch")["status"]=="FAIL"
    violations=check(result,"drainage.pitch")["evidence"]["violations"]
    assert any(row["fall_mm"]<0 for row in violations)


def test_drainage_uses_explicit_rotated_ship_axes():
    value=network([[500,1000,2000],[4500,1000,1940]])
    span=value["spans"][0];span.update(system_type="drainage",flow_direction="from_to")
    assert check(validate_network(value,reference=value),"drainage.pitch")["status"]=="FAIL"
    value["coordinate_system"].update(fore_aft=[0,1,0],athwartship=[1,0,0])
    assert check(validate_network(value,reference=value),"drainage.pitch")["status"]=="PASS"
    value["coordinate_system"].pop("up")
    assert check(validate_network(value,reference=value),"drainage.pitch")["status"]=="UNVERIFIED"


def test_penetration_inconsistent_authority_is_unverified_until_approved():
    value=network()
    value["penetrations"]=[{"id":"hole","position":[2000,3000,2000],"radius_mm":40,"offset_required":False}]
    value["obstacles"]=[{"id":"stiff","kind":"stiffener","min":[2070,2900,1900],"max":[2100,3100,2100]}]
    result=validate_network(value,reference=value)
    assert check(result,"penetrations.separation")["status"]=="UNVERIFIED"
    assert requirement("penetrations.separation")["value"] is None
    value["standards_overrides"]={"penetrations.separation":{"value_mm":50,"source_reference":"Synthetic designer fixture authority","approved_by":"fixture-designer"}}
    result=validate_network(value,reference=value)
    assert check(result,"penetrations.separation")["status"]=="FAIL"
    assert check(result,"penetrations.separation")["evidence"]["violations"][0]["surface_gap_mm"]==pytest.approx(30)


def test_support_and_valve_out_of_range_preferences_are_not_hard_failures():
    value=network()
    value["supports"][0]["center_to_structure_mm"]=500
    value["supports"][0]["structure_point"][2]=1500
    valve={"id":"v","type":"valve","system_id":"s","position":[2500,1000,1000],"nominal_diameter_mm":50,"dimensions":{"body_radius_mm":50},"stem_orientation":"horizontal","stem_height_mm":2000,"fixed":False,"movement_region":{"min":[2400,900,900],"max":[2600,1100,1100]}}
    value["nodes"].append(valve)
    span=value["spans"][0];span["to_node"]="v";span["points"][-1]=valve["position"]
    other=deepcopy(span);other.update(id="q",from_node="v",to_node="b",points=[valve["position"],value["nodes"][1]["position"]]);value["spans"].append(other)
    result=validate_network(value,reference=value)
    assert check(result,"supports.vertical")["severity"]=="preference"
    assert check(result,"supports.vertical")["status"]=="FAIL"
    assert check(result,"valves.height")["status"]=="FAIL"
    assert result["hard_failures"]==0


def test_physical_elbow_sweep_endpoint_and_sagitta_bounds():
    span=network([[500,1000,1000],[2500,1000,1000],[2500,3000,1000]])["spans"][0]
    data=fitting_envelopes(span);fitting=data["fittings"][0]
    assert fitting["tangent_start"]==pytest.approx([2300,1000,1000])
    assert fitting["tangent_end"]==pytest.approx([2500,1200,1000])
    assert fitting["developed_length_mm"]==pytest.approx(100*math.pi)
    assert all(cap["radius"]>35 for cap in fitting["capsules"])
    segments=span_occupied_segments(span)
    assert segments[0]["end"]==pytest.approx(fitting["tangent_start"])


def test_candidate_cannot_remove_obstacles_constraints_or_change_source_flow():
    original=network()
    original["obstacles"]=[{"id":"wall","kind":"structure","min":[2000,900,900],"max":[2200,1100,1100]}]
    original["spans"][0]["constraints"]={"required_compartments":["required"]}
    candidate=deepcopy(original)
    candidate["obstacles"]=[]
    candidate["spans"][0]["constraints"]={}
    candidate["spans"][0]["flow_direction"]="to_from"
    result=validate_network(candidate,reference=original)
    assert check(result,"network.connectivity")["status"]=="FAIL"
    reasons=str(check(result,"network.connectivity")["evidence"])
    assert "context changed" in reasons and "constraints changed" in reasons


def test_node_body_collides_with_structure_and_boundary():
    value=network()
    value["nodes"][0].update(type="valve",dimensions={"body_radius_mm":200},stem_orientation="horizontal",stem_height_mm=1000)
    value["obstacles"]=[{"id":"valve-body-only","kind":"structure","min":[480,1150,980],"max":[520,1200,1020]}]
    result=validate_network(value,reference=value)
    assert check(result,"collision.fitting_envelopes")["status"]=="FAIL"
    assert check(result,"collision.pipe_to_structure")["status"]=="FAIL"
    assert check(result,"fittings.component_catalog")["status"]=="UNVERIFIED"
    value["bounds"]["min"][0]=400
    assert check(validate_network(value,reference=value),"routing.permitted_bounds")["status"]=="FAIL"


def test_missing_node_body_and_unsupported_fitting_do_not_pass():
    value=network()
    value["nodes"][0].update(type="tee",dimensions={})
    result=validate_network(value,reference=value)
    assert check(result,"collision.fitting_envelopes")["status"]=="UNVERIFIED"
    assert not result["geometrically_feasible"]
    span=network([[500,1000,1000],[2500,1000,1000],[3500,2000,1000]])["spans"][0]
    span["fitting_catalog"]["angles_deg"]=[90]
    assert any(item["code"]=="unsupported_catalog_angle" for item in fitting_envelopes(span)["issues"])


def test_fitting_fitting_collision_detected_symmetrically():
    value=network([[500,1000,1000],[2500,1000,1000],[2500,3000,1000]])
    other=deepcopy(value["spans"][0])
    other.update(id="q",from_node="c",to_node="d",system_id="other",points=[[2350,500,1000],[2350,1400,1000],[3500,1400,1000]])
    value["spans"].append(other)
    value["nodes"].extend([{"id":"c","type":"endpoint","system_id":"other","position":other["points"][0],"dimensions":{"body_radius_mm":30},"nominal_diameter_mm":50},{"id":"d","type":"endpoint","system_id":"other","position":other["points"][-1],"dimensions":{"body_radius_mm":30},"nominal_diameter_mm":50}])
    first=validate_network(value,reference=value)
    value["spans"].reverse()
    second=validate_network(value,reference=value)
    assert check(first,"collision.fitting_envelopes")["status"]=="FAIL"
    assert check(second,"collision.fitting_envelopes")["status"]=="FAIL"


def test_returning_own_pipe_crossing_is_a_hard_failure():
    value=network([[500,1000,1000],[3500,1000,1000],[3500,3500,1000],[1500,3500,1000],[1500,500,1000]])
    result=validate_network(value,reference=value)
    assert check(result,"collision.pipe_to_pipe")["status"]=="FAIL"
    assert any(item.get("reason")=="self-intersection" for item in check(result,"collision.pipe_to_pipe")["evidence"]["violations"])


def test_two_penetrations_need_a_full_diameter_directional_offset():
    value=network()
    value["penetrations"]=[{"id":"p1","position":[1000,1000,1000],"radius_mm":40,"offset_required":True,"paired_penetration_id":"p2","surface_normal":[0,0,1],"pipe_outer_diameter_mm":60},{"id":"p2","position":[1020,1000,2000],"radius_mm":40,"offset_required":False}]
    result=validate_network(value,reference=value)
    row=check(result,"penetrations.directional_offset")
    assert row["status"]=="FAIL"
    assert row["evidence"]["violations"][0]["directional_offset_mm"]==pytest.approx(20)


def test_wcs_frame_and_revision_changes_are_export_failures():
    value=network();candidate=deepcopy(value)
    candidate["drawing"]["revision"]="stale"
    candidate["coordinate_system"]["frame"]="UCS"
    result=validate_network(candidate,reference=value)
    assert check(result,"export.coordinate_consistency")["status"]=="FAIL"


def test_required_compartment_corner_touch_is_not_traversal():
    value=network([[500,1000,1000],[2500,1000,1000]])
    value["spans"][0]["constraints"]={"required_compartments":["room"]}
    value["compartments"]=[{"id":"room","min":[1000,1000,1000],"max":[2000,2000,2000]}]
    assert check(validate_network(value,reference=value),"network.required_compartments")["status"]=="FAIL"
    value["compartments"][0]["min"]=[1000,900,900]
    assert check(validate_network(value,reference=value),"network.required_compartments")["status"]=="PASS"


def test_indexed_clearance_matches_all_pairs_and_detects_thin_capsule():
    import random
    from pipe_router.network_validation import _ObstacleIndex, _obstacle_gap, _minimum_pair, _capsule_gap
    randomizer=random.Random(183)
    for trial in range(12):
        obstacles=[]
        for index in range(40):
            point=[randomizer.uniform(-5000,5000) for _ in range(3)]
            obstacles.append({"id":str(index),"kind":"structure","min":point,"max":[coordinate+randomizer.uniform(1,100) for coordinate in point]})
        probes=[{"start":[randomizer.uniform(-3000,3000) for _ in range(3)],"end":[randomizer.uniform(-3000,3000) for _ in range(3)],"radius":30} for _ in range(3)]
        index=_ObstacleIndex(obstacles)
        selected=index.candidates(probes,50.8)
        brute=min(_obstacle_gap(probe,obstacle) for probe in probes for obstacle in obstacles)
        indexed=min(_obstacle_gap(probe,obstacle) for probe in probes for obstacle in selected)
        assert indexed==pytest.approx(brute)
        right=[{"start":obstacle["min"],"end":obstacle["max"],"radius":20} for obstacle in obstacles]
        assert _minimum_pair(probes,right)==pytest.approx(min(_capsule_gap(a,b) for a in probes for b in right))
    value=network()
    value["obstacles"]=[{"id":"thin","kind":"hvac","min":[1999,999,900],"max":[2001,1001,1100],"capsules":[{"start":[2000,1000,900],"end":[2000,1000,1100],"radius":0.1}]}]
    assert check(validate_network(value,reference=value),"collision.pipe_to_duct")["status"]=="FAIL"


def test_rotated_ship_up_headroom_uses_explicit_floor_point():
    value=network([[500,3000,1000],[4500,3000,1000]])
    value["coordinate_system"].update(up=[0,1,0],fore_aft=[1,0,0],athwartship=[0,0,1])
    value["obstacles"]=[{"id":"walk","kind":"walkway","min":[1000,500,500],"max":[2000,600,1500],"floor_point":[1000,500,500]}]
    result=validate_network(value,reference=value)
    assert check(result,"access.accessibility")["status"]=="PASS"
    rows=check(result,"access.accessibility")["evidence"]["headroom_measurements"]
    assert rows[0]["headroom_mm"]==pytest.approx(2470)
    value["obstacles"][0]["floor_point"][1]=1200
    assert check(validate_network(value,reference=value),"access.accessibility")["status"]=="FAIL"


def test_support_scalar_without_position_is_unverified():
    value=network()
    value["supports"]=[{"id":"s","orientation":"vertical","center_to_structure_mm":381}]
    result=validate_network(value,reference=value)
    assert check(result,"supports.vertical")["status"]=="UNVERIFIED"


def test_pathological_dense_geometry_rejected_before_arc_allocation(monkeypatch):
    import pipe_router.network_validation as validation
    points=[[100+i/1000,1000+(i%2),1000] for i in range(4096)]
    value=network(points)
    called=[]
    monkeypatch.setattr(validation,"span_occupied_segments",lambda span:called.append(True))
    with pytest.raises(ValueError,match="budget"):
        validate_network(value,reference=value)
    assert called==[]


def test_pair_budget_exhaustion_never_returns_partial_pass(monkeypatch):
    import pipe_router.network_validation as validation
    value=network()
    value["obstacles"]=[{"id":"wall","kind":"structure","min":[1500,1500,500],"max":[2000,2000,1500]}]
    monkeypatch.setattr(validation,"MAX_NARROWPHASE_PAIRS",1)
    with pytest.raises(ValueError,match="pair budget"):
        validate_network(value,reference=value)
    # A failed validation resets its context and does not poison later requests.
    monkeypatch.setattr(validation,"MAX_NARROWPHASE_PAIRS",250000)
    assert validate_network(value,reference=value)["work_budget"]["narrowphase_pairs"]>1
