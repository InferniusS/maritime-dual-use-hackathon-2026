"""Computed connected-network scenarios using invented WCS geometry only."""
from copy import deepcopy
from collections import OrderedDict
import json
from pathlib import Path

import pytest

from pipe_router.connected import optimize_network
from pipe_router import connected
from pipe_router.network_model import normalize_network, network_digest
from pipe_router.network_validation import validate_network

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "connected"
CASES = (
    ("a-already-good", "NO IMPROVEMENT FOUND"),
    ("b-collision-repair", "IMPROVED"),
    ("c-connected-branch", "IMPROVED"),
    ("d-blocked", "NO FEASIBLE ROUTE FOUND"),
    ("e-multisystem", "IMPROVED"),
    ("f-directional-drainage", "NO IMPROVEMENT FOUND"),
)


def load(identifier):
    return json.loads((EXAMPLES / f"{identifier}.json").read_text())


def check(validation, identifier):
    return next(item for item in validation["checks"] if item["id"] == identifier)


@pytest.fixture(scope="module")
def computed():
    results = {}
    for identifier, _ in CASES:
        source = load(identifier)
        snapshot = deepcopy(source)
        results[identifier] = optimize_network(source, max_evaluations=8)
        assert source == snapshot
    return results


@pytest.mark.parametrize("identifier,status", CASES)
def test_actual_scenario_statuses_and_source_immutability(identifier, status, computed):
    source = load(identifier)
    snapshot = deepcopy(source)
    result = computed[identifier]
    assert result["status"] == status
    assert result["source_digest"] == network_digest(source)
    assert result["source_revision"] == source["drawing"]["revision"]
    assert result["original_network"] == normalize_network(snapshot)
    assert source == snapshot
    assert 1 <= result["search"]["evaluations"] <= 8
    assert result["search"]["elapsed_seconds"] >= 0
    assert len(result["validation"]["checks"]) >= 20
    proposed = result["proposed_network"]
    if status != "IMPROVED":
        assert proposed is None
        assert not result["changed_span_ids"]
        assert result["after"] == result["before"]
        return
    assert proposed is not None
    original = result["original_network"]
    independently_checked = validate_network(proposed, reference=original)
    assert independently_checked["hard_failures"] == 0
    assert proposed["drawing"] == original["drawing"]
    assert proposed["coordinate_system"] == original["coordinate_system"]
    assert proposed["units"] == "mm"
    assert {n["id"] for n in proposed["nodes"]} == {n["id"] for n in original["nodes"]}
    for before, after in zip(original["nodes"], proposed["nodes"]):
        assert after["cad_handle"] == before["cad_handle"]
        assert after["dimensions"] == before["dimensions"]
        assert after["nominal_diameter_mm"] == before["nominal_diameter_mm"]
        if before["fixed"]:
            assert after["position"] == before["position"]
    for before, after in zip(original["spans"], proposed["spans"]):
        for key in ("id", "from_node", "to_node", "system_id", "cad_handle", "instance_path", "outer_diameter_mm", "nominal_diameter_mm"):
            assert after[key] == before[key]
        if not before["modifiable"]:
            assert after == before
    # A bounded synthetic search does not certify fabrication or installation.
    assert independently_checked["unverified"] > 0
    assert result["release_status"] == "REVIEW REQUIRED"


def test_good_route_is_preserved_without_invented_benefit(computed):
    result = computed["a-already-good"]
    assert result["before"]["length_mm"] == pytest.approx(7000)
    assert result["before"]["direction_changes"] == 0
    assert result["original_validation"]["hard_failures"] == 0
    assert result["after"]["objective"] == result["before"]["objective"]


def test_colliding_source_is_repaired_even_when_route_becomes_longer(computed):
    result = computed["b-collision-repair"]
    assert check(result["original_validation"], "collision.pipe_to_structure")["status"] == "FAIL"
    assert result["after"]["length_mm"] > result["before"]["length_mm"]
    assert check(result["validation"], "collision.pipe_to_structure")["status"] == "PASS"
    shock = check(result["validation"], "clearance.shock")
    assert shock["status"] == "PASS"
    assert shock["evidence"]["minimum_surface_gap_mm"] >= 50.8
    assert check(result["validation"], "fittings.straight_between_bends")["status"] == "PASS"
    assert result["validation"]["manufacturability_verified"] is False


def test_connected_movable_valve_shortens_two_spans_and_keeps_branch(computed):
    result = computed["c-connected-branch"]
    original, proposed = result["original_network"], result["proposed_network"]
    assert len(original["spans"]) == 5
    assert sorted(node["type"] for node in original["nodes"]) == ["equipment_port"]*3 + ["tee"] + ["valve"]*2
    assert set(result["changed_span_ids"]) == {"pump-valve", "valve-tee"}
    assert result["before"]["length_mm"] - result["after"]["length_mm"] == pytest.approx(3000)
    valve = next(node for node in proposed["nodes"] if node["id"] == "valve-a")
    assert valve["position"] == [2000, 2000, 1500]
    assert all(low <= pos <= high for pos, low, high in zip(valve["position"], valve["movement_region"]["min"], valve["movement_region"]["max"]))
    assert next(span for span in proposed["spans"] if span["id"] == "branch") == next(span for span in original["spans"] if span["id"] == "branch")
    assert check(result["validation"], "network.connectivity")["status"] == "PASS"
    assert check(result["validation"], "valves.permitted_space")["status"] == "PASS"


def test_blocked_scene_does_not_generate_fabricated_candidate(computed):
    result = computed["d-blocked"]
    assert result["proposed_network"] is None
    assert result["original_validation"]["hard_failures"] > 0
    assert result["validation"]["hard_failures"] > 0
    assert not any(attempt["accepted"] for attempt in result["search"]["attempts"])
    assert "not a proof" in " ".join(result["search"]["limitations"])


def test_retained_other_system_has_full_finite_diameter_and_is_unchanged(computed):
    result = computed["e-multisystem"]
    assert check(result["original_validation"], "collision.pipe_to_pipe")["status"] == "FAIL"
    assert check(result["validation"], "collision.pipe_to_pipe")["status"] == "PASS"
    original = next(span for span in result["original_network"]["spans"] if span["id"] == "firemain")
    proposed = next(span for span in result["proposed_network"]["spans"] if span["id"] == "firemain")
    assert proposed == original
    assert proposed["outer_diameter_mm"] == 100
    assert proposed["modifiable"] is False
    assert check(result["validation"], "clearance.shock")["evidence"]["minimum_surface_gap_mm"] >= 50.8


def test_rotated_axes_and_reverse_storage_direction_use_real_segment_pitch(computed):
    result = computed["f-directional-drainage"]
    assessment = check(result["validation"], "drainage.pitch")
    assert assessment["status"] == "PASS"
    segment, = assessment["evidence"]["segments"]
    assert segment["fore_aft_run_mm"] == pytest.approx(0)
    assert segment["athwartship_run_mm"] == pytest.approx(4000)
    assert segment["fall_mm"] == pytest.approx(200)
    assert segment["required_fall_mm"] == pytest.approx(40)
    network = normalize_network(load("f-directional-drainage"))
    network["spans"][0]["flow_direction"] = "from_to"
    assert check(validate_network(network), "drainage.pitch")["status"] == "FAIL"


def test_drainage_endpoint_drop_does_not_hide_a_local_reverse_slope():
    network = normalize_network(load("f-directional-drainage"))
    # In actual to_from flow: inlet -> raised middle -> lower outlet.
    network["spans"][0]["points"].insert(1, [3000, 1000, 2100])
    assessment = check(validate_network(network), "drainage.pitch")
    assert assessment["status"] == "FAIL"
    assert sum(row["fall_mm"] for row in assessment["evidence"]["segments"]) == pytest.approx(200)
    assert any(row["fall_mm"] < 0 for row in assessment["evidence"]["segments"])


def test_drainage_missing_axes_remains_unverified():
    network = normalize_network(load("f-directional-drainage"))
    network["coordinate_system"].pop("athwartship")
    assert check(validate_network(network), "drainage.pitch")["status"] == "UNVERIFIED"


def test_required_compartment_search_and_entire_network_validation():
    network = load("a-already-good")
    network["compartments"] = [{"id":"mandatory-room", "min":[3000,2500,1000], "max":[5000,4500,2000]}]
    network["spans"][0]["constraints"]["required_compartments"] = ["mandatory-room"]
    result = optimize_network(network, max_evaluations=2)
    assert check(result["original_validation"], "network.required_compartments")["status"] == "FAIL"
    assert result["status"] == "IMPROVED"
    assert check(result["validation"], "network.required_compartments")["status"] == "PASS"
    assert result["validation"]["hard_failures"] == 0


def test_revision_and_catalog_changes_invalidate_cached_route(monkeypatch):
    monkeypatch.setattr(connected, "_CACHE", OrderedDict())
    source = load("b-collision-repair")
    cold = optimize_network(source, max_evaluations=2)
    warm = optimize_network(source, max_evaluations=2)
    assert cold["search"]["cache_hits"] == 0
    assert warm["search"]["cache_hits"] == 1
    assert cold["proposed_network"] == warm["proposed_network"]
    source["drawing"]["revision"] = "fixture-2"
    revised = optimize_network(source, max_evaluations=2)
    assert revised["search"]["cache_hits"] == 0
    assert revised["source_digest"] != cold["source_digest"]
    assert revised["source_revision"] == "fixture-2"
    source["spans"][0]["fitting_catalog"]["source"] += "; updated synthetic catalog revision"
    recataloged = optimize_network(source, max_evaluations=2)
    assert recataloged["search"]["cache_hits"] == 0
    assert recataloged["status"] == "IMPROVED"
