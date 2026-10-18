"""Calculated industrial routing, bounded decomposition and progress evidence."""
from copy import deepcopy

import pytest

from pipe_router.connected import optimize_network
from pipe_router.industrial_examples import build_manifold, industrial_scenarios
from pipe_router.industrial_search import NeighborhoodSearch, boxes_overlap, component_obstacles, spatial_search_stages, violation_count
from pipe_router.network_components import component_geometry, support_contact_radius
from pipe_router import core
from pipe_router.network_model import normalize_network, network_digest, graph_summary
from pipe_router.network_validation import validate_network


def failing(rows):
    return {"hard_failures":1, "checks":[{"id":"collision.pipe_to_structure", "severity":"hard", "status":"FAIL",
            "component_ids":[row["id"] for row in rows], "evidence":{"violations":rows}}]}


def test_forty_connected_runs_are_decomposed_with_actual_topology_and_complete_coverage():
    net = build_manifold(40)
    ids = [s["id"] for s in net["spans"]]
    controller = NeighborhoodSearch(net, ids, {"hard_failures":0,"checks":[]}, {"objective":1})
    assert sorted(s for group in controller.primary for s in group) == sorted(ids)
    assert all(1 <= len(group) <= 8 for group in controller.primary)
    assert controller.graph_summary["topology_edges"] > 0
    assert controller.graph_summary["spatial_edges"] > 0
    assert len(graph_summary(net)["components"]) == 1
    assert len(graph_summary(net)["branches"]) >= 8


def test_partial_repair_uses_measured_violation_rows_and_never_pretends_checks_pass():
    net = build_manifold(20)
    ids = [s["id"] for s in net["spans"]]
    old = failing([{"id":identifier} for identifier in ids[:3]])
    controller = NeighborhoodSearch(net, ids, old, {"objective":1})
    fewer = failing([{"id":identifier} for identifier in ids[:2]])
    assert violation_count(old) == 3 and violation_count(fewer) == 2
    assert controller.observe(deepcopy(net), fewer, {"objective":2}, True)
    assert controller.validation["hard_failures"] == 1
    assert not controller.observe(deepcopy(net), old, {"objective":.1}, True)
    assert not controller.observe(deepcopy(net), {"hard_failures":0,"checks":[]}, {"objective":.1}, False)


def test_multiresolution_local_grids_are_bounded_before_search_and_keep_every_intersecting_obstacle():
    layout = {"bounds":{"min":[0,0,0],"max":[1e8,1e8,1e8]},
              "pipe":{"original_route":[[500,1500,1500],[7500,1500,1500]], "start":[500,1500,1500],"end":[7500,1500,1500]},
              "rules":{"grid_step":.0001,"bend_radius":200},
              "obstacles":[{"id":"near","min":[3000,1000,1000],"max":[4000,2000,2000]},
                           {"id":"far","min":[1e7,1e7,1e7],"max":[1e7+100,1e7+100,1e7+100]}]}
    original = deepcopy(layout)
    stages = spatial_search_stages(layout)
    assert 1 <= len(stages) <= 3
    for local, evidence in stages:
        assert evidence["estimated_grid_nodes_upper_bound"] <= evidence["grid_node_budget"] <= 50000
        assert local["rules"]["grid_step"] >= layout["rules"]["grid_step"]
        assert {o["id"] for o in local["obstacles"]} == {o["id"] for o in layout["obstacles"] if boxes_overlap(o, local["bounds"])}
        assert "near" in {o["id"] for o in local["obstacles"]}
        assert "far" not in {o["id"] for o in local["obstacles"]}
    assert layout == original


@pytest.mark.parametrize("count", [5,10,20,40])
def test_real_connected_industrial_optimization_preserves_full_context_and_reports_actual_progress(count):
    net = build_manifold(count)
    snapshot = deepcopy(net)
    events = []
    result = optimize_network(net, max_evaluations=32, max_seconds=30, progress=events.append)
    assert net == snapshot
    assert result["source_digest"] == network_digest(net)
    assert result["status"] == "IMPROVED"
    assert len(result["selection"]["modifiable_span_ids"]) == count
    assert result["search"]["selected_span_count"] == count
    assert len(result["changed_span_ids"]) >= (count-1)//4
    assert result["after"]["length_mm"] < result["before"]["length_mm"]
    assert result["validation"]["hard_failures"] == 0
    independent = validate_network(result["proposed_network"], reference=result["original_network"], permitted_span_ids=set(result["selection"]["modifiable_span_ids"]))
    assert independent["hard_failures"] == 0
    assert result["topology"]["components"] == graph_summary(normalize_network(net))["components"]
    assert result["search"]["completed_neighborhoods"] == result["search"]["total_neighborhoods"]
    assert result["search"]["local_solves"] > 0
    assert result["search"]["expanded_states"] >= 0
    assert result["search"]["grid_nodes_peak"] <= 50000
    assert events[0]["phase"] == "initial_validation" and events[-1]["phase"] == "complete"
    assert all(a["evaluations"] <= b["evaluations"] for a,b in zip(events,events[1:]))
    assert any(e["active_span_ids"] for e in events)
    assert events[-1]["selected_span_count"] == count
    assert events[-1]["evaluations"] == result["search"]["evaluations"]
    assert all(len(a.get("neighborhood_span_ids",[])) <= 16 for a in result["search"]["attempts"])


def test_industrial_budget_stop_does_not_publish_an_unfinished_invalid_repair():
    net = industrial_scenarios()["g-blocked-assembly"]
    result = optimize_network(net, max_evaluations=1, max_seconds=30)
    assert result["proposed_network"] is None
    assert result["status"] == "SEARCH LIMIT REACHED"
    assert result["search"]["termination_reason"] == "EVALUATION_LIMIT"
    assert result["after"] == result["before"]
    assert result["validation"]["hard_failures"] > 0


def test_industrial_cancel_discards_private_partial_incumbents():
    net = build_manifold(40)
    calls = 0
    def cancelled():
        nonlocal calls
        calls += 1
        return calls >= 8
    result = optimize_network(net, max_evaluations=32, cancelled=cancelled)
    assert result["status"] == "CANCELLED"
    assert result["proposed_network"] is None
    assert result["search"]["cancelled"] is True
    assert result["search"]["termination_reason"] == "CANCELLED"
    assert result["after"] == result["before"]


def test_progress_callback_failure_cannot_change_engineering_acceptance():
    def broken(record):
        raise RuntimeError("Synthetic UI disconnection")
    source = industrial_scenarios()["a-industrial-5"]
    result = optimize_network(source, max_evaluations=1, progress=broken)
    assert result["search"]["progress_delivery_errors"] > 0
    if result["proposed_network"]:
        assert result["validation"]["hard_failures"] == 0


def test_support_planning_contact_keeps_remaining_material_and_other_pipes_blocked():
    net = build_manifold(5)
    support = next(c for c in component_geometry(net) if c["type"] == "support")
    own = support["incident_span_ids"][0]
    span = next(s for s in net["spans"] if s["id"] == own)
    contact = tuple(support["contact_position"])
    material = tuple(support["position"])
    own_parts = component_obstacles(support, span)
    foreign_parts = component_obstacles(support, {**span, "id":"unrelated-run"})
    assert own_parts and foreign_parts
    applied_radius = support_contact_radius(support, span, 50.8)
    assert applied_radius == pytest.approx(105.8)
    assert applied_radius < support["contact_radius_mm"]
    assert all(o["applied_contact_radius_mm"] == applied_radius for o in own_parts)
    # Intentional contact is available only to the explicitly attached run.
    assert not any(core._segment_intersects_box(contact, contact, core._prepare_obstacle(o, "support", 85.8)) for o in own_parts)
    assert any(core._segment_intersects_box(contact, contact, core._prepare_obstacle(o, "support", 0)) for o in foreign_parts)
    # A returning route through the actual hanger away from its contact remains
    # blocked. The complete component has never been exempted for its run.
    assert any(core._segment_intersects_box(material, material, core._prepare_obstacle(o, "support", 0)) for o in own_parts)
    # A return outside the bounded contact but inside the old raw 200 mm request
    # is still material, even though the source requested a larger exception.
    returning = (contact[0], contact[1], contact[2]+150)
    assert any(core._segment_intersects_box(returning, returning, core._prepare_obstacle(o, "support", 0)) for o in own_parts)


def test_default_budget_repairs_congested_assembly_without_detaching_valid_adjacent_supports():
    net = industrial_scenarios()["e-congested-assembly"]
    result = optimize_network(net, max_evaluations=24, max_seconds=30)
    expected = {s["id"] for s in net["spans"] if s["id"].endswith("-feed")}
    assert result["status"] == "IMPROVED"
    assert expected <= set(result["changed_span_ids"])
    assert not any(identifier.endswith("-reducer-inlet") for identifier in result["changed_span_ids"])
    assert result["validation"]["hard_failures"] == 0
    assert next(c for c in result["validation"]["checks"] if c["id"] == "components.support_attachment")["status"] == "PASS"
    assert result["proposed_network"]["supports"] == result["original_network"]["supports"]
