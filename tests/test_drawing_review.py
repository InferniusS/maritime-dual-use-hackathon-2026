"""Drawing screening is traceable and never turns missing evidence into a pass."""
from copy import deepcopy
import json
from zipfile import ZipFile

import pytest

from pipe_router import drawing_review
from pipe_router.drawing_review import review_drawing, write_drawing_review
from pipe_router.cad_layout import prepare_cad_layout
from test_cad_layout import solid
from test_autocad import autocad_workspace, solid_snapshot
from test_web import json_request


def drawing(routes, obstacles=()):
    candidates, objects = [], []
    for index, points in enumerate(routes, 1):
        handle = f"{index:X}"
        candidates.append({"id": f"run-{index}", "name": f"Source {handle}", "points": points,
            "geometric_diameter": 10, "source_handles": [handle], "instance_path": []})
        objects.append(solid(handle, [min(p[i] for p in points)-5 for i in range(3)],
                            [max(p[i] for p in points)+5 for i in range(3)]))
    return {"name": "Test drawing", "source_sha256": "source-hash", "provenance": "cad", "units": "mm",
            "route_candidates": candidates, "objects": [*objects, *obstacles], "counts": {"solid_instances": len(objects)+len(obstacles)}}


def test_straight_route_is_not_called_optimal_or_compliant():
    cad = drawing([[[0,0,0], [1000,0,0]]])
    before = deepcopy(cad)
    result = review_drawing(cad)
    row = result["candidates"][0]
    assert row["review_status"] == "no_obvious_detour"
    assert row["rule_counts"]["needs_review"] > 0
    assert not row["full_compliance_verified"]
    assert result["review"]["coverage"] == "all_supported_routes_screened"
    assert not result["review"]["complete_model_verified"]
    assert cad == before


def test_detour_is_only_an_opportunity_without_search():
    result = review_drawing(drawing([[[0,0,0], [0,500,0], [1000,500,0], [1000,0,0]]]))
    row = result["candidates"][0]
    assert row["review_status"] == "opportunity"
    assert row["axis_backtracking_mm"] == 1000
    assert "proven saving" in ' '.join(result["review"]["limitations"])
    assert "candidate" not in row


def test_connections_are_flagged_without_waivers():
    cad = drawing([[[0,0,0], [1000,0,0]]], [solid("F", [990,-20,-20], [1050,20,20])])
    row = review_drawing(cad)["candidates"][0]
    assert row["review_status"] == "attention"
    assert row["endpoint_contact_count"] == 1
    assert row["issue_count"] > 0
    assert any(item.get("code") == "obstacle_collision" for item in row["findings"])
    assert not prepare_cad_layout(cad, row["id"]).get("protected_endpoints")


def test_chain_members_are_not_double_counted():
    cad = drawing([[[0,0,0], [1000,0,0]], [[1000,0,0], [2000,0,0]]])
    result = review_drawing(cad)
    assert result["review"]["total_supported_routes"] == 1
    assert result["candidates"][0]["member_count"] == 2
    assert result["candidates"][0]["source_handles"] == ["1", "2"]


def test_gravity_screen_uses_requested_flow_direction_and_pitch():
    cad = drawing([[[0,0,0], [1000,0,20]]])
    normal = review_drawing(cad, system_type="gravity")["candidates"][0]
    reverse = review_drawing(cad, system_type="gravity", reverse_flow=True, min_slope=.01)["candidates"][0]
    steeper = review_drawing(cad, system_type="gravity", reverse_flow=True, min_slope=.04)["candidates"][0]
    assert any(item["code"] == "uphill_flow" for item in normal["findings"])
    assert not reverse["findings"]
    assert reverse["start"] == [1000,0,20]
    assert any(item["code"] == "insufficient_slope" for item in steeper["findings"])


def test_cap_and_time_budget_expose_unscreened_routes(monkeypatch):
    cad = drawing([[[0,i*1000,0], [1000,i*1000,0]] for i in range(3)])
    monkeypatch.setattr(drawing_review, "MAX_REVIEW_ROUTES", 2)
    result = review_drawing(cad)
    assert result["review"]["reviewed_routes"] == 2
    assert result["review"]["unreviewed_routes"] == 1
    assert result["review"]["omitted_from_list"] == 1
    monkeypatch.setattr(drawing_review, "MAX_REVIEW_SECONDS", -1)
    result = review_drawing(cad)
    assert result["review"]["reviewed_routes"] == 0
    assert result["review"]["unreviewed_routes"] == 3
    assert all(row["review_status"] == "not_reviewed" for row in result["candidates"])


def test_unreadable_or_dense_section_remains_an_explicit_review_item():
    cad = drawing([[[0,0,0], [1000,0,0]]])
    cad["objects"].clear()
    result = review_drawing(cad)
    assert result["candidates"][0]["review_status"] == "needs_review"
    assert result["review"]["unreviewed_routes"] == 1


def test_report_escapes_drawing_text_and_workbook_never_uses_formulas(tmp_path):
    cad = drawing([[[0,0,0], [1000,0,0]]])
    cad["route_candidates"][0]["name"] = '=HYPERLINK("<script>bad</script>")'
    summary = {**review_drawing(cad), "source_sha256": "source-hash"}
    write_drawing_review(summary, tmp_path)
    page = (tmp_path / "report.html").read_text()
    assert "<script>bad</script>" not in page
    assert "&lt;script&gt;" in page
    with ZipFile(tmp_path / "route-review.xlsx") as book:
        xml = book.read("xl/worksheets/sheet1.xml").decode()
        assert "<f>" not in xml
        assert "HYPERLINK" in xml


def test_review_api_inspects_snapshot_and_preserves_identity_for_selection(autocad_workspace, solid_snapshot):
    source, payload = solid_snapshot
    payload.pop("selected_handles")
    status, response = json_request(autocad_workspace, "POST", "/api/autocad/review", payload)
    assert status == 200, response
    assert response["review"]["reviewed_routes"] == 1
    assert len(response["candidates"]) == 1
    for path in response["exports"].values():
        assert (autocad_workspace["output"] / response["cad_id"] / path.split('/')[-1]).is_file()
    cached = json.loads((autocad_workspace["output"] / response["cad_id"] / "cad-inspection.json").read_text())
    assert cached["autocad_context"]["document"] == payload["document"]
    status, selected = json_request(autocad_workspace, "POST", "/api/prepare-cad-layout", {
        "cad_id": response["cad_id"], "candidate_id": response["candidates"][0]["id"]})
    assert status == 200, selected
    assert selected["layout"]["cad_context"]["source_snapshot_sha256"] == response["source_sha256"]


@pytest.mark.parametrize("settings", [{"clearance": 50}, {"system_type": "unknown"}, {"padding": -1}])
def test_invalid_settings_fail_instead_of_silently_downgrading(settings):
    with pytest.raises(ValueError):
        review_drawing(drawing([[[0,0,0], [1000,0,0]]]), **settings)
