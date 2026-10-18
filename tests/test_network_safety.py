"""Routing gates, bounded caches and in-search cooperative cancellation."""
from collections import OrderedDict
from copy import deepcopy
import json
from pathlib import Path

import pytest

from pipe_router import connected, core
from pipe_router.network_model import normalize_network
from pipe_router.network_runtime import cancellation_scope, search_cancelled

EXAMPLES = Path(__file__).resolve().parents[1]/"examples/connected"


def load(name): return json.loads((EXAMPLES/(name+".json")).read_text())


def test_unknown_system_function_is_not_guessed_into_pressure_routing():
    source = load("b-collision-repair")
    source.pop("systems",None)
    for span in source["spans"]: span.pop("system_type",None)
    result = connected.optimize_network(source)
    assert result["proposed_network"] is None
    assert result["search"]["evaluations"] == 0
    assert any("system type" in a.get("reason","") for a in result["search"]["attempts"])


@pytest.mark.parametrize("missing",["radius_mm","body_radius_mm"])
def test_missing_physical_bend_geometry_cannot_create_accepted_repair(missing):
    source = load("b-collision-repair")
    source["spans"][0]["fitting_catalog"].pop(missing)
    result = connected.optimize_network(source,max_evaluations=1)
    assert result["proposed_network"] is None
    assert result["search"]["attempts"][0]["missing_physical_fitting_geometry"]


def test_uncovered_nominal_size_requires_engineering_clarification():
    source = load("b-collision-repair")
    source["spans"][0]["nominal_diameter_mm"] = 65
    result = connected.optimize_network(source,max_evaluations=1)
    assert result["proposed_network"] is None
    assert result["search"]["attempts"][0]["uncovered_nominal_size"]


def test_cancel_during_grid_expansion_discards_partial_candidate():
    source = load("d-blocked")
    snapshot = deepcopy(source)
    calls = 0
    def cancellation():
        nonlocal calls
        calls += 1
        return calls >= 5
    result = connected.optimize_network(source,max_evaluations=1,cancelled=cancellation)
    assert calls >= 5
    assert result["status"] == "CANCELLED"
    assert result["proposed_network"] is None
    assert result["search"]["evaluations"] == 0
    assert source == snapshot
    assert not search_cancelled()


def test_numerical_cancel_context_is_restored_on_exception():
    assert not search_cancelled()
    with pytest.raises(RuntimeError):
        with cancellation_scope(lambda: True):
            assert search_cancelled()
            raise RuntimeError("synthetic failure")
    assert not search_cancelled()


def test_core_checks_cancel_even_when_grid_has_no_route_yet():
    source = load("d-blocked")
    span = source["spans"][0]
    layout = {"units":"mm","provenance":"synthetic","system_type":"pressurized","bounds":source["bounds"],
        "pipe":{"start":span["points"][0],"end":span["points"][-1],"original_route":span["points"],"diameter":span["outer_diameter_mm"]},
        "obstacles":source["obstacles"],"rules":{"clearance":50.8,"grid_step":500,"bend_penalty":150}}
    with cancellation_scope(lambda:True):
        result=core.solve_layout(layout)
    assert result["search"]["cancelled"]
    assert result["search"]["expanded_states"] == 0
    assert result["candidate"] is None


def test_geometry_cache_obeys_entry_and_total_byte_limits(monkeypatch):
    monkeypatch.setattr(connected,"_CACHE",OrderedDict())
    monkeypatch.setattr(connected,"CACHE_BYTES_LIMIT",128)
    result=connected.optimize_network(load("a-already-good"),max_evaluations=1)
    assert result["status"] == "NO IMPROVEMENT FOUND"
    assert sum(item[2] for item in connected._CACHE.values()) <= 128
    # The numerical result is much larger than the tiny injected entry limit;
    # it must be computed without retention rather than evicting an active job.
    assert not connected._CACHE


def test_locked_source_span_is_never_changed_to_repair_another_constraint():
    source = load("b-collision-repair")
    source["spans"][0]["constraints"]["preserve_existing"] = True
    result = connected.optimize_network(source,max_evaluations=1)
    assert result["proposed_network"] is None
    assert result["original_network"]["spans"][0]["points"] == source["spans"][0]["points"]


def test_multiple_movable_components_generate_joint_bounded_alternatives():
    source=normalize_network(load("c-connected-branch"))
    valve=next(n for n in source["nodes"] if n["id"]=="valve-b")
    valve["fixed"]=False
    valve["movement_region"]={"min":[5000,1500,1400],"max":[6000,2500,1600]}
    valve["allowed_positions"]=[[5500,2000,1500],[5700,2000,1500]]
    variants=list(connected._node_variants(source))
    joint=next(net for net,moved in variants if isinstance(moved,list) and len(moved)==2)
    assert next(n for n in joint["nodes"] if n["id"]=="valve-a")["position"]==[2000,2000,1500]
    assert next(n for n in joint["nodes"] if n["id"]=="valve-b")["position"]==[5700,2000,1500]
    assert next(s for s in joint["spans"] if s["id"]=="tee-valve")["points"][-1]==[5700,2000,1500]
    assert next(s for s in joint["spans"] if s["id"]=="valve-cooler")["points"][0]==[5700,2000,1500]


@pytest.mark.parametrize("value",[True,0,65,-1,1.5])
def test_network_search_budget_rejected_before_work(value):
    with pytest.raises(ValueError,match="max_evaluations"):
        connected.optimize_network(load("a-already-good"),max_evaluations=value)
