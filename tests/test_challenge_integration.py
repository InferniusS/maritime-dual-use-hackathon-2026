"""Exercise the V3 profile through routing, context, and HTTP exports."""
from copy import deepcopy
from http.client import HTTPConnection
from io import BytesIO
import json
import math
from pathlib import Path
import sys
import threading
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest

from pipe_router import core
from pipe_router.model_context import apply_model_context
from pipe_router.workflow import run_workflow, validate_layout_route, with_endpoint_connections

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "challenge"
sys.path.insert(0, str(ROOT / "scripts"))
from run_web import make_server


def load_case(name):
    return json.loads((EXAMPLES / f"{name}.json").read_text())


def checks(assessment):
    return {finding["id"]: finding for finding in assessment["findings"]}


def test_v3_enforces_exact_two_inch_floor_without_changing_prototype_profile():
    value = load_case("valid-straight")
    value["rules"]["clearance"] = 50
    with pytest.raises(ValueError, match="50.8"):
        run_workflow(value)
    value["rule_profile"] = "prototype-unverified"
    assert run_workflow(value)["candidate"] is not None
    value["rule_profile"] = "fmg-v3"
    value["rules"]["clearance"] = 50.8
    result = run_workflow(value)
    assert result["candidate"] is not None
    assert result["normalized_layout"]["rules"]["clearance"] == 50.8


def scale_input(value, units):
    """Convert geometric input only. Engineering observations remain explicit mm."""
    value = deepcopy(value)
    scale = {"m": 1000, "in": 25.4}[units]
    value["units"] = units
    for name in ("start", "end"):
        value["pipe"][name] = [coordinate / scale for coordinate in value["pipe"][name]]
    value["pipe"]["original_route"] = [[coordinate / scale for coordinate in point] for point in value["pipe"]["original_route"]]
    value["pipe"]["diameter"] /= scale
    for name in ("min", "max"):
        value["bounds"][name] = [coordinate / scale for coordinate in value["bounds"][name]]
    for obstacle in value["obstacles"]:
        for name in ("min", "max"):
            obstacle[name] = [coordinate / scale for coordinate in obstacle[name]]
    for name in ("clearance", "grid_step", "bend_penalty"):
        if name in value["rules"]:
            value["rules"][name] /= scale
    return value


@pytest.mark.parametrize("units", ["in", "m"])
def test_default_v3_clearance_is_mm_and_engineering_metadata_is_not_rescaled(units):
    value = scale_input(load_case("valid-straight"), units)
    del value["rules"]["clearance"]
    before = deepcopy(value)
    result = run_workflow(value)
    assert value == before
    assert result["input_units"] == units
    assert result["units"] == "mm"
    assert result["normalized_layout"]["rules"]["clearance"] == pytest.approx(50.8)
    assert result["original"]["length"] == pytest.approx(2000)
    assert result["normalized_layout"]["engineering"] == value["engineering"]
    assert checks(result["challenge_review"]["original"])["piece_length"]["status"] == "pass"


def test_changed_candidate_does_not_inherit_source_fabrication_approval():
    value = load_case("added-detour")
    value["engineering"] = {
        "units": "mm", "route_points": deepcopy(value["pipe"]["original_route"]),
        "nominal_diameter_mm": 40, "nominal_size_basis_verified": True,
        "inventory_complete": {"pieces": True},
        "pieces": [{"id": "original-piece", "source_refs": ["reviewed original geometry"],
                    "centerline_length_mm": 4000, "bends": [{"radius_mm": 152.4, "angle_deg": 90}, {"radius_mm": 152.4, "angle_deg": 90}],
                    "straight_between_bends_mm": [2000], "weld_spacings_mm": []}],
    }
    result = run_workflow(value)
    assert result["status"] == "improved"
    assert result["candidate"]["length"] == pytest.approx(2000)
    baseline = checks(result["challenge_review"]["original"])
    candidate = checks(result["challenge_review"]["candidate"])
    assert baseline["bend_radius"]["status"] == "pass"
    assert baseline["bends_per_piece"]["status"] == "pass"
    assert candidate["bend_radius"]["status"] == "needs_review"
    assert candidate["piece_length"]["status"] == "needs_review"
    assert result["challenge_review"]["complete_compliance_verified"] is False


@pytest.mark.parametrize("name,rule", [("undersized-bend", "bend_radius"), ("welds-too-close", "weld_spacing")])
def test_unchanged_geometry_with_known_manufacturing_failure_is_not_saveable(name, rule):
    result = run_workflow(load_case(name))
    assert result["original"]["valid"]  # Geometric validity cannot erase manufacturing failure.
    assert result["status"] == "no_route"
    assert result["candidate"] is None
    assert checks(result["challenge_review"]["original"])[rule]["status"] == "fail"
    assert rule in result["challenge_review"]["rejected_candidate"]["hard_failures"]


def test_reversed_flow_is_refused_instead_of_searched_in_wrong_direction():
    value = load_case("fore-aft-correct-fall")
    value["engineering"]["flow_direction"] = "end_to_start"
    with pytest.raises(ValueError, match="reverse the route points"):
        run_workflow(value)


def test_mixed_direction_gravity_search_allocates_different_fall_per_axis():
    value = load_case("fore-aft-correct-fall")
    value["pipe"]["end"] = [1000, 1000, 950]
    value["pipe"]["original_route"] = [[0, 0, 1000], [0, 1200, 1000], [1000, 1200, 980], [1000, 1000, 950]]
    result = run_workflow(value)
    assert result["status"] == "improved"
    assert result["candidate"] is not None
    observed = {}
    for start, end in zip(result["candidate"]["points"], result["candidate"]["points"][1:]):
        dx, dy = abs(end[0] - start[0]), abs(end[1] - start[1])
        assert not (dx > 1e-6 and dy > 1e-6)
        if dx > 1e-6:
            observed["longitudinal"] = (start[2] - end[2]) / dx
        if dy > 1e-6:
            observed["transverse"] = (start[2] - end[2]) / dy
    assert observed == pytest.approx({"longitudinal": 0.04, "transverse": 0.01})
    assert checks(result["challenge_review"]["candidate"])["directional_drain_pitch"]["status"] == "pass"
    assert math.dist(result["candidate"]["points"][-1], value["pipe"]["end"]) < 1e-8


def test_mixed_direction_path_with_insufficient_combined_fall_is_refused():
    value = load_case("fore-aft-correct-fall")
    value["pipe"]["end"] = [1000, 1000, 960]
    value["pipe"]["original_route"] = [[0, 0, 1000], [1000, 0, 980], [1000, 1000, 960]]
    result = run_workflow(value)
    assert result["candidate"] is None
    assert result["status"] == "no_route"
    assert checks(result["challenge_review"]["original"])["directional_drain_slope"]["status"] == "fail"


def test_context_keepout_cannot_be_waived_as_endpoint_connector():
    value = load_case("protected-passage")
    value["model_context"]["keep_outs"][0].update(min=[-20, -20, 980], max=[20, 20, 1020])
    with_context = apply_model_context(value, value["model_context"])
    obstacle = "context:keepout:walkway"
    assert any(item["id"] == obstacle for item in with_context["obstacles"])
    with pytest.raises(ValueError, match="cannot be treated as pipe connections"):
        with_endpoint_connections(with_context, {"start": [obstacle]}, max_stub_length=250)


def test_direct_public_validator_applies_raw_context_before_collision_check():
    value = load_case("protected-passage")
    assert value["obstacles"] == []
    result = validate_layout_route(value, value["pipe"]["original_route"])
    assert not result["valid"]
    assert any(issue.get("obstacle") == "context:keepout:walkway" for issue in result["issues"])
    assert value["obstacles"] == []
    assert "applied" not in value["model_context"]


def test_malformed_engineering_is_rejected_before_expensive_search(monkeypatch):
    value = load_case("valid-straight")
    value["engineering"]["pieces"][0]["centerline_length_mm"] = "not a measurement"
    called = []

    def unexpected_search(*args, **kwargs):
        called.append(True)
        raise AssertionError("Geometry search must not start for malformed engineering metadata")

    monkeypatch.setattr(core, "_search", unexpected_search)
    with pytest.raises(ValueError, match="centerline_length_mm"):
        run_workflow(value)
    assert not called


@pytest.mark.parametrize("units", ["m", "in"])
def test_context_and_input_units_are_scaled_once_and_original_units_retained(units):
    mm_input = load_case("protected-passage")
    expected = run_workflow(mm_input)
    value = scale_input(mm_input, units)
    context = value["model_context"]
    context["units"] = units
    scale = {"m": 1000, "in": 25.4}[units]
    for group in ("allowed_spaces", "keep_outs"):
        for item in context[group]:
            for key in ("min", "max"):
                item[key] = [coordinate / scale for coordinate in item[key]]
    before = deepcopy(value)
    result = run_workflow(value)
    assert value == before
    assert result["input_units"] == units
    assert result["status"] == "improved"
    assert result["original"]["length"] == pytest.approx(expected["original"]["length"])
    assert result["candidate"]["length"] == pytest.approx(expected["candidate"]["length"])
    normalized = result["normalized_layout"]
    assert normalized["units"] == "mm"
    assert normalized["model_context"]["units"] == "mm"
    assert len(normalized["obstacles"]) == 1
    assert normalized["obstacles"][0]["min"] == pytest.approx([800, -150, 800])
    assert not validate_layout_route(value, value["pipe"]["original_route"])["valid"]


MANIFEST = json.loads((EXAMPLES / "manifest.json").read_text())


@pytest.mark.parametrize("case", MANIFEST["cases"], ids=lambda case: case["id"])
def test_challenge_examples_retain_expected_behavior_and_evidence_limits(case):
    value = json.loads((EXAMPLES / case["layout"]).read_text())
    result = run_workflow(value)
    assert result["status"] == case["expected_status"]
    actual = checks(result["challenge_review"]["original"])
    for rule, expected in case["original_findings"].items():
        assert actual[rule]["status"] == expected
    assert result["challenge_review"]["original"]["engineering_compliance_certified"] is False
    assert actual["required_component_preservation"]["status"] == "needs_review"
    assert not result["checks"]["official_standards_compliance"]
    if result["candidate"]:
        assert result["candidate"]["points"][0] == value["pipe"]["start"]
        assert result["candidate"]["points"][-1] == value["pipe"]["end"]
        assert not result["challenge_review"]["candidate"]["hard_failures"]


@pytest.fixture(scope="module")
def challenge_server(tmp_path_factory):
    server = make_server(port=0, output=tmp_path_factory.mktemp("challenge-http"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def http_request(server, method, path, payload=None):
    client = HTTPConnection("127.0.0.1", server.server_port, timeout=30)
    try:
        headers = {"Content-Type": "application/json"} if payload is not None else {}
        client.request(method, path, body=json.dumps(payload) if payload is not None else None, headers=headers)
        response = client.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        client.close()


def test_http_v3_result_downloads_review_workbook_with_findings_and_real_coordinates(challenge_server):
    value = load_case("added-detour")
    value["name"] = '=HYPERLINK("https://example.invalid","not a formula")'
    status, _, data = http_request(challenge_server, "POST", "/api/run", {"layout": value})
    assert status == 200, data
    response = json.loads(data)
    assert response["result"]["rule_profile"] == "fmg-v3"
    assert response["result"]["status"] == "improved"
    assert response["exports"]["excel"].endswith("/route-review.xlsx")
    status, headers, workbook = http_request(challenge_server, "GET", response["exports"]["excel"])
    assert status == 200
    assert headers["Content-Type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    namespace = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(BytesIO(workbook)) as archive:
        doc = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        sheet_names = [item.attrib["name"] for item in doc.findall("m:sheets/m:sheet", namespace)]
        assert {"Summary", "Rule findings", "Route points", "Route legs", "Declared pieces"} <= set(sheet_names)
        summary = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        assert summary.findall(".//m:f", namespace) == []
        assert value["name"] in [item.text for item in summary.findall(".//m:t", namespace)]
        findings_xml = archive.read("xl/worksheets/sheet2.xml").decode()
        assert "physical_installability" in findings_xml
        assert "needs_review" in findings_xml
        point_doc = ElementTree.fromstring(archive.read("xl/worksheets/sheet3.xml"))
        candidate_points = []
        for row in point_doc.findall("m:sheetData/m:row", namespace):
            cells = row.findall("m:c", namespace)
            if not cells or cells[0].findtext("m:is/m:t", namespaces=namespace) != "candidate":
                continue
            candidate_points.append([float(cell.findtext("m:v", namespaces=namespace)) for cell in cells[2:5]])
        assert candidate_points == response["result"]["candidate"]["points"]
    status, _, html = http_request(challenge_server, "GET", response["exports"]["report"])
    assert status == 200
    assert b"Challenge rule review" in html
    assert b"route-review.xlsx" in html


def test_http_rejects_invalid_v3_request_without_producing_result_exports(challenge_server):
    value = load_case("valid-straight")
    value["rules"]["clearance"] = 50
    status, _, data = http_request(challenge_server, "POST", "/api/run", {"layout": value})
    assert status == 400, data
    record = json.loads(data)
    assert "50.8" in record["error"]
    assert "exports" not in record


def test_direct_public_validator_enforces_v3_clearance_floor():
    value = load_case("valid-straight")
    value["rules"]["clearance"] = 50
    with pytest.raises(ValueError, match="50.8"):
        validate_layout_route(value, value["pipe"]["original_route"])


@pytest.mark.parametrize("units", ["mm", "in", "m"])
def test_direct_public_validator_applies_v3_default_clearance_in_mm(units):
    value = load_case("valid-straight")
    # The OD is 40 mm. A box 70.5 mm from the centerline leaves 50.5 mm
    # clearance, which the old 50 mm prototype default would incorrectly pass.
    value["obstacles"] = [{"id": "close-wall", "min": [900, 70.5, 900], "max": [1100, 150, 1100]}]
    del value["rules"]["clearance"]
    if units != "mm":
        value = scale_input(value, units)
    before = deepcopy(value)
    result = validate_layout_route(value, value["pipe"]["original_route"])
    assert value == before
    assert not result["valid"]
    assert any(issue.get("obstacle") == "close-wall" for issue in result["issues"])
    assert result["length"] == pytest.approx(2000)


@pytest.mark.parametrize("endpoint", ["start", "end"])
def test_required_space_visited_by_fixed_stub_does_not_reject_movable_middle(endpoint):
    value = load_case("added-detour")
    if endpoint == "start":
        points = [[0, 0, 1000], [200, 0, 1000], [200, 1000, 1000], [2000, 1000, 1000], [2000, 0, 1000]]
        stub = points[:2]
        connector_min, connector_max = [-10, -10, 990], [10, 10, 1010]
        required_min, required_max = [-50, -50, 950], [100, 50, 1050]
    else:
        points = [[0, 0, 1000], [0, 1000, 1000], [1800, 1000, 1000], [1800, 0, 1000], [2000, 0, 1000]]
        stub = points[-2:]
        connector_min, connector_max = [1990, -10, 990], [2010, 10, 1010]
        required_min, required_max = [1900, -50, 950], [2050, 50, 1050]
    value["pipe"]["original_route"] = points
    value["obstacles"] = [{"id": "fixed-connector", "min": connector_min, "max": connector_max}]
    value["protected_endpoints"] = [{"endpoint": endpoint, "points": stub, "max_distance": 250, "obstacle_ids": ["fixed-connector"]}]
    value["model_context"] = {
        "units": "mm", "coordinate_frame": {"id": "synthetic-frame", "shared_coordinates_confirmed": True},
        "allowed_spaces": [
            {"id": "room", "min": [-250, -250, 100], "max": [2250, 1500, 1700], "source": "synthetic room"},
            {"id": "fixed-port-compartment", "min": required_min, "max": required_max, "source": "synthetic required connection space"},
        ],
        "required_space_ids": ["fixed-port-compartment"],
    }
    before = deepcopy(value)
    result = run_workflow(value)
    assert value == before
    assert result["status"] == "improved"
    assert result["candidate"]["length"] == pytest.approx(2000)
    assert result["candidate"]["length"] < result["original"]["length"]
    proposed = result["candidate"]["points"]
    assert (proposed[:2] if endpoint == "start" else proposed[-2:]) == stub
    assert result["normalized_layout"]["model_context"]["required_space_ids"] == ["fixed-port-compartment"]
    candidate_checks = result["challenge_review"]["candidate"]["findings"]
    required_checks = [item for item in candidate_checks if item["id"] == "context_required_space"]
    assert required_checks and all(item["status"] == "pass" for item in required_checks)
    # The movable segment itself never enters the required compartment.
    from pipe_router.model_context import validate_allowed_spaces
    middle = proposed[1:] if endpoint == "start" else proposed[:-1]
    required_middle = [item for item in validate_allowed_spaces(result["normalized_layout"], middle) if item["rule_id"] == "required_space"]
    assert required_middle[0]["status"] == "fail"
