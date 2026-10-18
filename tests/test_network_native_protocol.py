"""Synthetic drawing handoff exercises the actual additive native HTTP protocol.

These tests execute DXF extraction and both shared APIs. They do not load Autodesk
assemblies or claim an AutoCAD runtime test.
"""
from copy import deepcopy
import threading

import ezdxf
import pytest

from pipe_router.connected import network_digest
from test_autocad import payload_for
from test_cad_import import cylinder_sat, make_cylinder_dxf
from test_web import json_request
from run_web import make_server


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
