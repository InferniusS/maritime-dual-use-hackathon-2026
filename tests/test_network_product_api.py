"""Product boundaries exercise real HTTP jobs and lossless synthetic metadata."""
from copy import deepcopy
import time
import threading

import pytest

from test_network_api import client, network
from test_network_model import cad, network as example_network
from pipe_router.network_model import network_digest, normalize_network, reconstruct_network
from pipe_router.network_service import NetworkWorkspace


@pytest.mark.parametrize("options", [
    {"strategy": "invented"}, {"strategy": []},
    {"max_seconds": 0}, {"max_seconds": True}, {"max_seconds": 121},
    {"selected_span_ids": []}, {"selected_span_ids": ["missing"]},
    {"selected_span_ids": ["ab", "ab"]}, {"selected_span_ids": [True]},
    {"selected_system_ids": ["missing"]},
    {"objective_weights": {}}, {"objective_weights": {"length": 0}},
    {"objective_weights": {"length": True}}, {"objective_weights": {"unknown": 1}},
])
def test_invalid_options_rejected_before_background_worker(client, network, options):
    response = client.post("/api/network/jobs", json={"network": network, **options})
    assert response.status_code == 422, response.text
    # Bad requests must not acquire or strand the single calculation slot.
    valid = client.post("/api/network/optimize", json={"network": network, "max_evaluations": 1})
    assert valid.status_code == 200, valid.text


def test_async_selection_and_strategy_keep_immutable_review_source(client, network):
    source = deepcopy(network)
    started = client.post("/api/network/jobs", json={
        "network": network, "selected_span_ids": ["ab"], "strategy": "length",
        "objective_weights": {"length": 1}, "max_seconds": 5, "max_evaluations": 1,
    })
    assert started.status_code == 200, started.text
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = client.get("/api/network/jobs/" + started.json()["id"]).json()
        if job["status"] not in ("queued", "running"):
            break
        time.sleep(.01)
    assert job["status"] == "complete", job
    response = job["response"]
    result = response["result"]
    assert result["source_digest"] == network_digest(source)
    assert result["strategy"] == "length"
    assert response["network"] == normalize_network(source)
    assert result["original_network"] == normalize_network(source)
    assert network == source
    exported = client.post("/api/network/export", json={
        "network": response["network"], "review_token": response["review_token"],
        "proposal_id": result["proposal_id"], "approved": True,
    })
    assert exported.status_code == 200, exported.text
    urls = list(exported.json()["exports"].values())
    assert any(url.endswith("network-comparison.svg") for url in urls)
    assert any(url.endswith("coordinates.csv") for url in urls)
    assert all(client.get(url).status_code == 200 for url in urls)


def test_cad_round_trip_retains_explicit_engineering_metadata_and_source_hash():
    source = cad()
    source["source_sha256"] = "a" * 64
    source["systems"] = [{"id": "cooling", "type": "pressurized"}]
    first = source["route_candidates"][0]
    first.update({
        "pipe_id": "cooling-pipe-A", "system_type": "pressurized", "flow_direction": "from_to",
        "modifiable": False, "fitting_catalog": {"status": "provisional", "available": False, "radius_mm": 228.6},
        "manufacturing": {"pieces": []}, "hydraulics": {"inner_diameter_mm": 50, "density_kg_m3": 997},
        "constraints": {"preserve_existing": True, "required_compartments": []},
        "engineering_metadata": {"source": "Synthetic designer-supplied facts"},
    })
    snapshot = deepcopy(source)
    result = reconstruct_network(source)
    span = result["spans"][0]
    for key in ("pipe_id", "system_type", "flow_direction", "modifiable", "fitting_catalog", "manufacturing", "hydraulics", "engineering_metadata"):
        assert span[key] == first[key]
    assert span["constraints"]["preserve_existing"]
    assert span["source_instances"] == first["source_instances"]
    assert result["source_snapshot_sha256"] == source["source_sha256"]
    assert result["drawing"] == source["drawing"]
    assert result["systems"] == source["systems"]
    assert source == snapshot
    modified = deepcopy(result)
    modified["source_snapshot_sha256"] = "b" * 64
    assert network_digest(modified) != network_digest(result)


@pytest.mark.parametrize("metadata", [
    {"port_direction": [0, 0, 0]}, {"port_direction": [0, 0]},
    {"constraints": {"angular_tolerance_deg": -1}},
    {"constraints": {"angular_tolerance_deg": True}},
    {"constraints": {"port_directions": {"missing": [1, 0, 0]}}},
    {"constraints": {"port_nominal_diameters_mm": {"s1": 0}}},
])
def test_port_metadata_cannot_bypass_geometric_contract(metadata):
    source = example_network()
    source["nodes"][0].update(metadata)
    with pytest.raises(ValueError):
        normalize_network(source)


def test_port_directions_are_vectors_not_scaled_coordinates():
    source = example_network()
    first = source["nodes"][0]
    first["port_direction"] = [1, 0, 0]
    first["constraints"] = {"angular_tolerance_deg": 3}
    assert normalize_network(source)["nodes"][0]["port_direction"] == [1, 0, 0]


def test_nonobject_cad_candidate_fails_clearly_instead_of_attribute_error():
    source = cad()
    source["route_candidates"].append(None)
    with pytest.raises(ValueError, match="candidates must be objects"):
        reconstruct_network(source)


def test_cancel_accepted_during_review_preparation_never_exposes_a_token(tmp_path, monkeypatch):
    workspace = NetworkWorkspace(tmp_path)
    preparing, release = threading.Event(), threading.Event()
    remember = workspace._remember

    def delayed_remember(network, result):
        response = remember(network, result)
        preparing.set()
        assert release.wait(10)
        return response

    monkeypatch.setattr(workspace, "_remember", delayed_remember)
    try:
        started = workspace.dispatch("jobs", {"network": example_network(), "max_evaluations": 1})
        assert preparing.wait(10), "The actual job did not reach review preparation"
        cancelled = workspace.dispatch("jobs/" + started["id"] + "/cancel", {})
        assert cancelled["status"] == "cancellation_requested"
        release.set()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            job = workspace.job(started["id"])
            if job["status"] not in ("queued", "running"):
                break
            time.sleep(.01)
        assert job["status"] == "cancelled", job
        assert job["response"] is None
        assert not workspace.reviews
    finally:
        release.set()
        workspace.close()


@pytest.mark.parametrize("owner", ["node", "span"])
@pytest.mark.parametrize("field", ["available", "cutback_allowed"])
def test_catalog_boolean_permissions_cannot_be_enabled_with_text(owner, field):
    source = example_network()
    record = source["nodes"][0] if owner == "node" else source["spans"][0]
    record["fitting_catalog"] = {field: "false"}
    with pytest.raises(ValueError, match="explicitly boolean"):
        normalize_network(source)


@pytest.mark.parametrize("settings", [
    {"strategy": []}, {"strategy": "unsupported"}, {"max_seconds": False},
    {"max_seconds": -1}, {"objective_weights": {"invented": 1}},
])
def test_invalid_embedded_search_settings_are_rejected_before_queuing(client, network, settings):
    network["search"] = settings
    response = client.post("/api/network/jobs", json={"network": network})
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("systems", [
    [{"id": "s", "type": "unrecognized"}],
    [{"id": "s", "type": "gravity", "system_type": "pressurized"}],
    [{"id": "s", "type": "gravity"}, {"id": "s", "type": "pressurized"}],
    {"s": 42},
])
def test_ambiguous_system_function_cannot_fall_back_to_pressure_routing(client, network, systems):
    network["systems"] = systems
    network["spans"][0].pop("system_type")
    response = client.post("/api/network/jobs", json={"network": network})
    assert response.status_code == 422, response.text


def test_null_catalog_stays_unverified_through_actual_display_and_export(client, network):
    network["spans"][0]["fitting_catalog"] = None
    inspected = client.post("/api/network/inspect", json={"network": network})
    assert inspected.status_code == 200, inspected.text
    assert inspected.json()["geometry"]["spans"][0]["catalog_status"] == "missing"
    calculated = client.post("/api/network/optimize", json={"network": network, "max_evaluations": 1})
    assert calculated.status_code == 200, calculated.text
    record = calculated.json()
    assert record["result"]["status"] == "NO IMPROVEMENT FOUND"
    assert record["result"]["validation"]["unverified"] > 0
    exported = client.post("/api/network/export", json={
        "network": record["network"], "review_token": record["review_token"], "approved": True,
    })
    assert exported.status_code == 200, exported.text
    assert len(exported.json()["exports"]) == 8
