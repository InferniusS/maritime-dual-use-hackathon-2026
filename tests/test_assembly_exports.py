"""The delivered geometry and schedules agree with the accepted proposal."""
from copy import deepcopy
import math
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import ezdxf
import pytest

from pipe_router.assembly import plan_assembly, render_reference
from pipe_router.review_export import assembly_panel, write_review_xlsx
from pipe_router.workflow import run_workflow
from scripts.run_demo import make_figure, print_route_svg, write_dxf, write_html


def example():
    points = [[0, 0, 0], [1000, 0, 0], [1000, 1000, 0]]
    layout = {"name": "Assembly export test", "units": "mm", "provenance": "synthetic", "system_type": "pressurized",
              "bounds": {"min": [-500, -500, -500], "max": [1500, 1500, 500]},
              "pipe": {"start": points[0], "end": points[-1], "diameter": 40, "original_route": points},
              "obstacles": [], "rules": {"clearance": 0, "grid_step": 250, "bend_penalty": 300}}
    result = run_workflow(layout)
    result["candidate"] = deepcopy(result["original"])
    plan = plan_assembly(result["normalized_layout"], points, bend_radius_mm=200)
    assert plan["status"] == "ready"
    plan["reference_meshes"] = render_reference(points, 40)["meshes"]
    plan["change_type"] = "review_proposal"
    result["assembly"] = plan
    result["comparison_objective"] = "rounded_bends_elevation_length"
    return layout, result


def test_ready_assembly_plot_and_dxf_preserve_vertices_and_component_dimensions(tmp_path):
    layout, result = example()
    before = deepcopy((layout, result))
    figure = make_figure(layout, result)
    traces = [item for item in figure.data if item.legendgroup == "rounded-proposal"]
    assert len(traces) == len(result["assembly"]["meshes"])
    for trace, mesh in zip(traces, result["assembly"]["meshes"]):
        assert list(zip(trace.x, trace.y, trace.z)) == [tuple(p) for p in mesh["vertices"]]
        assert list(zip(trace.i, trace.j, trace.k)) == [tuple(face) for face in mesh["faces"]]
    assert next(item for item in figure.data if item.name == "Suggested route").visible == "legendonly"
    path = tmp_path / "proposal.dxf"
    write_dxf(layout, result, path)
    doc = ezdxf.readfile(path)
    assert not doc.audit().errors
    faces = list(doc.modelspace().query('3DFACE[layer=="SYNTHETIC_PROPOSED_PIPE_ENVELOPE"]'))
    assert len(faces) == sum(len(mesh["faces"]) for mesh in result["assembly"]["meshes"])
    first_mesh = result["assembly"]["meshes"][0]
    assert list(faces[0].dxf.vtx0) == pytest.approx(first_mesh["vertices"][first_mesh["faces"][0][0]])
    centerline = list(doc.modelspace().query('POLYLINE[layer=="SYNTHETIC_ROUNDED_CENTERLINE"]'))
    assert len(centerline) == 1
    assert [list(point) for point in centerline[0].points()] == result["assembly"]["centerline_points"]
    labels = ' '.join(item.dxf.text for item in doc.modelspace().query('TEXT'))
    assert "800.0 mm" in labels and "R200" in labels and "90 deg" in labels
    assert "not verified catalog" in labels
    assert (layout, result) == before


def test_component_workbook_has_cut_lengths_bend_geometry_and_literal_ids(tmp_path):
    layout, result = example()
    result["assembly"]["components"][0]["id"] = '=HYPERLINK("untrusted")'
    path = tmp_path / "review.xlsx"
    write_review_xlsx(layout, result, path)
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(path) as book:
        workbook = ET.fromstring(book.read("xl/workbook.xml"))
        sheets = workbook.findall("s:sheets/s:sheet", ns)
        number = next(index for index, sheet in enumerate(sheets, 1) if sheet.attrib["name"] == "Proposed assembly")
        document = ET.fromstring(book.read(f"xl/worksheets/sheet{number}.xml"))
        rows = document.findall("s:sheetData/s:row", ns)
        assert len(rows) == 4
        cells = {cell.attrib["r"]: cell for row in rows for cell in row}
        assert cells["A2"].attrib["t"] == "inlineStr"
        assert cells["A2"].find("s:is/s:t", ns).text.startswith("=HYPERLINK")
        assert not document.findall(".//s:f", ns)
        assert float(cells["J2"].find("s:v", ns).text) == pytest.approx(800)
        assert float(cells["K3"].find("s:v", ns).text) == pytest.approx(100*math.pi)
        assert float(cells["L3"].find("s:v", ns).text) == 200
        assert float(cells["M3"].find("s:v", ns).text) == 90
        assert "not verified" in cells["Q2"].find("s:is/s:t", ns).text


def test_before_after_controls_preserve_context_and_label_rounded_baseline():
    layout, result = example()
    result["assembly"]["reference_representation"] = "rounded_baseline"
    result["assembly"]["reference_meshes"] = result["assembly"]["meshes"]
    figure = make_figure(layout, result)
    original_index = next(i for i, trace in enumerate(figure.data) if trace.name == "Original route")
    assert figure.data[original_index].visible == "legendonly"
    assert any(trace.name == "Original rounded reference" for trace in figure.data)
    buttons = {button.label: button.args[0]["visible"] for button in figure.layout.updatemenus[0].buttons}
    for index, trace in enumerate(figure.data):
        if trace.legendgroup == "original-width":
            assert buttons["Before"][index] is True and buttons["After"][index] is False
        elif trace.legendgroup == "rounded-proposal":
            assert buttons["Before"][index] is False and buttons["After"][index] is True
        elif trace.name == "Fixed connections":
            assert all(mask[index] is True for mask in buttons.values())
    assert buttons["Both"][original_index] == "legendonly"
    panel = assembly_panel(result)
    assert "same geometric radius assumption" in panel
    assert "unfitted reference" not in panel
    assert "<details><summary>Proposed geometric component schedule" in panel


@pytest.mark.parametrize("blocked", [True, False])
def test_rejected_geometry_is_not_exported_as_accepted_components(tmp_path, blocked):
    layout, result = example()
    if blocked:
        result["assembly"]["status"] = "blocked"
    else:
        # A stale ready assembly must not survive rejection elsewhere in the workflow.
        result["candidate"] = None
    figure = make_figure(layout, result)
    assert not any(item.legendgroup == "rounded-proposal" for item in figure.data)
    path = tmp_path / "rejected.dxf"
    write_dxf(layout, result, path)
    doc = ezdxf.readfile(path)
    assert not list(doc.modelspace().query('3DFACE[layer=="SYNTHETIC_PROPOSED_PIPE_ENVELOPE"]'))
    book_path = tmp_path / "rejected.xlsx"
    write_review_xlsx(layout, result, book_path)
    with ZipFile(book_path) as book:
        xml = book.read("xl/worksheets/sheet6.xml").decode()
        assert "No accepted rounded assembly" in xml
        assert "<v>800" not in xml
    assert "Rounded pipe proposal blocked" in assembly_panel(result)


def test_report_has_print_safe_isometric_evidence_and_no_unearned_fitting_claim(tmp_path):
    layout, result = example()
    result["assembly"]["findings"].append({"message": "Check <script>alert(1)</script>"})
    path = tmp_path / "report.html"
    write_html(layout, result, .1, path)
    page = path.read_text()
    assert "Review pipe proposal" in page
    assert "Not assessed as an assembly" in page
    assert "Selected centerline bend radius: 200 mm" in page
    assert "not an orderable parts list" in page
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert ".plot { display: none }" in page
    assert ".print-figure { display: block" in page
    svg = print_route_svg(layout, result)
    document = ET.fromstring(svg)
    assert document.tag.endswith("svg")
    assert "800.0 mm" in svg
    assert "Start" in svg and "End" in svg
