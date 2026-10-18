"""Computed, dimensionally consistent objectives with explicit evidence gaps."""
from copy import deepcopy
import json
import math
from pathlib import Path

import pytest

from pipe_router.connected import metrics, optimize_network, resolve_selection
from pipe_router.network_model import normalize_network, network_digest
from pipe_router.network_objective import hydraulic_estimate, resolve_objective, validate_weights


def source():
    return normalize_network(json.loads((Path(__file__).resolve().parents[1]/"examples/connected/a-already-good.json").read_text()))


def hydraulics():
    return {"inner_diameter_mm": 50., "density_kg_m3": 1000., "dynamic_viscosity_pa_s": .001,
            "volumetric_flow_m3_s": 1e-5, "roughness_mm": .01, "bend_loss_coefficient": .4,
            "source": "Invented constant-flow water values for numerical regression only"}


def test_objective_terms_have_stable_source_normalization_and_explicit_units():
    original = source()
    candidate = deepcopy(original)
    candidate["spans"][0]["points"] = [[500,1500,1500], [500,2500,1500], [7500,2500,1500], [7500,1500,1500]]
    before, after = metrics(original, reference=original), metrics(candidate, reference=original)
    assert before["normalization_scales"] == after["normalization_scales"]
    assert after["objective_units"] == "dimensionless source-normalized score"
    assert after["objective"] == pytest.approx(sum(t["contribution"] for t in after["objective_terms"].values()))
    assert after["objective_terms"]["length"]["normalized"] == pytest.approx(9000/7000)
    assert after["changed_span_count"] == 1
    assert after["objective_terms"]["modifications"]["status"] == "GEOMETRIC_PROXY"
    assert "hydraulic" in after["unverified_objective_terms"]
    assert after["effective_objective_weights"]["hydraulic"] == 0


def test_custom_length_and_bend_strategies_really_reverse_a_geometric_tradeoff():
    original = source()
    short = deepcopy(original)
    short["spans"][0]["points"] = [[500,1500,1500], [1000,1500,1500], [1000,2500,1500], [7000,2500,1500], [7000,1500,1500], [7500,1500,1500]]
    long = deepcopy(original)
    long["spans"][0]["points"] = [[500,1500,1500], [500,3500,1500], [7500,3500,1500], [7500,1500,1500]]
    assert metrics(short, original, strategy="length")["objective"] < metrics(long, original, strategy="length")["objective"]
    assert metrics(short, original, strategy="bends")["objective"] > metrics(long, original, strategy="bends")["objective"]
    custom = metrics(short, original, objective_weights={"length": 3, "bends": 1})
    assert custom["effective_objective_weights"]["length"] == .75
    assert custom["effective_objective_weights"]["bends"] == .25


def test_laminar_darcy_estimate_matches_closed_form_without_claiming_network_head():
    net = source()
    net["spans"][0]["hydraulics"] = hydraulics()
    result = hydraulic_estimate(net)
    length_m = 7.
    expected = 128*.001*1e-5*length_m/(math.pi*.05**4)
    assert result["aggregate_segment_loss_pa"] == pytest.approx(expected)
    assert result["spans"][0]["regime"] == "laminar"
    assert result["status"] == "ESTIMATED"
    assert "not solved network pressure" in result["scope"]
    scored = metrics(net, net, strategy="hydraulic")
    assert scored["unverified_objective_terms"] == []
    assert scored["effective_objective_weights"]["hydraulic"] == .95


@pytest.mark.parametrize("field", ["inner_diameter_mm", "density_kg_m3", "dynamic_viscosity_pa_s", "volumetric_flow_m3_s", "roughness_mm", "bend_loss_coefficient", "source"])
def test_hydraulic_evidence_is_never_filled_with_demo_defaults(field):
    net = source()
    data = hydraulics()
    del data[field]
    net["spans"][0]["hydraulics"] = data
    result = metrics(net, net, strategy="hydraulic")
    assert result["hydraulic_evidence"]["status"] == "UNVERIFIED"
    assert result["hydraulic_evidence"]["aggregate_segment_loss_pa"] is None
    assert result["unverified_objective_terms"] == ["hydraulic"]
    assert result["effective_objective_weights"]["hydraulic"] == 0


@pytest.mark.parametrize("extreme", [{"inner_diameter_mm": 1e-300, "roughness_mm": 0}, {"volumetric_flow_m3_s": 1e308}, {"volumetric_flow_m3_s": 1e-300}])
def test_extreme_hydraulic_values_are_reported_unverified_instead_of_crashing(extreme):
    net = source()
    net["spans"][0]["hydraulics"] = {**hydraulics(), **extreme}
    result = hydraulic_estimate(net)
    assert result["status"] == "UNVERIFIED"
    assert result["aggregate_segment_loss_pa"] is None


@pytest.mark.parametrize("value", [{}, {"length": 0}, {"length": True}, {"length": float("inf")}, {"unknown": 1}, {"bends": -1}, {"length": 1001}])
def test_invalid_weights_fail_before_search(value):
    with pytest.raises(ValueError, match="objective_weights"):
        validate_weights(value)


def test_minimal_modification_alias_is_canonical_and_unknown_strategy_is_rejected():
    assert resolve_objective("modification")["strategy"] == "minimal_modifications"
    with pytest.raises(ValueError, match="strategy"):
        resolve_objective("invented-global-optimal")


def test_explicit_hydraulic_only_weights_fall_back_transparently_when_evidence_missing():
    result = metrics(source(), objective_weights={"hydraulic": 1})
    assert result["objective_fallback"] == "length because no requested term has sufficient evidence"
    assert result["effective_objective_weights"]["length"] == 1
    assert result["unverified_objective_terms"] == ["hydraulic"]


@pytest.mark.parametrize("seconds", [True, 0, -1, float("inf"), 121, "30"])
def test_invalid_time_budgets_rejected_before_search(seconds):
    with pytest.raises(ValueError, match="max_seconds"):
        optimize_network(source(), max_seconds=seconds)


def test_network_search_settings_do_not_modify_source_digest():
    net = source()
    net["search"].update({"strategy": "length", "max_seconds": 2, "objective_weights": {"length": 1}})
    snapshot = deepcopy(net)
    result = optimize_network(net, max_evaluations=1)
    assert net == snapshot
    assert result["source_digest"] == network_digest(snapshot)
    assert result["strategy"] == "length"
    assert result["search"]["max_seconds"] == 2


def test_selection_is_explicit_union_and_does_not_rewrite_permissions():
    net = source()
    span = deepcopy(net["spans"][0])
    span.update({"id": "second", "system_id": "other"})
    net["spans"].append(span)
    original = deepcopy(net)
    resolved = resolve_selection(net, selected_span_ids=["supply"], selected_system_ids=["other"])
    assert resolved["selected_span_ids"] == ["second", "supply"]
    assert net == original
    with pytest.raises(ValueError, match="selected_span_ids"):
        resolve_selection(net, [])
