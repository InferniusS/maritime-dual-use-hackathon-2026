"""Synthetic drawing handoff exercises the actual additive native HTTP protocol.

These tests execute DXF extraction and both shared APIs. They do not load Autodesk
assemblies or claim an AutoCAD runtime test.
"""
from copy import deepcopy
import json
from pathlib import Path
import threading
import time

import ezdxf
import pytest

from pipe_router.connected import network_digest
from test_autocad import payload_for
from test_cad_import import cylinder_sat, make_cylinder_dxf
from test_web import json_request
from run_web import make_server


def _wait_native_job(workspace, identifier):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        status, record = json_request(workspace, "GET", "/api/network/jobs/" + identifier)
        assert status == 200, record
        if record["status"] not in ("queued", "running"):
            return record
        time.sleep(.01)
    pytest.fail("Native protocol calculation did not finish within its bounded test duration")


@pytest.fixture
def native_workspace(tmp_path):
    server = make_server(port=0, output=tmp_path / "native-exports")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {"server": server, "output": tmp_path / "native-exports"}
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_native_multi_selection_preserves_revision_and_requires_semantic_confirmation(tmp_path, native_workspace):
    source = tmp_path / "synthetic-network.dxf"
    document = ezdxf.new("R2010")
    document.header["$INSUNITS"] = 4
    handles = []
    for origin in ((0, 0, 0), (0, 0, 10), (1000, 0, 0)):
        solid = document.modelspace().add_3dsolid()
        solid.sat = cylinder_sat(translation=origin)
        handles.append(solid.dxf.handle)
    document.saveas(source)
    original_bytes = source.read_bytes()
    selected = handles[:2]
    status, inspection = json_request(native_workspace, "POST", "/api/autocad/inspect", payload_for(source, selected))
    assert status == 200, inspection
    drawing = {"id": "synthetic-native-fingerprint", "revision": "pipeforge-session:test:4", "source_units": "mm"}
    request = {"cad_id": inspection["cad_id"], "selected_handles": selected, "drawing": drawing,
               "bounds": {"min": [-100, -100, -100], "max": [1200, 300, 300]},
               "coordinate_system": {"frame": "WCS"}, "tolerance_mm": 1}
    status, inspected = json_request(native_workspace, "POST", "/api/network/inspect", request)
    assert status == 200, inspected
    network = inspected["network"]
    assert network["drawing"] == drawing
    assert network["bounds"] == request["bounds"]
    assert len(network["spans"]) == 2
    assert {span["cad_handle"] for span in network["spans"]} == set(selected)
    assert handles[2] not in {span["cad_handle"] for span in network["spans"]}
    assert len(network["unresolved_connections"]) == 1
    assert len(network["nodes"]) == 4, "Geometric proximity must not silently merge semantic ports"
    before = deepcopy(network)
    connection = network["unresolved_connections"][0]
    status, confirmed = json_request(native_workspace, "POST", "/api/network/confirm", {
        "network": network, "decisions": [{"connection_id": connection["id"], "action": "connect"}],
    })
    assert status == 200, confirmed
    reviewed = confirmed["network"]
    assert len(reviewed["nodes"]) == 3
    assert not reviewed["unresolved_connections"]
    assert reviewed["drawing"] == drawing
    junction = next(node for node in reviewed["nodes"] if node["type"] == "junction")
    assert len(junction["source_endpoints"]) == 2
    assert {span["cad_handle"] for span in reviewed["spans"]} == set(selected)
    assert network == before, "Confirmation must not mutate the source handoff"
    status, validation = json_request(native_workspace, "POST", "/api/network/validate", {
        "network": reviewed, "reference": reviewed,
    })
    assert status == 200, validation
    assert validation["source_digest"] == network_digest(reviewed)
    assert validation["validation"]["unverified"] > 0, "Unknown CAD engineering metadata cannot be certified"
    assert source.read_bytes() == original_bytes


def test_native_handoff_needs_declared_volume_and_preserves_block_instance(tmp_path, native_workspace):
    source = make_cylinder_dxf(tmp_path / "synthetic-block.dxf", in_block=True)
    placement = next(iter(ezdxf.readfile(source).modelspace().query("INSERT")))
    status, inspection = json_request(native_workspace, "POST", "/api/autocad/inspect", payload_for(source, [placement.dxf.handle]))
    assert status == 200, inspection
    request = {"cad_id": inspection["cad_id"], "selected_handles": [placement.dxf.handle],
               "drawing": {"id": "block-fingerprint", "revision": "pipeforge-session:block:0", "source_units": "mm"},
               "coordinate_system": {"frame": "WCS"}}
    status, denied = json_request(native_workspace, "POST", "/api/network/inspect", request)
    assert status == 422
    assert "permitted ship bounds" in denied["error"]
    status, response = json_request(native_workspace, "POST", "/api/network/inspect", {
        **request, "bounds": {"min": [-1000, -1000, -1000], "max": [1000, 1000, 1000]},
    })
    assert status == 200, response
    span = response["network"]["spans"][0]
    assert span["instance_path"] == [f"{placement.dxf.handle}[0]"]
    assert span["points"][0] == pytest.approx([59.5, 220.25, 361])
    assert span["source_instances"][0]["instance_path"] == span["instance_path"]


def test_native_job_settings_keep_unselected_system_and_bind_approval_to_revision(native_workspace):
    """The native command's actual JSON contract, without loading Autodesk DLLs."""
    status, health = json_request(native_workspace, "GET", "/api/health")
    assert status == 200, health
    assert "network_optimization_v5" in health["capabilities"], "Native selection/settings must never run against an older service that ignores them"
    source = Path(__file__).resolve().parents[1] / "examples/connected/a-already-good.json"
    network = json.loads(source.read_text())
    network["drawing"] = {"id": "synthetic-native-drawing", "revision": "pipeforge-session:native:4", "source_units": "mm"}
    # A second complete, unselected system stays in the calculation and validation.
    network["systems"].append({"id": "retained", "type": "pressurized"})
    for node in deepcopy(network["nodes"]):
        node["id"] += "-retained"
        node["system_id"] = "retained"
        node["position"][1] += 2000
        network["nodes"].append(node)
    retained = deepcopy(network["spans"][0])
    retained.update(id="retained-pipe", from_node="pump-retained", to_node="cooler-retained", system_id="retained", cad_handle="B2")
    retained["points"] = [[p[0], p[1] + 2000, p[2]] for p in retained["points"]]
    network["spans"][0]["cad_handle"] = "A1"
    network["spans"].append(retained)
    original = deepcopy(network)
    status, job = json_request(native_workspace, "POST", "/api/network/jobs", {
        "network": network, "selected_span_ids": ["supply"], "strategy": "minimal_modifications",
        "max_evaluations": 1, "max_seconds": 10,
    })
    assert status == 200, job
    finished = _wait_native_job(native_workspace, job["id"])
    assert finished["status"] == "complete", finished
    response = finished["response"]
    assert {span["system_id"] for span in response["network"]["spans"]} == {"cooling", "retained"}
    result = response["result"]
    assert result["source_revision"] == network["drawing"]["revision"]
    assert result["source_digest"] == network_digest(network)
    effective = result.get("proposed_network") or result["original_network"]
    assert next(span for span in effective["spans"] if span["id"] == "retained-pipe")["points"] == retained["points"]
    review = {"network": network, "review_token": response["review_token"], "proposal_id": result["proposal_id"]}
    status, denied = json_request(native_workspace, "POST", "/api/network/export", review)
    assert status == 409, denied
    changed = deepcopy(network)
    changed["drawing"]["revision"] = "pipeforge-session:native:5"
    status, stale = json_request(native_workspace, "POST", "/api/network/export", {**review, "network": changed, "approved": True})
    assert status == 409, stale
    status, approved = json_request(native_workspace, "POST", "/api/network/export", {**review, "approved": True})
    assert status == 200, approved
    assert approved["native_applied"] is False
    expected = {"instructions.json", "instructions.md", "components.xlsx", "components.csv", "network-preview.dxf", "network-result.json", "network-comparison.svg", "coordinates.csv"}
    assert {url.rsplit("/", 1)[1] for url in approved["exports"].values()} == expected
    status, saved = json_request(native_workspace, "GET", next(url for url in approved["exports"].values() if url.endswith("network-result.json")))
    assert status == 200, saved
    assert saved["source_digest"] == result["source_digest"]
    assert saved["original_network"]["spans"][1]["cad_handle"] == "B2"
    assert network == original


def test_native_cancel_requests_engine_stop_and_never_produces_approval(native_workspace, monkeypatch):
    import pipe_router.connected as connected
    entered = threading.Event()

    def wait_for_cancellation(network, *, cancelled=None, **options):
        entered.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if cancelled and cancelled():
                # The service must suppress any review token after cancellation.
                return {"status": "CANCELLED"}
            time.sleep(.005)
        raise RuntimeError("Cooperative cancellation was never delivered")

    monkeypatch.setattr(connected, "optimize_network", wait_for_cancellation)
    source = Path(__file__).resolve().parents[1] / "examples/connected/a-already-good.json"
    network = json.loads(source.read_text())
    status, job = json_request(native_workspace, "POST", "/api/network/jobs", {"network": network, "selected_span_ids": ["supply"], "strategy": "balanced"})
    assert status == 200, job
    assert entered.wait(2)
    status, acknowledgement = json_request(native_workspace, "POST", f"/api/network/jobs/{job['id']}/cancel", {})
    assert status == 200, acknowledgement
    finished = _wait_native_job(native_workspace, job["id"])
    assert finished["status"] == "cancelled", finished
    assert finished["response"] is None
    assert not native_workspace["server"].RequestHandlerClass.keywords["workspace"].network.reviews
