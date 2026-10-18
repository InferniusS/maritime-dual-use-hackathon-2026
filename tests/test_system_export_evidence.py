"""Accepted piece schedules, source labels and global geometry stay traceable."""
from copy import deepcopy
import json
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import ezdxf
import pytest

from pipe_router.system_export import geometry_schedule_issues, write_system_review
from pipe_router.system_optimization import optimize_sections
from test_system_optimization import crossing_drawing, drawing, selections


def workbook_rows(path, name):
    namespace = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(path) as book:
        workbook = ET.fromstring(book.read("xl/workbook.xml"))
        sheets = workbook.findall("s:sheets/s:sheet", namespace)
        index = next(i for i, sheet in enumerate(sheets, 1) if sheet.attrib["name"] == name)
        document = ET.fromstring(book.read(f"xl/worksheets/sheet{index}.xml"))
        answer = []
        for row in document.findall("s:sheetData/s:row", namespace):
            values = []
            for cell in row:
                value = cell.find("s:v", namespace)
                if value is not None:
                    values.append(float(value.text))
                else:
                    text = cell.find("s:is/s:t", namespace)
                    values.append(text.text or "")
            answer.append(values)
        return [dict(zip(answer[0], row)) for row in answer[1:]]


def test_component_coordinates_and_ids_match_dxf_and_analytic_length(tmp_path):
    requests = selections(2)
    requests[0]["source_scan_number"], requests[1]["source_scan_number"] = 17, 29
    summary = optimize_sections(crossing_drawing(), requests)
    before = deepcopy(summary)
    write_system_review(summary, tmp_path)
    assert summary == before
    assert json.loads((tmp_path / "result.json").read_text()) == summary
    pieces = workbook_rows(tmp_path / "route-review.xlsx", "Geometric pieces")
    checks = workbook_rows(tmp_path / "route-review.xlsx", "Export reconciliation")
    doc = ezdxf.readfile(tmp_path / "route-centerlines.dxf")
    for route in summary["routes"]:
        assert not geometry_schedule_issues(route)
        assembly = route["result"]["assembly"]
        records = [row for row in pieces if row["Batch item"] == route["index"]]
        assert len(records) == len(assembly["components"])
        assert sum(row["Centerline length mm"] for row in records) == pytest.approx(assembly["metrics"]["centerline_length_mm"])
        for part, row in zip(assembly["components"], records):
            assert row["Source scan number"] == route["source_scan_number"]
            assert row["Candidate ID"] == route["candidate_id"]
            assert [row[f"Start {axis} mm"] for axis in "XYZ"] == part["start"]
            assert [row[f"End {axis} mm"] for axis in "XYZ"] == part["end"]
            assert row["Outer diameter mm"] == part["outer_diameter_mm"]
            assert row["Centerline length mm"] == part["length_mm"]
            if part["kind"] == "bend":
                assert [row[f"Bend center {axis} mm"] for axis in "XYZ"] == part["center"]
                assert [row[f"Bend normal {axis}"] for axis in "XYZ"] == part["normal"]
                assert row["Bend arc length mm"] == part["length_mm"]
            faces = [entity for entity in doc.modelspace().query(f'3DFACE[layer=="SECTION_{route["index"]:02d}_PROPOSAL"]')
                     if entity.get_xdata("MARITIME_REVIEW")[5].value == part["id"]]
            mesh = next(mesh for mesh in assembly["meshes"] if mesh["id"] == part["id"])
            assert len(faces) == len(mesh["faces"])
            assert list(faces[0].dxf.vtx0) == pytest.approx(mesh["vertices"][mesh["faces"][0][0]])
            assert faces[0].get_xdata("MARITIME_REVIEW")[4].value == f'source_scan_number={route["source_scan_number"]}'
        check = next(check for check in checks if check["Batch item"] == route["index"])
        assert check["Scheduled length mm"] == check["Reported rounded length mm"]
    page = (tmp_path / "report.html").read_text()
    assert "Batch item 1" in page and "Source scan number: 17" in page
    assert "not MARITIMESCAN route numbers" in page


@pytest.mark.parametrize("corruption", ["mesh_identity", "mesh_endpoint", "schedule_length", "metric_length", "diameter", "continuity", "centerline"])
def test_inconsistent_geometry_withheld_without_changing_recorded_solver_outcome(tmp_path, corruption):
    summary = optimize_sections(crossing_drawing(), selections(2))
    route = summary["routes"][1]
    assembly = route["result"]["assembly"]
    if corruption == "mesh_identity":
        assembly["meshes"][0]["id"] = "wrong-part"
    elif corruption == "mesh_endpoint":
        assembly["meshes"][0]["vertices"][-2][1] += 100
    elif corruption == "schedule_length":
        assembly["components"][0]["length_mm"] += 100
    elif corruption == "metric_length":
        assembly["metrics"]["centerline_length_mm"] += 100
    elif corruption == "diameter":
        assembly["components"][0]["outer_diameter_mm"] += 5
    elif corruption == "continuity":
        assembly["components"][1]["start"][0] += 20
    else:
        assembly["centerline_points"][0] = [99, 99, 99]
    before = deepcopy(summary)
    write_system_review(summary, tmp_path)
    assert summary == before and route["status"] == "proposed"
    assert "Export withheld: geometry consistency needs review" in (tmp_path / "report.html").read_text()
    pieces = workbook_rows(tmp_path / "route-review.xlsx", "Geometric pieces")
    assert not any(row["Batch item"] == 2 for row in pieces)
    assert any(row["Batch item"] == 1 for row in pieces)
    doc = ezdxf.readfile(tmp_path / "route-centerlines.dxf")
    assert "SECTION_02_PROPOSAL" not in doc.layers


def test_active_search_settings_and_action_are_visible_without_weakening_clearance(tmp_path):
    summary = optimize_sections(drawing([[[0, 0, 0], [2000, 0, 0]]]), selections(1))
    route = summary["routes"][0]
    route["status"] = "blocked"
    route["coordination"]["accepted"] = False
    route["result"]["search_region_review"] = {
        "padding_mm": 500, "clearance_mm": 50.8, "pipe_outer_diameter_mm": 114.3,
        "bounds_mm": {"min": [-100, -100, -100], "max": [2100, 100, 100]},
        "boundary_review": "represented_barrier_spans_search_region",
        "message": "Represented box spans Y", "barriers": [{"obstacle_id": "<wall>", "axis": "Y"}],
        "next_action": "Review the wall and increase search padding; keep clearance unchanged.",
    }
    write_system_review(summary, tmp_path)
    page = (tmp_path / "report.html").read_text()
    assert "Next action:" in page and "keep clearance unchanged" in page
    assert "&lt;wall&gt;" in page and "<wall>" not in page
    settings = workbook_rows(tmp_path / "route-review.xlsx", "Search settings")
    assert next(row["Value"] for row in settings if row["Setting"] == "Padding mm") == 500
    assert next(row["Value"] for row in settings if row["Setting"] == "Required clearance mm") == 50.8
    assert next(row["Value"] for row in settings if row["Setting"] == "Search diagnosis") == "represented_barrier_spans_search_region"


def test_fixed_connection_summary_records_remaining_obstacles_and_not_certified_ports(tmp_path):
    summary = optimize_sections(drawing([[[0, 0, 0], [2000, 0, 0]]]), selections(1))
    row = summary["routes"][0]
    row["result"]["connection_selection_review"] = {"status": "blocked", "message": "Fixed stub remains blocked; keep unrelated obstacles active.",
        "endpoints": [{"endpoint": "start", "status": "blocked", "fixed_endpoint": [0, 0, 0], "movable_anchor": [50, 0, 0],
            "fixed_stub_points": [[0, 0, 0], [50, 0, 0]], "fixed_stub_length_mm": 50,
            "departure_direction_into_movable_section": [1, 0, 0], "direction_basis": "last_preserved_segment",
            "selected_obstacle_ids": ["connector"], "blockers": [{"source_handle": "<adjacent>", "scopes": ["unchanged_stub", "movable_anchor"]}]}]}
    write_system_review(summary, tmp_path)
    page = (tmp_path / "report.html").read_text()
    assert "Fixed connection check" in page and "&lt;adjacent&gt; (unchanged stub, movable anchor)" in page
    assert "connection identity is not automatically verified" in page
    records = workbook_rows(tmp_path / "route-review.xlsx", "Fixed connections")
    assert records[0]["Movable anchor X mm"] == 50
    assert records[0]["Fixed stub length mm"] == 50
    assert records[0]["Connection identity verified"] == "False"


@pytest.mark.parametrize("number", [None, True, -1, 0, 1.5, "17"])
def test_invalid_scan_label_never_becomes_geometry_identity(number):
    requests = selections(1)
    requests[0]["source_scan_number"] = number
    row = optimize_sections(drawing([[[0, 0, 0], [2000, 0, 0]]]), requests)["routes"][0]
    assert "source_scan_number" not in row
    assert row["candidate_id"] == requests[0]["candidate_id"]
