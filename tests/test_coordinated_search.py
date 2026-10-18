"""Independent whole-network gates for prioritized shared-corridor routing."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import math

from backend.geometry import segment_distance

from pipe_router import connected
from pipe_router.network_model import network_digest
from pipe_router.network_validation import validate_network


def scenario():
    return json.loads((Path(__file__).resolve().parents[1]/"examples/multi-system/09-coordinated-improvement.json").read_text())


def test_shared_corridor_requires_releasing_both_permitted_systems_and_independent_validation():
    net = scenario()
    snapshot = deepcopy(net)
    assert validate_network(net)["hard_failures"] == 0
    result = connected.optimize_network(net, max_evaluations=8)
    assert net == snapshot
    assert result["status"] == "IMPROVED"
    assert set(result["changed_span_ids"]) == {"supply", "firemain"}
    assert result["before"]["length_mm"] == 16000
    assert result["after"]["length_mm"] == 11000
    assert validate_network(result["proposed_network"], reference=result["original_network"])["hard_failures"] == 0
    retained = [a for a in result["search"]["attempts"] if a.get("reservation_mode") == "retain_existing"]
    assert retained and not any(a["accepted"] for a in retained)
    selected, = [a for a in result["search"]["attempts"] if a.get("selected")]
    assert selected["reservation_mode"] == "prioritized_reservations"
    assert selected["candidate_digest"] == network_digest(result["proposed_network"])
    assert selected["metrics"] == result["after"]
    assert any(s["released_unplanned_span_ids"] for s in selected["span_searches"])
    assert result["optimization_quality"] == "HEURISTICALLY IMPROVED"


def test_unselected_other_system_cannot_be_temporarily_released_or_moved():
    net = scenario()
    result = connected.optimize_network(net, selected_span_ids=["supply"], max_evaluations=8)
    assert result["status"] == "NO IMPROVEMENT FOUND"
    assert result["source_digest"] == network_digest(net)
    assert result["proposed_network"] is None
    assert result["selection"]["modifiable_span_ids"] == ["supply"]
    assert all(not s["released_unplanned_span_ids"] for a in result["search"]["attempts"] for s in a.get("span_searches", []))


def test_short_individually_solved_routes_never_bypass_whole_network_collision_gate(monkeypatch):
    net = scenario()
    def individually_shortest(network, span, cancelled, **options):
        return [span["points"][0], span["points"][-1]], {"reason": "Deliberately unsafe independent shortest routes"}, False
    monkeypatch.setattr(connected, "_solve_span", individually_shortest)
    result = connected.optimize_network(net, max_evaluations=4, objective_weights={"length": 1})
    assert result["proposed_network"] is None
    assert result["status"] == "NO IMPROVEMENT FOUND"
    assert all(a["hard_failures"] > 0 and not a["accepted"] for a in result["search"]["attempts"])
    assert result["after"] == result["before"]


def test_time_budget_is_distinct_from_designer_cancellation(monkeypatch):
    calls = 0
    def clock():
        nonlocal calls
        calls += 1
        return 0. if calls == 1 else 1.
    monkeypatch.setattr(connected, "time", SimpleNamespace(perf_counter=clock))
    result = connected.optimize_network(scenario(), max_seconds=.01)
    assert result["search"]["time_budget_reached"] is True
    assert result["search"]["cancelled"] is False
    assert result["status"] != "CANCELLED"
    assert result["search"]["evaluations"] == 0
    assert result["proposed_network"] is None


def test_node_variants_cannot_move_components_touching_an_unselected_span():
    path = Path(__file__).resolve().parents[1]/"examples/connected/c-connected-branch.json"
    net = connected.normalize_network(json.loads(path.read_text()))
    variants = list(connected._node_variants(net, {"pump-valve"}))
    assert len(variants) == 1
    assert variants[0][1] is None
    assert variants[0][0] == net


def test_routing_reserves_inner_corner_fitting_volume_away_from_sharp_centerline():
    net = connected.normalize_network(scenario())
    selected = net["spans"][0]
    neighbor = deepcopy(net["spans"][1])
    neighbor["points"] = [[1000,3000,1500], [3000,3000,1500], [3000,5000,1500]]
    neighbor["fitting_catalog"]["radius_mm"] = 1000
    item = connected._neighbor_obstacle(neighbor, selected, net)
    point = [2000+1000/math.sqrt(2), 4000-1000/math.sqrt(2), 1500]
    sharp_gap = min(segment_distance(point, point, a, b) for a, b in zip(neighbor["points"], neighbor["points"][1:]))
    assert sharp_gap > 250
    assert any(cap.get("kind") == "fitting" and segment_distance(point, point, cap["start"], cap["end"]) <= cap["radius"] for cap in item["capsules"])


def test_planning_routes_around_other_system_valve_body_not_just_its_thin_pipe():
    path = Path(__file__).resolve().parents[1]/"examples/connected/a-already-good.json"
    net = json.loads(path.read_text())
    net["systems"].append({"id": "other", "type": "pressurized"})
    valve = {"id": "other-valve", "type": "valve", "system_id": "other", "position": [3500,1700,1500],
             "fixed": True, "nominal_diameter_mm": 50, "dimensions": {"body_radius_mm": 300},
             "constraints": {}, "stem_orientation": "horizontal", "stem_height_mm": 1500, "fitting_catalog": None}
    port = {"id": "other-port", "type": "equipment_port", "system_id": "other", "position": [3500,5000,1500],
            "fixed": True, "nominal_diameter_mm": 50, "dimensions": {}, "constraints": {}}
    net["nodes"].extend([valve, port])
    neighbor = deepcopy(net["spans"][0])
    neighbor.update({"id": "other-pipe", "system_id": "other", "from_node": "other-valve", "to_node": "other-port",
                     "points": [valve["position"], port["position"]], "modifiable": False})
    net["spans"].append(neighbor)
    result = connected.optimize_network(net, max_evaluations=2)
    assert result["original_validation"]["hard_failures"] > 0
    assert result["status"] == "IMPROVED"
    assert result["validation"]["hard_failures"] == 0
    assert result["changed_span_ids"] == ["supply"]
    assert next(s for s in result["proposed_network"]["spans"] if s["id"] == "other-pipe") == next(s for s in result["original_network"]["spans"] if s["id"] == "other-pipe")
    scope = next(c for c in result["validation"]["checks"] if c["id"] == "preservation.optimization_scope")
    assert scope["status"] == "PASS"


def test_explicit_unknown_catalog_is_reported_without_crashing_or_inventing_fittings():
    path = Path(__file__).resolve().parents[1]/"examples/connected/b-collision-repair.json"
    net = json.loads(path.read_text())
    net["spans"][0]["fitting_catalog"] = None
    result = connected.optimize_network(net, max_evaluations=1)
    assert result["proposed_network"] is None
    assert result["search"]["attempts"][0]["missing_physical_fitting_geometry"] is True
