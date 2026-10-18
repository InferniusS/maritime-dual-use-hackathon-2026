"""Endpoint evidence separates clearance from overlap without changing checks."""
from copy import deepcopy
import math

import pytest

from pipe_router import core
from pipe_router.endpoint_contacts import contact_evidence, envelope_contact_margin
from pipe_router.workflow import with_endpoint_connections, validate_layout_route


def box(identifier, low, high):
    return {"id": identifier, "min": low, "max": high}


def layout(obstacles):
    return {"name": "Endpoint contact fixture", "units": "mm", "provenance": "synthetic",
            "system_type": "pressurized", "bounds": {"min": [-200, -200, -200], "max": [1200, 200, 200]},
            "pipe": {"start": [0, 0, 0], "end": [1000, 0, 0], "original_route": [[0, 0, 0], [1000, 0, 0]], "diameter": 20},
            "obstacles": obstacles, "rules": {"clearance": 20, "grid_step": 50, "bend_penalty": 100}}


def test_clearance_contact_is_not_reported_as_a_physical_connection():
    obstacle = box("behind", [-60, -20, -20], [-15, 20, 20])
    evidence = contact_evidence([[0, 0, 0], [1000, 0, 0]], "start", obstacle, 10, 20, 250)
    assert evidence["contact_kind"] == "clearance_only"
    assert evidence["remaining_pipe_clearance_mm"] == pytest.approx(5)
    assert evidence["connection_verified"] is False
    assert evidence["requires_explicit_confirmation"] is True
    assert "not evidence of a physical joint" in evidence["contact_summary"]
    assert "empty space" in evidence["contact_summary"]
    assert evidence["stub_preview"]["required_length_mm"] == pytest.approx(15.001, abs=1e-5)
    assert evidence["stub_preview"]["status"] == "available"


def test_overlap_remains_only_envelope_evidence_with_no_automatic_waiver():
    source = layout([box("joint", [-20, -20, -20], [5, 20, 20])])
    before = deepcopy(source)
    evidence = contact_evidence(source["pipe"]["original_route"], "start", source["obstacles"][0], 10, 20, 250)
    assert evidence["contact_kind"] == "pipe_envelope_overlap"
    assert evidence["remaining_pipe_clearance_mm"] == 0
    assert "does not prove" in evidence["contact_summary"]
    assert source == before
    assert not validate_layout_route(source, source["pipe"]["original_route"])["valid"]


@pytest.mark.parametrize("point", [[0, 0, 0], [0, 30, 0], [-30, 15, 0], [70, 0, 0], [20, 20, 20]])
def test_margin_matches_clipped_capsule_checker_at_endpoint(point):
    obstacle = box("clipped", [0, -10, -10], [40, 10, 10])
    obstacle["capsules"] = [{"start": [0, 0, 0], "end": [40, 0, 0], "radius": 10,
                             "clip_min": [0, -10, -10], "clip_max": [40, 10, 10]}]
    margin = envelope_contact_margin(point, obstacle)
    above = core._prepare_obstacle(obstacle, "test", margin + 0.0001)
    assert core._segment_intersects_box(tuple(point), tuple(point), above)
    if margin > 0.0001:
        below = core._prepare_obstacle(obstacle, "test", margin - 0.0001)
        assert not core._segment_intersects_box(tuple(point), tuple(point), below)


@pytest.mark.parametrize("obstacle,status", [
    (box("long", [-20, -20, -20], [350, 20, 20]), "exceeds_limit"),
    (box("whole", [-20, -20, -20], [1100, 20, 20]), "covers_route"),
])
def test_stub_preview_explains_unusable_selection_before_approval(obstacle, status):
    evidence = contact_evidence([[0, 0, 0], [1000, 0, 0]], "start", obstacle, 10, 20, 250)
    assert evidence["stub_preview"]["status"] == status
    if status == "covers_route":
        assert evidence["stub_preview"]["required_length_mm"] is None
    else:
        assert evidence["stub_preview"]["required_length_mm"] > 250


def test_selected_stub_records_evidence_without_waiving_a_nearby_nonselected_contact():
    source = layout([box("joint", [-20, -20, -20], [5, 20, 20]),
                     box("nearby", [-20, 15, -20], [5, 30, 20])])
    source["cad_context"] = {"endpoint_contacts": [{"endpoint": "start", "obstacle_id": "joint",
        **contact_evidence(source["pipe"]["original_route"], "start", source["obstacles"][0], 10, 20, 250)}]}
    prepared = with_endpoint_connections(source, {"start": ["joint"]}, 250)
    assert prepared["obstacles"] == source["obstacles"]
    record = prepared["protected_endpoints"][0]
    assert record["obstacle_ids"] == ["joint"]
    assert record["original_stub_length_mm"] == pytest.approx(35.001, abs=1e-5)
    assert record["contact_evidence"][0]["connection_verified"] is False
    checked = validate_layout_route(prepared, prepared["pipe"]["original_route"])
    assert not checked["valid"]
    assert any(issue.get("obstacle") == "nearby" for issue in checked["issues"])


def test_reentry_into_same_selected_obstacle_is_not_exempted():
    obstacle = box("two-lobes", [-10, -10, -10], [310, 10, 10])
    obstacle["capsules"] = [{"start": [x, 0, 0], "end": [x, 0, 0], "radius": 10} for x in (0, 300)]
    source = layout([obstacle])
    evidence = contact_evidence(source["pipe"]["original_route"], "start", obstacle, 10, 20, 250)
    assert evidence["stub_preview"]["reentry_distance_mm"] == pytest.approx(260, abs=1e-5)
    prepared = with_endpoint_connections(source, {"start": ["two-lobes"]}, 250)
    checked = validate_layout_route(prepared, prepared["pipe"]["original_route"])
    assert not checked["valid"]
    assert any(issue.get("obstacle") == "two-lobes" for issue in checked["issues"])


def test_end_contact_uses_reversed_distance_without_moving_original_endpoint():
    source = layout([box("end-joint", [995, -20, -20], [1020, 20, 20])])
    evidence = contact_evidence(source["pipe"]["original_route"], "end", source["obstacles"][0], 10, 20, 250)
    prepared = with_endpoint_connections(source, {"end": ["end-joint"]}, 250)
    assert evidence["stub_preview"]["required_length_mm"] == pytest.approx(35.001, abs=1e-5)
    assert prepared["protected_endpoints"][0]["points"][-1] == [1000, 0, 0]
    assert math.dist(*prepared["protected_endpoints"][0]["points"]) == pytest.approx(35.001, abs=1e-5)
