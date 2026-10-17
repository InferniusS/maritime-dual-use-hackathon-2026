"""Verify the actual handoff files agree with the routing result."""

import json
import hashlib
from pathlib import Path
import subprocess
import sys

import ezdxf
import pytest

from pipe_router.workflow import run_workflow
from scripts.run_demo import make_figure, write_html


ROOT = Path(__file__).resolve().parents[1]


def run_demo(tmp_path, layout=None):
    command = [sys.executable, str(ROOT / "scripts" / "run_demo.py"), "--output", str(tmp_path / "report")]
    if layout is not None:
        source = tmp_path / "layout.json"
        source.write_text(json.dumps(layout), encoding="utf-8")
        command.extend(["--layout", str(source)])
    return subprocess.run(command, capture_output=True, text=True, check=False)


def test_demo_exports_same_routes_in_json_and_dxf(tmp_path):
    completed = run_demo(tmp_path)

    assert completed.returncode == 0, completed.stderr
    folder = tmp_path / "report"
    report = json.loads((folder / "result.json").read_text())
    assert report["layout"]["provenance"] == "synthetic"
    assert report["result"]["candidate"]["length"] == 8000
    assert report["result"]["candidate"]["bends"] == 2
    document = ezdxf.readfile(folder / "route-centerlines.dxf")
    assert document.units == ezdxf.units.MM
    audit = document.audit()
    assert not audit.errors
    assert not audit.fixes
    for key in ("original", "candidate"):
        layer = "SYNTHETIC_" + key.upper() + "_CENTERLINE"
        entities = list(document.modelspace().query(f'POLYLINE[layer=="{layer}"]'))
        assert len(entities) == 1
        assert [list(point) for point in entities[0].points()] == report["result"][key]["points"]
    page = (folder / "index.html").read_text()
    assert "2,500 mm shorter in this test" in page
    assert "plotly.js" in page
    assert '<script src="http' not in page
    assert '"modeBarButtonsToRemove": ["sendChartToCloud"]' in page
    assert "SYNTHETIC TEST" in page


def test_blocked_layout_produces_review_report_without_candidate(tmp_path):
    layout = json.loads((ROOT / "examples" / "test-room.json").read_text())
    layout["obstacles"][0]["max"][1] = 4000

    completed = run_demo(tmp_path, layout)

    assert completed.returncode == 0, completed.stderr
    folder = tmp_path / "report"
    report = json.loads((folder / "result.json").read_text())
    assert report["result"]["status"] == "no_route"
    assert report["result"]["candidate"] is None
    assert "No accepted route from this search" in (folder / "index.html").read_text()
    document = ezdxf.readfile(folder / "route-centerlines.dxf")
    assert not list(document.modelspace().query('POLYLINE[layer=="SYNTHETIC_CANDIDATE_CENTERLINE"]'))


def test_unconfirmed_units_are_not_assumed_by_demo(tmp_path):
    layout = json.loads((ROOT / "examples" / "test-room.json").read_text())
    layout["units"] = "unknown"

    completed = run_demo(tmp_path, layout)

    assert completed.returncode != 0
    assert "units must be mm, m, or in" in completed.stderr
    assert not (tmp_path / "report").exists()


def review_layout(*, provenance="synthetic", system_type="pressurized", units="mm"):
    start = [10, 10, 70] if system_type == "gravity" else [10, 10, 50]
    end = [90, 10, 30] if system_type == "gravity" else [90, 10, 50]
    return {
        "name": "Review example", "provenance": provenance, "units": units,
        "system_type": system_type,
        "bounds": {"min": [0, 0, 0], "max": [100, 100, 100]},
        "obstacles": [],
        "pipe": {"start": start, "end": end, "diameter": 4, "original_route": [start, end]},
        "rules": {"clearance": 3, "grid_step": 10, "bend_penalty": 5, "min_slope": 0.1},
        "assumptions": ["Prototype rules remain unverified"],
    }


def test_cad_derived_report_preserves_provenance_in_html_json_and_dxf(tmp_path):
    layout = review_layout(provenance="cad-derived")
    layout["source"] = {"filename": "supplied.dwg", "method": "manual point extraction"}
    completed = run_demo(tmp_path, layout)
    assert completed.returncode == 0, completed.stderr
    folder = tmp_path / "report"
    report = json.loads((folder / "result.json").read_text())
    assert report["source_layout"] == layout
    assert report["layout"]["provenance"] == "cad-derived"
    assert report["result"]["provenance"] == "cad-derived"
    page = (folder / "index.html").read_text()
    assert "CAD-DERIVED REVIEW" in page
    assert "SYNTHETIC TEST" not in page
    assert "geometry is invented" not in page
    assert "Pass implemented checks" in page
    assert "does not certify compliance" in page
    assert "extraction method and completeness still need review" in page
    document = ezdxf.readfile(folder / "route-centerlines.dxf")
    assert not document.audit().errors
    assert not any(layer.dxf.name.startswith("SYNTHETIC_") for layer in document.layers)
    for key in ("original", "candidate"):
        entities = list(document.modelspace().query(f'POLYLINE[layer=="CAD_DERIVED_{key.upper()}_CENTERLINE"]'))
        assert len(entities) == 1
        assert [list(point) for point in entities[0].points()] == report["result"][key]["points"]
    notes = " ".join(entity.dxf.text for entity in document.modelspace().query("TEXT"))
    assert "CAD-DERIVED PROTOTYPE REVIEW" in notes
    assert "Unverified rules" in notes


def test_gravity_report_explains_slopes_and_exports_sloped_geometry(tmp_path):
    completed = run_demo(tmp_path, review_layout(system_type="gravity"))
    assert completed.returncode == 0, completed.stderr
    folder = tmp_path / "report"
    report = json.loads((folder / "result.json").read_text())
    page = (folder / "index.html").read_text()
    assert "Gravity drain" in page
    assert "selected minimum slope of 0.1" in page
    assert "start-to-end direction" in page
    assert "Pressure routes use axis-aligned segments" not in page
    assert report["result"]["candidate"]["valid"]
    document = ezdxf.readfile(folder / "route-centerlines.dxf")
    route = next(iter(document.modelspace().query('POLYLINE[layer=="SYNTHETIC_CANDIDATE_CENTERLINE"]')))
    points = [list(point) for point in route.points()]
    assert points == report["result"]["candidate"]["points"]
    assert points[0][2] > points[-1][2]


def test_inch_input_is_preserved_but_all_exports_are_mm(tmp_path):
    layout = review_layout(units="in")
    completed = run_demo(tmp_path, layout)
    assert completed.returncode == 0, completed.stderr
    folder = tmp_path / "report"
    report = json.loads((folder / "result.json").read_text())
    assert report["source_layout"]["units"] == "in"
    assert report["source_layout"]["pipe"]["diameter"] == 4
    assert report["layout"]["units"] == "mm"
    assert report["layout"]["pipe"]["diameter"] == pytest.approx(101.6)
    page = (folder / "index.html").read_text()
    assert "Declared input units in" in page
    assert "101.6 mm outer diameter" in page
    figure = make_figure(layout, report["result"])
    connection_trace = next(trace for trace in figure.data if trace.name == "Fixed connections")
    assert list(connection_trace.x) == pytest.approx([254, 2286])
    document = ezdxf.readfile(folder / "route-centerlines.dxf")
    assert document.units == ezdxf.units.MM
    route = next(iter(document.modelspace().query('POLYLINE[layer=="SYNTHETIC_CANDIDATE_CENTERLINE"]')))
    assert [list(point) for point in route.points()] == report["result"]["candidate"]["points"]


def test_reports_escape_supplied_names_and_keep_review_caveats(tmp_path):
    layout = review_layout()
    layout["name"] = "<img src=x onerror=alert(1)>"
    completed = run_demo(tmp_path, layout)
    assert completed.returncode == 0, completed.stderr
    page = (tmp_path / "report" / "index.html").read_text()
    assert layout["name"] not in page
    assert "&lt;img src=x onerror=alert(1)&gt;" in page
    assert "Every corner and connection still needs designer review" in page


def test_rendering_rejects_unscaled_geometry_without_normalized_workflow_result():
    layout = review_layout(units="in")
    result = run_workflow(layout)
    del result["normalized_layout"]
    with pytest.raises(ValueError, match="normalized"):
        make_figure(layout, result)


def test_shared_cad_demo_reproduces_baseline_and_detour_without_source_files():
    folder = ROOT / "examples" / "real-cad-demo"
    baseline = json.loads((folder / "baseline.json").read_text())
    detour = json.loads((folder / "detour.json").read_text())
    baseline_result = run_workflow(baseline)
    result = run_workflow(detour)

    assert baseline_result["status"] == "unchanged"
    assert baseline_result["candidate"]["points"] == baseline_result["original"]["points"]
    assert baseline_result["original"]["length"] == pytest.approx(3679.2477574864556)
    assert result["status"] == "improved"
    assert result["original"]["length"] == pytest.approx(4179.247757486455)
    assert result["candidate"]["length"] == pytest.approx(3728.3047959127844)
    assert result["candidate"]["valid"]
    assert len(detour["obstacles"]) == 36
    assert detour["obstacles"] == baseline["obstacles"]
    assert detour["rules"]["clearance"] == baseline["rules"]["clearance"] == 50
    assert detour["protected_endpoints"] == baseline["protected_endpoints"]
    assert detour["benchmark"]["baseline_layout"] == baseline
    baseline_bytes = json.dumps(baseline, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
    assert detour["benchmark"]["source_layout_sha256"] == hashlib.sha256(baseline_bytes).hexdigest()
    for name in ("baseline.json", "detour.json", "result.json"):
        text = (folder / name).read_text()
        assert "/Users/" not in text
        assert '"cad_id"' not in text


def test_controlled_cad_report_shows_true_baseline_and_fixed_sections(tmp_path):
    folder = ROOT / "examples" / "real-cad-demo"
    detour = json.loads((folder / "detour.json").read_text())
    result = run_workflow(detour)
    output = tmp_path / "report.html"
    write_html(detour, result, 0.1, output)
    page = output.read_text()
    assert "CONTROLLED DETOUR TEST" in page
    assert "451 mm removed from an artificial detour" in page
    assert "Unchanged CAD baseline: 3,679 mm" in page
    assert "still 49 mm longer than that baseline" in page
    assert "not a defect found in the source drawing" in page
    assert "Assumed local search area" in page
    assert "The movable route still checks every obstacle" in page
    assert '<script src="http' not in page
    figure = make_figure(detour, result)
    fixed = [trace for trace in figure.data if trace.name == "Fixed end sections"]
    assert len(fixed) == 1
    assert fixed[0].line.color == "#ffd166"
    assert list(zip(fixed[0].x, fixed[0].y, fixed[0].z)) == [
        tuple(point) for point in detour["protected_endpoints"][0]["points"]
    ]


def test_shared_results_and_cad_export_match_the_reproducible_demo():
    folder = ROOT / "examples" / "real-cad-demo"
    report = json.loads((folder / "result.json").read_text())
    assert report["input_sha256"] == hashlib.sha256((folder / "detour.json").read_bytes()).hexdigest()
    fresh = run_workflow(report["source_layout"])
    for key in ("original", "candidate"):
        assert report["result"][key] == fresh[key]
    assert report["baseline_result"]["status"] == "unchanged"
    document = ezdxf.readfile(folder / "route-centerlines.dxf")
    audit = document.audit()
    assert not audit.errors
    assert not audit.fixes
    for key in ("original", "candidate"):
        entities = list(document.modelspace().query(f'POLYLINE[layer=="CAD_DERIVED_{key.upper()}_CENTERLINE"]'))
        assert len(entities) == 1
        assert [list(point) for point in entities[0].points()] == report["result"][key]["points"]
    fixed = list(document.modelspace().query('POLYLINE[layer=="CAD_DERIVED_FIXED_END_SECTIONS"]'))
    assert len(fixed) == 1
    assert [list(point) for point in fixed[0].points()] == report["layout"]["protected_endpoints"][0]["points"]
    notes = " ".join(entity.dxf.text for entity in document.modelspace().query("TEXT"))
    assert "CONTROLLED DETOUR TEST" in notes
