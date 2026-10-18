"""Ten computed, source-only engineering demonstrations; no result fixtures."""
from copy import deepcopy
import json
import math
from pathlib import Path

import pytest

from pipe_router.connected import optimize_network
from pipe_router.network_model import network_digest, normalize_network
from pipe_router.network_validation import validate_network

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "multi-system"
CASES = (
    ("01-already-good", "NO IMPROVEMENT FOUND"),
    ("02-single-pipe-conflict", "IMPROVED"),
    ("03-connected-network", "IMPROVED"),
    ("04-multi-pipe-congestion", "IMPROVED"),
    ("05-multiple-systems", "IMPROVED"),
    ("06-directional-drainage", "NO IMPROVEMENT FOUND"),
    ("07-unavailable-fitting", "NO FEASIBLE ROUTE FOUND"),
    ("08-sealed-compartment", "NO FEASIBLE ROUTE FOUND"),
    ("09-coordinated-improvement", "IMPROVED"),
    ("10-representative-compartment", "IMPROVED"),
)


def load(identifier):
    return json.loads((EXAMPLES / f"{identifier}.json").read_text(encoding="utf-8"))


def check(validation, identifier):
    return next(row for row in validation["checks"] if row["id"] == identifier)


@pytest.fixture(scope="module")
def computed():
    outcomes = {}
    for identifier, _ in CASES:
        source = load(identifier)
        snapshot = deepcopy(source)
        outcomes[identifier] = optimize_network(source, max_evaluations=16)
        assert source == snapshot
    return outcomes


@pytest.mark.parametrize("identifier,status", CASES)
def test_synthetic_scenes_are_calculated_and_independently_audited(identifier, status, computed):
    scene = load(identifier)
    assert scene["provenance"] == "synthetic"
    assert scene["drawing"]["id"].startswith("SYNTHETIC-V5-")
    # Examples contain inputs, never a canned optimizer response or geometry.
    assert not ({"proposed_network", "result", "before", "after"} & scene.keys())
    result = computed[identifier]
    original = normalize_network(scene)
    assert result["status"] == status
    assert result["source_digest"] == network_digest(original)
    assert result["original_network"] == original
    assert result["before"]["length_mm"] == pytest.approx(sum(
        math.dist(a, b) for span in original["spans"]
        for a, b in zip(span["points"], span["points"][1:])))
    proposal = result["proposed_network"]
    if status != "IMPROVED":
        assert proposal is None
        assert result["changed_span_ids"] == []
        assert result["after"] == result["before"]
        assert "not a proof" in " ".join(result["search"]["limitations"])
        return
    audited = validate_network(proposal, reference=original)
    assert audited["hard_failures"] == 0
    assert audited["unverified"] > 0
    assert result["release_status"] == "REVIEW REQUIRED"
    assert proposal["drawing"] == original["drawing"]
    assert proposal["coordinate_system"] == original["coordinate_system"]
    assert {n["id"] for n in proposal["nodes"]} == {n["id"] for n in original["nodes"]}
    proposed_spans = {span["id"]: span for span in proposal["spans"]}
    for node in original["nodes"]:
        after = next(n for n in proposal["nodes"] if n["id"] == node["id"])
        if node["fixed"]:
            assert after["position"] == node["position"]
    for span in original["spans"]:
        after = proposed_spans[span["id"]]
        for field in ("system_id", "from_node", "to_node", "outer_diameter_mm",
                      "nominal_diameter_mm", "cad_handle", "fitting_catalog"):
            assert after[field] == span[field]
        if not span["modifiable"]:
            assert after == span


def test_three_congested_runs_are_all_repaired_without_new_pipe_interference(computed):
    result = computed["04-multi-pipe-congestion"]
    assert len(result["changed_span_ids"]) == 3
    assert result["original_validation"]["hard_failures"] > 0
    assert check(result["validation"], "collision.pipe_to_structure")["status"] == "PASS"
    assert check(result["validation"], "collision.pipe_to_pipe")["status"] == "PASS"
    assert check(result["validation"], "clearance.shock")["evidence"]["minimum_surface_gap_mm"] >= 50.8
    # Feasibility dominates objective: fixing the clash can require more pipe.
    assert result["after"]["length_mm"] > result["before"]["length_mm"]


def test_branch_drainage_checks_all_flow_directed_segments(computed):
    result = computed["06-directional-drainage"]
    rows = check(result["validation"], "drainage.pitch")["evidence"]["segments"]
    assert len(rows) == 3
    assert all(row["fall_mm"] >= row["required_fall_mm"] for row in rows)
    reversed_flow = normalize_network(load("06-directional-drainage"))
    reversed_flow["spans"][0]["flow_direction"] = "to_from"
    assert check(validate_network(reversed_flow), "drainage.pitch")["status"] == "FAIL"


def test_missing_fitting_geometry_cannot_be_replaced_by_demo_dimensions(computed):
    result = computed["07-unavailable-fitting"]
    assert result["proposed_network"] is None
    original = result["original_network"]["spans"][0]
    assert original["fitting_catalog"]["status"] == "missing"
    assert "radius_mm" not in original["fitting_catalog"]
    assert result["original_validation"]["hard_failures"] > 0


def test_coordinated_proposal_vacates_another_valid_system(computed):
    result = computed["09-coordinated-improvement"]
    assert result["original_validation"]["hard_failures"] == 0
    assert set(result["changed_span_ids"]) == {"supply", "firemain"}
    assert result["after"]["length_mm"] < result["before"]["length_mm"]
    accepted = [row for row in result["search"]["attempts"] if row.get("accepted")]
    assert any(row["reservation_mode"] == "prioritized_reservations" for row in accepted)
    # With the independently valid firemain locked, this same bounded search
    # cannot invent a too-tight cooling elbow to realize the joint proposal.
    selected_only = optimize_network(load("09-coordinated-improvement"),
                                     selected_span_ids=["supply"],
                                     max_evaluations=16)
    assert selected_only["status"] == "NO IMPROVEMENT FOUND"
    assert selected_only["proposed_network"] is None


def test_representative_compartment_retains_unselected_systems_and_access(computed):
    result = computed["10-representative-compartment"]
    scene = result["original_network"]
    assert len(scene["systems"]) == 3
    assert {o["kind"] for o in scene["obstacles"]} >= {
        "equipment", "duct", "wireway", "walkway", "ladder"}
    assert set(result["changed_span_ids"]) == {"pump-valve", "valve-tee"}
    assert check(result["validation"], "network.connectivity")["status"] == "PASS"
    assert result["after"]["length_mm"] < result["before"]["length_mm"]


def test_all_ten_scenes_are_source_only_and_original_six_are_retained():
    assert sorted(path.stem for path in EXAMPLES.glob("*.json")) == sorted(
        identifier for identifier, _ in CASES)
    originals = EXAMPLES.parent / "connected"
    assert len(list(originals.glob("*.json"))) == 6
