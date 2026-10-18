"""Calculated connected industrial assemblies, physical interfaces and scale."""
from copy import deepcopy
import json
from pathlib import Path

import networkx as nx
import pytest

from pipe_router.connected import optimize_network
from pipe_router.industrial_examples import build_manifold, industrial_scenarios
from pipe_router.network_model import normalize_network
from pipe_router.network_validation import validate_network

EXAMPLES = Path(__file__).resolve().parents[1]/"examples"/"industrial"
SCALES = (("a-industrial-5", 5), ("b-industrial-10", 10),
          ("c-industrial-20", 20), ("d-industrial-40", 40))


def cooling_graph(network):
    graph = nx.Graph()
    for span in network["spans"]:
        if span["system_id"] == "cooling":
            graph.add_edge(span["from_node"], span["to_node"], id=span["id"])
    return graph


@pytest.mark.parametrize("identifier,count", SCALES)
def test_scale_is_an_actual_connected_branched_assembly(identifier, count):
    network = industrial_scenarios()[identifier]
    assert len(network["spans"]) == count
    assert len(network["nodes"]) == count+1
    assert all(span["modifiable"] for span in network["spans"])
    graph = cooling_graph(network)
    assert nx.is_connected(graph)
    assert graph.number_of_edges() == count
    assert all(graph.degree(node["id"]) > 0 for node in network["nodes"])
    assert any(degree >= 3 for _, degree in graph.degree)
    assert {node["type"] for node in network["nodes"]} >= {
        "equipment_port", "tee", "valve", "reducer", "flange"}
    assert {span["outer_diameter_mm"] for span in network["spans"]} == {60, 100}
    assert network["supports"]
    assert all(support["dimensions"]["body_length_mm"] > 0 for support in network["supports"])
    assert all(node["cad_handle"].startswith("SYN51-") for node in network["nodes"])
    assert all(span["cad_handle"].startswith("SYN51-") for span in network["spans"])
    assert network["provenance"] == "synthetic"


def test_generator_reproduces_only_source_inputs_and_supports_nonpreset_counts():
    sources = industrial_scenarios()
    assert len(sources) == 7
    assert {path.stem for path in EXAMPLES.glob("*.json")} == set(sources)
    for identity, network in sources.items():
        assert json.loads((EXAMPLES/f"{identity}.json").read_text(encoding="utf-8")) == network
        assert not ({"proposed_network", "before", "after", "result"} & network.keys())
        graph = cooling_graph(network)
        assert nx.is_connected(graph)
        if identity[0] in "efg":
            assert graph.number_of_edges() == 20
    additional = build_manifold(13)
    assert len(additional["spans"]) == 13
    assert nx.is_connected(cooling_graph(additional))


@pytest.mark.parametrize("options", [
    {"run_count": True}, {"run_count": 4}, {"run_count": 81},
    {"first_station_mm": float("nan")}, {"first_station_mm": float("inf")},
    {"first_station_mm": True},
])
def test_generator_rejects_invalid_counts_and_nonfinite_geometry(options):
    with pytest.raises(ValueError):
        build_manifold(**options)


@pytest.fixture(scope="module")
def computed():
    results = {}
    for identity, source in industrial_scenarios().items():
        snapshot = deepcopy(source)
        results[identity] = optimize_network(source, max_evaluations=32, max_seconds=60)
        assert source == snapshot
    return results


@pytest.mark.parametrize("identifier,count", SCALES)
def test_actual_scale_optimization_preserves_all_fixed_interfaces(identifier, count, computed):
    result = computed[identifier]
    assert result["status"] == "IMPROVED"
    assert len(result["selection"]["modifiable_span_ids"]) == count
    assert result["search"]["evaluations"] > 0
    assert result["search"]["local_solves"] > 0
    assert result["changed_span_ids"]
    original, proposal = result["original_network"], result["proposed_network"]
    audit = validate_network(proposal, reference=original)
    assert audit["hard_failures"] == 0
    assert audit["unverified"] > 0
    assert result["after"]["length_mm"] < result["before"]["length_mm"]
    assert nx.is_connected(cooling_graph(proposal))
    assert {(s["id"], s["from_node"], s["to_node"], s["system_id"])
            for s in proposal["spans"]} == {
                (s["id"], s["from_node"], s["to_node"], s["system_id"])
                for s in original["spans"]}
    for before, after in zip(original["nodes"], proposal["nodes"]):
        assert before == after  # All generated component boundaries are fixed.
    for before, after in zip(original["spans"], proposal["spans"]):
        for key in ("outer_diameter_mm", "nominal_diameter_mm", "fitting_catalog", "cad_handle"):
            assert after[key] == before[key]


def test_forty_eligible_connected_spans_are_considered_in_real_bounded_groups(computed):
    result = computed["d-industrial-40"]
    assert result["search"]["selected_span_count"] == 40
    assert result["search"]["total_neighborhoods"] > 1
    attempts = result["search"]["attempts"]
    considered = {solve["span_id"] for attempt in attempts for solve in attempt.get("span_searches", [])}
    assert considered == {span["id"] for span in result["original_network"]["spans"]}
    assert all(len(attempt.get("order", [])) <= result["search"]["max_neighborhood_spans"]
               for attempt in attempts)
    assert result["search"]["expanded_states"] >= 0
    assert result["search"]["grid_nodes_peak"] >= 0


def test_congested_acceptance_repairs_several_connected_branches_with_other_system_retained(computed):
    result = computed["e-congested-assembly"]
    source, proposal = result["original_network"], result["proposed_network"]
    assert cooling_graph(source).number_of_edges() == 20
    assert result["original_validation"]["hard_failures"] > 0
    assert result["status"] == "IMPROVED"
    assert len(result["changed_span_ids"]) >= 3
    assert validate_network(proposal, reference=source)["hard_failures"] == 0
    assert nx.is_connected(cooling_graph(proposal))
    before_fire = next(span for span in source["spans"] if span["system_id"] == "firemain")
    after_fire = next(span for span in proposal["spans"] if span["system_id"] == "firemain")
    assert before_fire == after_fire
    assert not before_fire["modifiable"]


def test_complex_context_requires_coordinated_corridor_exchange(computed):
    result = computed["f-coordinated-assembly"]
    assert result["original_validation"]["hard_failures"] == 0
    assert result["status"] == "IMPROVED"
    assert {"main-01", "firemain-transfer"} <= set(result["changed_span_ids"])
    assert result["validation"]["hard_failures"] == 0
    source = industrial_scenarios()["f-coordinated-assembly"]
    only_main = optimize_network(source, selected_span_ids=["main-01"],
                                 max_evaluations=8, max_seconds=30)
    assert only_main["status"] == "NO IMPROVEMENT FOUND"
    assert only_main["proposed_network"] is None
    naive = normalize_network(source)
    main = next(span for span in naive["spans"] if span["id"] == "main-01")
    main["points"] = [main["points"][0], main["points"][-1]]
    assert validate_network(naive)["hard_failures"] > 0


def test_blocked_network_keeps_failed_source_and_distinguishes_timeout(computed):
    result = computed["g-blocked-assembly"]
    assert result["proposed_network"] is None
    assert not result["changed_span_ids"]
    assert result["validation"]["hard_failures"] > 0
    assert result["status"] == "NO FEASIBLE ROUTE FOUND"
    assert result["search"]["termination_reason"] == "BOUNDED_SEARCH_COMPLETED"
    assert not result["search"]["time_budget_reached"]
    assert "not a proof" in " ".join(result["search"]["limitations"])
    expired = optimize_network(industrial_scenarios()["g-blocked-assembly"], max_seconds=.000001)
    assert expired["status"] == "TIME BUDGET EXCEEDED"
    assert expired["search"]["termination_reason"] == "TIME_BUDGET"
    assert expired["proposed_network"] is None


def test_benchmark_records_real_geometry_progress_memory_and_post_audit():
    from scripts.benchmark_industrial import measure_scene
    source = build_manifold(5)
    evidence = measure_scene(source, repeats=1, max_evaluations=4, max_seconds=30)
    assert evidence["span_count"] == 5
    assert evidence["all_connections_preserved"]
    assert evidence["connections_preserved"] == 5
    assert evidence["final_hard_failures"] == 0
    assert evidence["progress"]["event_count"] > 0
    assert evidence["cold"]["median_ms"] > 0
    assert evidence["warm"]["median_ms"] > 0
    assert evidence["python_allocation"]["peak_python_allocation_bytes"] > 0
    assert evidence["process_high_water_rss_bytes"] is None or evidence["process_high_water_rss_bytes"] > 0
    assert evidence["before"]["developed_centerline_length_mm"] < evidence["before"]["polyline_centerline_length_mm"]
    assert evidence["after"]["polyline_centerline_length_mm"] < evidence["before"]["polyline_centerline_length_mm"]
