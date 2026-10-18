"""AutoCAD snapshots use real DXF extraction and the existing routing workflow."""

import base64
import hashlib
import json
from pathlib import Path
import sys
import threading

import ezdxf
import pytest

from pipe_router.autocad import decode_dxf_upload, inspect_snapshot, validate_snapshot_request
from test_cad_import import cylinder_sat, make_cylinder_dxf
from test_web import json_request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from run_web import make_server


@pytest.fixture(scope="module")
def autocad_workspace(tmp_path_factory):
    temporary = tmp_path_factory.mktemp("autocad-api")
    server = make_server(port=0, output=temporary / "exports")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {"server": server, "output": temporary / "exports"}
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    assert not thread.is_alive()


def payload_for(source, selected):
    return {
        "filename": source.name,
        "content_base64": base64.b64encode(source.read_bytes()).decode("ascii"),
        "source_units": "mm",
        "selected_handles": selected,
        "document": {"name": "Engine room.dwg", "fingerprint": "fixture-document-id"},
    }


@pytest.fixture
def solid_snapshot(tmp_path):
    source = make_cylinder_dxf(tmp_path / "one-pipe.dxf")
    solid = next(iter(ezdxf.readfile(source).modelspace().query("3DSOLID")))
    return source, payload_for(source, [solid.dxf.handle])


def test_health_identifies_compatible_local_service(autocad_workspace):
    status, result = json_request(autocad_workspace, "GET", "/api/health")
    assert status == 200
    assert result == {
        "service": "maritime-pipe-router", "api_version": 1,
        "capabilities": ["cad_snapshot", "route_search", "pipe_envelopes", "challenge_rules_v3", "model_context", "review_excel", "network_optimization_v5"], "units": "mm",
    }


@pytest.mark.parametrize("units,scale", [("mm", 1), ("m", 1000), ("in", 25.4)])
def test_snapshot_returns_compact_geometry_choices(autocad_workspace, solid_snapshot, units, scale):
    source, payload = solid_snapshot
    payload["source_units"] = units
    status, result = json_request(autocad_workspace, "POST", "/api/autocad/inspect", payload)
    assert status == 200, result
    assert result["api_version"] == 1
    assert result["source_units"] == units
    assert result["units"] == "mm"
    assert result["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert len(result["cad_id"]) == 32
    assert len(result["candidates"]) == 1
    candidate = result["candidates"][0]
    assert candidate["kind"] == "run"
    assert candidate["member_count"] == 1
    assert candidate["source_handles"] == payload["selected_handles"]
    assert candidate["diameter_mm"] == pytest.approx(4 * scale)
    assert candidate["length_mm"] == pytest.approx(10 * scale)
    assert "objects" not in result and "source" not in result
    assert result["counts"]["matched_candidates"] == 1
    cached = json.loads((autocad_workspace["output"] / result["cad_id"] / "cad-inspection.json").read_text())
    assert len(cached["objects"]) == 3
    assert cached["autocad_context"]["document"] == payload["document"]
    assert "preview" not in cached


def test_selected_block_matches_actual_transformed_member(tmp_path, autocad_workspace):
    source = make_cylinder_dxf(tmp_path / "block-pipe.dxf", in_block=True)
    drawing = ezdxf.readfile(source)
    placement = next(iter(drawing.modelspace().query("INSERT")))
    status, imported = json_request(autocad_workspace, "POST", "/api/autocad/inspect", payload_for(source, [placement.dxf.handle]))
    assert status == 200, imported
    assert imported["candidates"][0]["length_mm"] == pytest.approx(20)
    assert imported["candidates"][0]["diameter_mm"] == pytest.approx(8)
    status, prepared = json_request(autocad_workspace, "POST", "/api/prepare-cad-layout", {
        "cad_id": imported["cad_id"], "candidate_id": imported["candidates"][0]["id"], "clearance": 0,
    })
    assert status == 200, prepared
    assert prepared["layout"]["pipe"]["start"] == pytest.approx([59.5, 220.25, 361])
    assert prepared["layout"]["cad_context"]["selected_handles"] == [placement.dxf.handle]


def test_real_dxf_chain_contacts_and_search_share_one_snapshot(tmp_path, autocad_workspace):
    source = tmp_path / "chain-and-connectors.dxf"
    drawing = ezdxf.new("R2010")
    drawing.header["$INSUNITS"] = 4
    handles = []
    for z, radius in ((0, 2), (10, 2), (-8, 3), (18, 3)):
        solid = drawing.modelspace().add_3dsolid()
        data = cylinder_sat(translation=(0, 0, z))
        if radius == 3:
            data = [line.replace("0 0 1 2 0 0 1 I I", "0 0 1 3 0 0 1 I I")
                    .replace("point $-1 -1 $-1 2 0", "point $-1 -1 $-1 3 0") for line in data]
        solid.sat = data
        handles.append(solid.dxf.handle)
    drawing.saveas(source)
    before = source.read_bytes()
    payload = payload_for(source, [handles[0]])
    status, imported = json_request(autocad_workspace, "POST", "/api/autocad/inspect", payload)
    assert status == 200, imported
    chain = imported["candidates"][0]
    assert chain["kind"] == "chain"
    assert chain["member_count"] == 2
    assert set(chain["source_handles"]) == set(handles[:2])
    assert chain["length_mm"] == pytest.approx(20)
    preparation = {"cad_id": imported["cad_id"], "candidate_id": chain["id"], "clearance": 0, "padding": 20}
    status, plain = json_request(autocad_workspace, "POST", "/api/prepare-cad-layout", preparation)
    assert status == 200, plain
    context = plain["layout"]["cad_context"]
    assert context["document"] == payload["document"]
    assert context["source_snapshot_sha256"] == imported["source_sha256"]
    assert context["selected_handles"] == [handles[0]]
    contacts = context["endpoint_contacts"]
    assert {item["endpoint"] for item in contacts} == {"start", "end"}
    status, blocked = json_request(autocad_workspace, "POST", "/api/run", {"layout": plain["layout"]})
    assert status == 200, blocked
    assert blocked["result"]["status"] == "no_route"
    connections = {side: [item["obstacle_id"] for item in contacts if item["endpoint"] == side]
                   for side in ("start", "end")}
    status, prepared = json_request(autocad_workspace, "POST", "/api/prepare-cad-layout", {
        **preparation, "endpoint_connections": connections, "max_stub_length": 8,
    })
    assert status == 200, prepared
    layout = prepared["layout"]
    assert len(layout["protected_endpoints"]) == 2
    assert layout["obstacles"] == plain["layout"]["obstacles"]
    status, checked = json_request(autocad_workspace, "POST", "/api/run", {"layout": layout})
    assert status == 200, checked
    assert checked["result"]["status"] == "unchanged"
    assert checked["result"]["candidate"]["valid"]
    assert checked["result"]["candidate"]["length"] == pytest.approx(20)
    assert not checked["result"]["checks"]["official_standards_compliance"]
    assert source.read_bytes() == before


@pytest.mark.parametrize("changed,message", [
    ({"source_units": "ft"}, "source units"),
    ({"source_units": None}, "source units"),
    ({"selected_handles": []}, "Select between"),
    ({"selected_handles": "2F"}, "Select between"),
    ({"selected_handles": ["not-a-handle"]}, "hexadecimal"),
    ({"selected_handles": ["0"]}, "hexadecimal"),
    ({"selected_handles": [3]}, "hexadecimal"),
    ({"selected_handles": ["A"] * 257}, "Select between"),
    ({"content_base64": "not-valid-base64!"}, "could not be read"),
    ({"content_base64": 42}, "could not be read"),
    ({"content_base64": ""}, "48 MB"),
    ({"filename": "pipe.dwg"}, "DXF file"),
    ({"filename": None}, "filename"),
    ({"document": "drawing"}, "object"),
    ({"document": {"fingerprint": 12}}, "fingerprint"),
])
def test_bad_snapshot_input_fails_before_import(autocad_workspace, solid_snapshot, changed, message):
    _, payload = solid_snapshot
    status, result = json_request(autocad_workspace, "POST", "/api/autocad/inspect", {**payload, **changed})
    assert status == 400
    assert message in result["error"]


def test_unknown_selection_does_not_fall_back_to_unselected_pipe(autocad_workspace, solid_snapshot):
    _, payload = solid_snapshot
    payload["selected_handles"] = ["FFFFFF"]
    status, result = json_request(autocad_workspace, "POST", "/api/autocad/inspect", payload)
    assert status == 400
    assert "No pipe route" in result["error"]


def test_plain_line_is_not_relabelled_as_a_solid_pipe(tmp_path, autocad_workspace):
    source = tmp_path / "line-only.dxf"
    drawing = ezdxf.new("R2010")
    line = drawing.modelspace().add_line((0, 0, 0), (10, 0, 0))
    drawing.saveas(source)
    status, result = json_request(autocad_workspace, "POST", "/api/autocad/inspect", payload_for(source, [line.dxf.handle]))
    assert status == 400
    assert "No pipe route" in result["error"]


def test_oversized_matching_selection_requires_a_narrower_pick(tmp_path):
    source = tmp_path / "many-pipes.dxf"
    drawing = ezdxf.new("R2010")
    block = drawing.blocks.new("PIPES")
    for x in range(51):
        solid = block.add_3dsolid()
        solid.sat = cylinder_sat(translation=(x * 20, 0, 0))
    placement = drawing.modelspace().add_blockref("PIPES", (0, 0, 0))
    drawing.saveas(source)
    with pytest.raises(ValueError, match="51 possible routes"):
        inspect_snapshot(source, source_units="mm", selected_handles=[placement.dxf.handle], document={})


def test_upload_filename_and_handle_normalization_are_bounded(solid_snapshot):
    _, payload = solid_snapshot
    payload["filename"] = r"C:\private\..\snapshot.dxf"
    payload["selected_handles"] = ["0000af", "AF"]
    clean = validate_snapshot_request(payload)
    assert clean["filename"] == "snapshot.dxf"
    assert clean["selected_handles"] == ["AF"]
    assert "content" in clean


def test_upload_limit_is_shared_with_browser(monkeypatch, solid_snapshot):
    _, payload = solid_snapshot
    monkeypatch.setattr("pipe_router.autocad.MAX_CAD_BYTES", 3)
    with pytest.raises(ValueError, match="48 MB"):
        decode_dxf_upload(payload)
