"""Exercise the local workspace over HTTP and verify exported geometry."""

import base64
import hashlib
from http.client import HTTPConnection
from io import StringIO
import json
from pathlib import Path
import sys
import threading

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from run_web import MAX_REQUEST_BYTES, RUN_LOCK, make_server


@pytest.fixture(scope="module")
def workspace(tmp_path_factory):
    temporary = tmp_path_factory.mktemp("http-workspace")
    sample = temporary / "known-line.dxf"
    drawing = ezdxf.new("R2010")
    drawing.header["$INSUNITS"] = 0
    drawing.modelspace().add_line((1, 2, 3), (4, 5, 6))
    drawing.saveas(sample)
    source_hash = hashlib.sha256(sample.read_bytes()).hexdigest()
    server = make_server(port=0, output=temporary / "exports", cad_sample=sample)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {"server": server, "sample": sample, "output": temporary / "exports"}
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert hashlib.sha256(sample.read_bytes()).hexdigest() == source_hash


def request(workspace, method, path, payload=None, *, raw=None, headers=None):
    connection = HTTPConnection("127.0.0.1", workspace["server"].server_port, timeout=30)
    request_headers = {"Content-Type": "application/json"} if method == "POST" else {}
    request_headers.update(headers or {})
    body = json.dumps(payload).encode("utf-8") if raw is None and method == "POST" else raw
    try:
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        data = response.read()
        return response.status, dict(response.getheaders()), data
    finally:
        connection.close()


def json_request(workspace, method, path, payload=None, **kwargs):
    status, headers, data = request(workspace, method, path, payload, **kwargs)
    assert headers["Content-Type"].startswith("application/json")
    return status, json.loads(data)


@pytest.fixture
def layout():
    return {
        "name": "HTTP integration layout", "provenance": "synthetic",
        "units": "mm", "system_type": "pressurized",
        "bounds": {"min": [0, 0, 0], "max": [100, 100, 100]}, "obstacles": [],
        "pipe": {
            "start": [10, 10, 50], "end": [90, 10, 50], "diameter": 4,
            "original_route": [[10, 10, 50], [10, 90, 50], [90, 90, 50], [90, 10, 50]],
        },
        "rules": {"clearance": 3, "grid_step": 10, "bend_penalty": 5},
        "assumptions": ["Invented HTTP test geometry"],
    }


def test_config_exposes_scenarios_and_provisional_profiles(workspace):
    status, config = json_request(workspace, "GET", "/api/config")
    assert status == 200
    assert config["cad_sample_available"]
    assert config["cad_sample_name"] == "known-line.dxf"
    assert {"equipment-detour", "gravity-drain"} <= {item["id"] for item in config["scenarios"]}
    profiles = {item["id"]: item for item in config["rule_profiles"]["profiles"]}
    assert profiles["prototype-gravity"]["rules"]["min_slope"] == 0.01
    assert profiles["prototype-pressure"]["status"] == "provisional"
    assert not any("path" in item for item in config["scenarios"])


@pytest.mark.parametrize("identifier,system", [("equipment-detour", "pressurized"), ("gravity-drain", "gravity")])
def test_layout_endpoint_loads_selected_geometry(workspace, identifier, system):
    status, loaded = json_request(workspace, "GET", f"/api/layout?id={identifier}")
    assert status == 200
    assert loaded["system_type"] == system
    assert loaded["provenance"] == "synthetic"
    assert loaded["pipe"]["original_route"][0] == loaded["pipe"]["start"]


def test_pressure_run_exports_the_actual_result(workspace, layout):
    status, record = json_request(workspace, "POST", "/api/run", {"layout": layout})
    assert status == 200, record
    result = record["result"]
    assert result["status"] == "improved"
    assert result["candidate"]["points"] == [[10, 10, 50], [90, 10, 50]]
    assert result["candidate"]["length"] == 80
    assert not result["checks"]["official_standards_compliance"]
    status, exported = json_request(workspace, "GET", record["exports"]["json"])
    assert status == 200
    assert exported["input_layout"] == layout
    assert exported["result"] == result
    status, headers, dxf_bytes = request(workspace, "GET", record["exports"]["dxf"])
    assert status == 200
    document = ezdxf.read(StringIO(dxf_bytes.decode("utf-8")))
    assert document.units == ezdxf.units.MM
    assert not document.audit().errors
    polyline_points = [[list(point) for point in entity.points()] for entity in document.modelspace().query("POLYLINE")]
    assert result["candidate"]["points"] in polyline_points
    assert result["original"]["points"] in polyline_points
    status, headers, html = request(workspace, "GET", record["exports"]["report"])
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert b"plotly.js" in html
    assert b"HTTP integration layout" in html


def test_gravity_run_preserves_downhill_flow_and_exports_slopes(workspace, layout):
    layout["system_type"] = "gravity"
    layout["pipe"]["start"] = [10, 10, 70]
    layout["pipe"]["end"] = [90, 10, 30]
    layout["pipe"]["original_route"] = [[10, 10, 70], [90, 10, 30]]
    layout["rules"]["min_slope"] = 0.1
    layout["obstacles"] = [{"id": "equipment", "min": [40, 5, 40], "max": [60, 20, 60]}]
    status, record = json_request(workspace, "POST", "/api/run", {"layout": layout})
    assert status == 200, record
    result = record["result"]
    assert not result["original"]["valid"]
    assert result["candidate"]["valid"]
    assert result["candidate"]["minimum_segment_slope"] >= 0.1
    assert result["checks"]["gravity_slope"]
    points = result["candidate"]["points"]
    assert points[0] == [10, 10, 70] and points[-1] == [90, 10, 30]
    assert all(a[2] >= b[2] for a, b in zip(points, points[1:]))
    status, headers, dxf_bytes = request(workspace, "GET", record["exports"]["dxf"])
    assert status == 200
    document = ezdxf.read(StringIO(dxf_bytes.decode("utf-8")))
    assert points in [[list(point) for point in entity.points()] for entity in document.modelspace().query("POLYLINE")]


@pytest.mark.parametrize("units,scale", [("mm", 1), ("in", 25.4), ("m", 1000)])
def test_run_converts_input_units_and_export_coordinates_together(workspace, layout, units, scale):
    layout["units"] = units
    status, record = json_request(workspace, "POST", "/api/run", {"layout": layout})
    assert status == 200, record
    assert record["result"]["input_units"] == units
    assert record["layout"]["units"] == "mm"
    assert record["result"]["candidate"]["length"] == pytest.approx(80 * scale)
    assert record["layout"]["pipe"]["diameter"] == pytest.approx(4 * scale)
    assert record["layout"]["rules"]["clearance"] == pytest.approx(3 * scale)
    status, exported = json_request(workspace, "GET", record["exports"]["json"])
    assert status == 200
    assert exported["input_layout"]["units"] == units
    assert exported["layout"]["pipe"]["start"] == pytest.approx([10 * scale, 10 * scale, 50 * scale])


def test_cad_sample_and_uploaded_copy_keep_actual_coordinates(workspace):
    status, sample = json_request(workspace, "POST", "/api/cad-sample", {"source_units": "in"})
    assert status == 200, sample
    assert len(sample["cad_id"]) == 32
    assert sample["export"] == f'/downloads/{sample["cad_id"]}/cad-inspection.json'
    assert sample["cad"]["objects"][0]["geometry"]["points"][0] == pytest.approx([25.4, 50.8, 76.2])
    assert not sample["cad"]["pipe_graph_ready"]
    status, exported = json_request(workspace, "GET", sample["export"])
    assert status == 200
    assert exported == sample["cad"]
    content = base64.b64encode(workspace["sample"].read_bytes()).decode("ascii")
    status, uploaded = json_request(workspace, "POST", "/api/import-cad", {
        "source_units": "mm", "filename": "upload.dxf", "content_base64": content,
    })
    assert status == 200, uploaded
    assert uploaded["cad_id"] != sample["cad_id"]
    assert uploaded["cad"]["objects"][0]["geometry"]["points"] == [[1, 2, 3], [4, 5, 6]]
    assert uploaded["cad"]["route_candidates"] == []


@pytest.fixture(scope="module")
def candidate_import(workspace, tmp_path_factory):
    # Reuse the independent analytic geometry fixture from importer tests.
    from test_cad_import import make_cylinder_dxf

    source = make_cylinder_dxf(tmp_path_factory.mktemp("http-cylinder") / "cylinder.dxf")
    status, imported = json_request(workspace, "POST", "/api/import-cad", {
        "source_units": "mm", "filename": source.name,
        "content_base64": base64.b64encode(source.read_bytes()).decode("ascii"),
    })
    assert status == 200, imported
    assert len(imported["cad"]["route_candidates"]) == 1
    return imported


@pytest.mark.parametrize("system_type,reverse_flow", [("pressurized", False), ("gravity", True)])
def test_imported_candidate_can_be_prepared_and_run_with_source_trace(workspace, candidate_import, system_type, reverse_flow):
    candidate = candidate_import["cad"]["route_candidates"][0]
    identifier = candidate_import["cad_id"]
    before = (workspace["output"] / identifier / "cad-inspection.json").read_bytes()
    status, prepared = json_request(workspace, "POST", "/api/prepare-cad-layout", {
        "cad_id": identifier, "candidate_id": candidate["id"],
        "system_type": system_type, "clearance": 50, "padding": 500,
        "reverse_flow": reverse_flow,
    })
    assert status == 200, prepared
    assert set(prepared) == {"layout", "cad_id"}
    assert prepared["cad_id"] == identifier
    layout = prepared["layout"]
    assert layout["provenance"] == "cad-derived"
    assert layout["system_type"] == system_type
    assert layout["units"] == "mm"
    assert layout["cad_context"]["cad_id"] == identifier
    assert layout["pipe"]["diameter"] == pytest.approx(4)
    expected_start = [10.125, 20.25, 40.5 if reverse_flow else 30.5]
    expected_end = [10.125, 20.25, 30.5 if reverse_flow else 40.5]
    assert layout["pipe"]["start"] == pytest.approx(expected_start)
    assert layout["pipe"]["end"] == pytest.approx(expected_end)
    assert layout["rules"]["clearance"] == 50
    assert (workspace["output"] / identifier / "cad-inspection.json").read_bytes() == before

    status, run = json_request(workspace, "POST", "/api/run", {"layout": layout})
    assert status == 200, run
    assert run["result"]["candidate"]["valid"]
    assert run["result"]["provenance"] == "cad-derived"
    assert not run["result"]["checks"]["official_standards_compliance"]
    assert not run["result"]["checks"]["complete_ship_geometry"]
    status, exported = json_request(workspace, "GET", run["exports"]["json"])
    assert status == 200
    assert exported["input_layout"]["cad_context"]["cad_id"] == identifier
    assert exported["layout"]["pipe"]["start"] == pytest.approx(expected_start)


@pytest.mark.parametrize("identifier", [None, "", "../source", "/tmp/source", "a" * 32 + "/../", 1, []])
def test_prepare_rejects_invalid_cad_identifiers(workspace, identifier):
    status, record = json_request(workspace, "POST", "/api/prepare-cad-layout", {"cad_id": identifier, "candidate_id": "candidate"})
    assert status == 400
    assert "workspace" in record["error"]


def test_prepare_requires_an_existing_snapshot(workspace):
    status, record = json_request(workspace, "POST", "/api/prepare-cad-layout", {"cad_id": "0" * 32, "candidate_id": "candidate"})
    assert status == 400
    assert "not found" in record["error"]


@pytest.mark.parametrize("change", [
    {"candidate_id": "missing-candidate"}, {"candidate_id": []},
    {"system_type": "unknown"}, {"reverse_flow": "false"},
    {"clearance": -1}, {"clearance": float("nan")}, {"padding": -1},
])
def test_prepare_rejects_unknown_candidate_and_invalid_controls(workspace, candidate_import, change):
    payload = {
        "cad_id": candidate_import["cad_id"],
        "candidate_id": candidate_import["cad"]["route_candidates"][0]["id"],
        **change,
    }
    status, record = json_request(workspace, "POST", "/api/prepare-cad-layout", payload)
    assert status == 400
    assert record["error"]


@pytest.mark.parametrize("payload", [None, [], "layout", {}, {"layout": None}, {"layout": {}}])
def test_invalid_run_payload_returns_json_error(workspace, payload):
    status, record = json_request(workspace, "POST", "/api/run", payload)
    assert status == 400
    assert isinstance(record["error"], str) and record["error"]


@pytest.mark.parametrize("raw", [b"{", b"\xff", b'{"layout": NaN}'])
def test_malformed_body_is_rejected_without_breaking_server(workspace, raw):
    status, record = json_request(workspace, "POST", "/api/run", raw=raw)
    assert status == 400
    assert record["error"]
    assert json_request(workspace, "GET", "/api/config")[0] == 200


@pytest.mark.parametrize("field,value", [("units", "feet"), ("system_type", "mystery")])
def test_unrecognized_layout_semantics_are_not_silently_assumed(workspace, layout, field, value):
    layout[field] = value
    status, record = json_request(workspace, "POST", "/api/run", {"layout": layout})
    assert status == 400
    assert field in record["error"]


@pytest.mark.parametrize("origin", ["http://example.invalid", "null"])
def test_cross_origin_post_is_rejected_before_running(workspace, layout, origin):
    status, record = json_request(workspace, "POST", "/api/run", {"layout": layout}, headers={"Origin": origin})
    assert status == 403
    assert "local workspace" in record["error"]


def test_same_origin_post_is_accepted(workspace, layout):
    origin = f'http://127.0.0.1:{workspace["server"].server_port}'
    status, record = json_request(workspace, "POST", "/api/run", {"layout": layout}, headers={"Origin": origin})
    assert status == 200, record


def test_non_json_content_type_is_rejected(workspace):
    status, record = json_request(workspace, "POST", "/api/run", {}, headers={"Content-Type": "text/plain"})
    assert status == 415
    assert record["error"]


@pytest.mark.parametrize("length,status_expected", [("0", 413), (str(MAX_REQUEST_BYTES + 1), 413), ("bad", 400)])
def test_request_size_headers_are_checked_before_read(workspace, length, status_expected):
    status, record = json_request(workspace, "POST", "/api/run", raw=b"", headers={"Content-Length": length})
    assert status == status_expected
    assert record["error"]


def test_busy_calculation_returns_retry_response(workspace, layout):
    with RUN_LOCK:
        status, record = json_request(workspace, "POST", "/api/run", {"layout": layout})
    assert status == 409
    assert "Another calculation" in record["error"]


@pytest.mark.parametrize("payload", [
    {"filename": "drawing.dwg", "content_base64": "AA=="},
    {"filename": "drawing.dxf", "content_base64": "!not base64!"},
    {"filename": "drawing.dxf", "content_base64": ""},
    {"filename": "drawing.dxf", "content_base64": "AA==", "source_units": "feet"},
])
def test_bad_cad_upload_is_rejected(workspace, payload):
    status, record = json_request(workspace, "POST", "/api/import-cad", payload)
    assert status == 400
    assert record["error"]


@pytest.mark.parametrize("path", ["/api/layout?id=missing", "/downloads/../../README.md", "/downloads/" + "a" * 32 + "/result.json"])
def test_unknown_layouts_and_exports_return_not_found(workspace, path):
    status, record = json_request(workspace, "GET", path)
    assert status == 404
    assert record["error"]


@pytest.fixture(scope="module")
def chain_import(workspace, tmp_path_factory):
    from test_cad_import import cylinder_sat

    source = tmp_path_factory.mktemp("http-chain") / "two-cylinders.dxf"
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    for z in (0, 10):
        solid = doc.modelspace().add_3dsolid()
        solid.sat = cylinder_sat(translation=(0, 0, z))
    doc.saveas(source)
    before = source.read_bytes()
    status, record = json_request(workspace, "POST", "/api/import-cad", {
        "source_units": "mm", "filename": source.name,
        "content_base64": base64.b64encode(before).decode("ascii"),
    })
    assert status == 200, record
    assert len(record["cad"]["route_candidates"]) == 2
    yield record
    assert source.read_bytes() == before


def test_network_endpoint_keeps_unjoined_fixture_as_unverified(workspace, candidate_import):
    status, network = json_request(workspace, "POST", "/api/cad-network", {"cad_id": candidate_import["cad_id"]})
    assert status == 200, network
    assert network["chains"] == []
    assert network["counts"]["accepted_joins"] == 0


def test_imported_chain_control_detour_and_export_are_traceable(workspace, chain_import):
    identifier = chain_import["cad_id"]
    source = workspace["output"] / identifier / "cad-inspection.json"
    before = source.read_bytes()
    status, network = json_request(workspace, "POST", "/api/cad-network", {"cad_id": identifier})
    assert status == 200, network
    assert len(network["chains"]) == 1
    chain = network["chains"][0]
    assert chain["points"] == [[0, 0, 0], [0, 0, 10], [0, 0, 20]]
    assert chain["requires_review"]
    assert not chain["connectivity_verified"]
    status, prepared = json_request(workspace, "POST", "/api/prepare-cad-layout", {
        "cad_id": identifier, "candidate_id": chain["id"], "clearance": 0, "padding": 500,
    })
    assert status == 200, prepared
    baseline = prepared["layout"]
    assert baseline["pipe"]["original_route"] == chain["points"]
    status, control = json_request(workspace, "POST", "/api/run", {"layout": baseline})
    assert status == 200, control
    assert control["result"]["status"] == "unchanged"
    assert control["result"]["candidate"]["length"] == pytest.approx(20)

    status, generated = json_request(workspace, "POST", "/api/make-detour", {"layout": baseline})
    assert status == 200, generated
    changed = generated["layout"]
    assert changed["benchmark"]["synthetic_mutation"]
    assert changed["benchmark"]["baseline_layout"]["pipe"] == baseline["pipe"]
    assert changed["cad_context"]["cad_id"] == identifier
    assert changed["benchmark"]["mutation"]["added_length_mm"] == pytest.approx(50)
    status, improved = json_request(workspace, "POST", "/api/run", {"layout": changed})
    assert status == 200, improved
    assert improved["result"]["status"] == "improved"
    assert improved["result"]["original"]["length"] == pytest.approx(70)
    assert improved["result"]["candidate"]["length"] == pytest.approx(20)
    assert improved["result"]["candidate"]["valid"]
    status, exported = json_request(workspace, "GET", improved["exports"]["json"])
    assert status == 200
    assert exported["input_layout"]["benchmark"] == changed["benchmark"]
    assert exported["result"]["normalized_layout"]["benchmark"] == changed["benchmark"]
    assert source.read_bytes() == before
    status, repeated = json_request(workspace, "POST", "/api/make-detour", {"layout": changed})
    assert status == 400
    assert "already" in repeated["error"]


@pytest.fixture(scope="module")
def connected_snapshot(workspace):
    # A complete saved inspection fixture with explicit source boxes. This
    # exercises preparation and contact assumptions without a private CAD file.
    from test_cad_layout import solid
    identifier = "f" * 32
    folder = workspace["output"] / identifier
    folder.mkdir()
    record = {
        "name": "Connector fixture", "provenance": "cad", "units": "mm",
        "source_units": "mm", "unit_assumption": False, "source_sha256": "fixture",
        "source": "fixture.dxf", "warnings": [], "unsupported_geometry": {},
        "counts": {"solid_instances": 3},
        "objects": [solid("PIPE", [100, 490, 490], [900, 510, 510]),
                    solid("START", [80, 480, 480], [150, 520, 520]),
                    solid("END", [850, 480, 480], [920, 520, 520])],
        "route_candidates": [{"id": "pipe-run", "points": [[100, 500, 500], [900, 500, 500]],
                              "geometric_diameter": 20, "source_handles": ["PIPE"],
                              "instance_path": [], "source_records": [1], "requires_review": True}],
    }
    target = folder / "cad-inspection.json"
    target.write_text(json.dumps(record))
    before = target.read_bytes()
    status, plain = json_request(workspace, "POST", "/api/prepare-cad-layout", {
        "cad_id": identifier, "candidate_id": "pipe-run", "clearance": 0,
    })
    assert status == 200, plain
    by_handle = {item["source_handle"]: item["id"] for item in plain["layout"]["cad_context"]["obstacle_sources"]}
    yield {"cad_id": identifier, "candidate_id": "pipe-run", "clearance": 0,
           "endpoint_connections": {"start": [by_handle["START"]], "end": [by_handle["END"]]}}
    assert target.read_bytes() == before


def test_endpoint_connection_assumptions_enable_only_fixed_stubs(workspace, connected_snapshot):
    plain_payload = {key: value for key, value in connected_snapshot.items() if key != "endpoint_connections"}
    status, plain = json_request(workspace, "POST", "/api/prepare-cad-layout", plain_payload)
    assert status == 200
    status, blocked = json_request(workspace, "POST", "/api/run", {"layout": plain["layout"]})
    assert status == 200
    assert blocked["result"]["status"] == "no_route"
    status, prepared = json_request(workspace, "POST", "/api/prepare-cad-layout", {
        **connected_snapshot, "max_stub_length": 100,
    })
    assert status == 200, prepared
    original = prepared["layout"]
    assert len(original["protected_endpoints"]) == 2
    assert len(original["obstacles"]) == 2
    assert original["obstacles"] == plain["layout"]["obstacles"]
    status, control = json_request(workspace, "POST", "/api/run", {"layout": original})
    assert status == 200, control
    assert control["result"]["status"] == "unchanged"
    status, detour = json_request(workspace, "POST", "/api/make-detour", {"layout": original})
    assert status == 200, detour
    assert detour["layout"]["protected_endpoints"] == original["protected_endpoints"]
    status, result = json_request(workspace, "POST", "/api/run", {"layout": detour["layout"]})
    assert status == 200, result
    assert result["result"]["status"] == "improved"
    points = result["result"]["candidate"]["points"]
    for stub in original["protected_endpoints"]:
        count = len(stub["points"])
        assert (points[:count] if stub["endpoint"] == "start" else points[-count:]) == stub["points"]


@pytest.mark.parametrize("change", [
    {"endpoint_connections": {"start": ["missing"]}},
    {"endpoint_connections": {"middle": []}},
    {"endpoint_connections": {"start": "cad-solid-2"}},
    {"endpoint_connections": ["start"]},
    {"endpoint_connections": []}, {"endpoint_connections": False},
    {"endpoint_connections": ""}, {"endpoint_connections": {"start": [1]}},
    {"max_stub_length": 0}, {"max_stub_length": -1},
    {"max_stub_length": 1001}, {"max_stub_length": float("nan")},
    {"max_stub_length": 20},
])
def test_endpoint_connection_options_reject_invalid_or_excessive_waivers(workspace, connected_snapshot, change):
    status, record = json_request(workspace, "POST", "/api/prepare-cad-layout", {**connected_snapshot, **change})
    assert status == 400
    assert record["error"]


@pytest.mark.parametrize("maximum", [0, 1001, float("nan")])
def test_stub_length_limit_is_validated_even_without_contacts(workspace, candidate_import, maximum):
    status, record = json_request(workspace, "POST", "/api/prepare-cad-layout", {
        "cad_id": candidate_import["cad_id"],
        "candidate_id": candidate_import["cad"]["route_candidates"][0]["id"],
        "max_stub_length": maximum,
    })
    assert status == 400
    assert "max_stub_length" in record["error"]


@pytest.mark.parametrize("identifier", [None, "../source", "0" * 32])
def test_network_endpoint_rejects_missing_or_invalid_snapshot(workspace, identifier):
    status, record = json_request(workspace, "POST", "/api/cad-network", {"cad_id": identifier})
    assert status == 400
    assert record["error"]


@pytest.mark.parametrize("payload", [{}, {"layout": None}, {"layout": []}])
def test_detour_endpoint_requires_a_layout(workspace, payload):
    status, record = json_request(workspace, "POST", "/api/make-detour", payload)
    assert status == 400
    assert record["error"]


def test_detour_endpoint_rejects_a_blocked_original_instead_of_cleaning_it(workspace, layout):
    layout["provenance"] = "cad-derived"
    layout["obstacles"] = [{"id": "wall", "min": [40, 0, 0], "max": [60, 100, 100]}]
    status, record = json_request(workspace, "POST", "/api/make-detour", {"layout": layout})
    assert status == 400
    assert "baseline must pass" in record["error"]


def test_detour_endpoint_honors_the_requested_offset(workspace, candidate_import):
    status, prepared = json_request(workspace, "POST", "/api/prepare-cad-layout", {
        "cad_id": candidate_import["cad_id"],
        "candidate_id": candidate_import["cad"]["route_candidates"][0]["id"],
        "clearance": 0, "padding": 500,
    })
    assert status == 200, prepared
    status, generated = json_request(workspace, "POST", "/api/make-detour", {
        "layout": prepared["layout"], "offset_mm": 250,
    })
    assert status == 200, generated
    benchmark = generated["layout"]["benchmark"]
    assert benchmark["mutation"]["offset_mm"] == 250
    assert benchmark["mutation"]["added_length_mm"] == pytest.approx(500)


@pytest.mark.parametrize("offset", [0, -1, 1001, float("nan"), float("inf"), True, "250", []])
def test_detour_endpoint_rejects_invalid_offset(workspace, candidate_import, offset):
    status, prepared = json_request(workspace, "POST", "/api/prepare-cad-layout", {
        "cad_id": candidate_import["cad_id"],
        "candidate_id": candidate_import["cad"]["route_candidates"][0]["id"],
        "clearance": 0, "padding": 500,
    })
    assert status == 200, prepared
    status, record = json_request(workspace, "POST", "/api/make-detour", {
        "layout": prepared["layout"], "offset_mm": offset,
    })
    assert status == 400
    assert "offset_mm" in record["error"]
