"""Exercise the scenario gallery against known synthetic outcomes."""

from copy import deepcopy
import json
from math import isfinite
from pathlib import Path
import subprocess
import sys

import ezdxf
import pytest

from pipe_router.core import solve_layout


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "examples" / "scenarios.json"


def load_scenarios():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["scenarios"]


def load_layout(scenario):
    return json.loads((MANIFEST.parent / scenario["layout"]).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def solved_scenarios():
    records = {}
    for scenario in load_scenarios():
        layout = load_layout(scenario)
        original_input = deepcopy(layout)
        result = solve_layout(layout)
        assert layout == original_input, "Solving must preserve the supplied geometry and pipe diameter"
        records[scenario["id"]] = (scenario, layout, result)
    return records


def assert_continuously_clear(layout, points):
    """Check all segments independently of the production geometry validator."""
    assert points[0] == layout["pipe"]["start"]
    assert points[-1] == layout["pipe"]["end"]
    margin = layout["pipe"]["diameter"] / 2 + layout["rules"]["clearance"]
    for point in points:
        assert len(point) == 3
        assert all(isfinite(coordinate) for coordinate in point)
        for axis in range(3):
            assert layout["bounds"]["min"][axis] + margin <= point[axis]
            assert point[axis] <= layout["bounds"]["max"][axis] - margin
    for start, end in zip(points, points[1:]):
        assert sum(start[axis] != end[axis] for axis in range(3)) == 1
        for obstacle in layout["obstacles"]:
            overlaps = [
                max(start[axis], end[axis]) >= obstacle["min"][axis] - margin
                and min(start[axis], end[axis]) <= obstacle["max"][axis] + margin
                for axis in range(3)
            ]
            assert not all(overlaps), f"A continuous segment touches {obstacle['id']}"


def test_all_fixture_outcomes_match_documented_expectations(solved_scenarios):
    assert len(solved_scenarios) == 7
    assert {record[2]["status"] for record in solved_scenarios.values()} == {
        "improved", "unchanged", "no_route"
    }
    for scenario, layout, result in solved_scenarios.values():
        expected = scenario["expected"]
        assert layout["provenance"] == "synthetic"
        assert layout["units"] == "mm"
        assert layout["system_type"] == "pressurized"
        assert result["status"] == expected["status"], scenario["id"]
        assert result["original"]["valid"] is expected["original_valid"], scenario["id"]
        candidate = result["candidate"]
        if expected["candidate_valid"] is None:
            assert candidate is None, scenario["id"]
            assert expected["length_mm"] is None
            assert expected["bends"] is None
        else:
            assert candidate is not None, scenario["id"]
            assert candidate["valid"] is expected["candidate_valid"], scenario["id"]
            assert candidate["length"] == pytest.approx(expected["length_mm"]), scenario["id"]
            assert candidate["bends"] == expected["bends"], scenario["id"]
            assert_continuously_clear(layout, candidate["points"])
            changes_z = any(point[2] != candidate["points"][0][2] for point in candidate["points"])
            assert changes_z is expected["vertical_detour"], scenario["id"]


def test_same_narrow_gap_accepts_small_pipe_and_rejects_large_pipe(solved_scenarios):
    _, small, small_result = solved_scenarios["narrow-gap-small-pipe"]
    _, large, large_result = solved_scenarios["narrow-gap-large-pipe"]
    assert small["bounds"] == large["bounds"]
    assert small["obstacles"] == large["obstacles"]
    assert small["rules"] == large["rules"]
    for key in ("start", "end", "original_route"):
        assert small["pipe"][key] == large["pipe"][key]
    assert small["pipe"]["diameter"] < large["pipe"]["diameter"]
    assert small_result["candidate"] is not None
    assert small_result["candidate"]["valid"]
    assert large_result["status"] == "no_route"
    assert large_result["candidate"] is None


def test_vertical_detour_clears_obstacle_height(solved_scenarios):
    _, layout, result = solved_scenarios["vertical-detour"]
    assert layout["pipe"]["start"][2] == layout["pipe"]["end"][2]
    assert not result["original"]["valid"]
    margin = layout["pipe"]["diameter"] / 2 + layout["rules"]["clearance"]
    top = max(obstacle["max"][2] for obstacle in layout["obstacles"])
    assert max(point[2] for point in result["candidate"]["points"]) > top + margin


def test_sealed_partition_reports_failure_without_inventing_route(solved_scenarios):
    _, _, result = solved_scenarios["sealed-partition"]
    assert result["status"] == "no_route"
    assert result["candidate"] is None
    assert not result["original"]["valid"]
    assert "exhausting" in result["search"]["reason"]


def test_gallery_cli_exports_matching_results_and_centerlines(tmp_path, solved_scenarios):
    output = tmp_path / "scenario-gallery"
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_scenarios.py"), "--output", str(output)],
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["all_expectations_passed"] is True
    records = summary["scenarios"]
    assert {record["id"] for record in records} == set(solved_scenarios)
    assert len(records) == len(solved_scenarios)
    gallery = (output / "index.html").read_text(encoding="utf-8")
    assert "synthetic" in gallery.lower()
    for record in records:
        identifier = record["id"]
        scenario, layout, result = solved_scenarios[identifier]
        assert record["title"] == scenario["title"]
        assert record["description"] == scenario["description"]
        assert record["status"] == result["status"]
        assert record["expected_passed"] is True
        assert record["checks"]
        assert all(check["name"] and check["passed"] is True for check in record["checks"])
        assert isfinite(record["runtime_seconds"]) and record["runtime_seconds"] >= 0
        folder = output / identifier
        exported = json.loads((folder / "result.json").read_text(encoding="utf-8"))
        assert exported["layout"] == layout
        assert exported["result"] == result
        assert exported["layout"]["pipe"]["diameter"] == layout["pipe"]["diameter"]
        assert f"{identifier}/index.html" in gallery
        assert "SYNTHETIC TEST" in (folder / "index.html").read_text(encoding="utf-8")
        document = ezdxf.readfile(folder / "route-centerlines.dxf")
        assert document.units == ezdxf.units.MM
        for key in ("original", "candidate"):
            route = result[key]
            if route is None:
                assert record[key] is None
            else:
                for metric in ("length", "bends", "valid"):
                    assert record[key][metric] == route[metric]
            layer = "SYNTHETIC_" + key.upper() + "_CENTERLINE"
            entities = list(document.modelspace().query(f'POLYLINE[layer=="{layer}"]'))
            if route is None:
                assert not entities
            else:
                assert len(entities) == 1
                assert [list(point) for point in entities[0].points()] == route["points"]


def test_gallery_cli_fails_when_a_known_expected_result_does_not_match(tmp_path):
    scenarios = load_scenarios()
    for scenario in scenarios:
        scenario["layout"] = str((MANIFEST.parent / scenario["layout"]).resolve())
    changed = scenarios[0]
    changed["expected"]["status"] = (
        "unchanged" if changed["expected"]["status"] != "unchanged" else "improved"
    )
    manifest = tmp_path / "incorrect-expectation.json"
    manifest.write_text(json.dumps({"scenarios": scenarios}), encoding="utf-8")
    output = tmp_path / "mismatch-report"

    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_scenarios.py"),
         "--manifest", str(manifest), "--output", str(output)],
        capture_output=True, text=True, check=False,
    )

    assert completed.returncode == 1, completed.stderr
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["all_expectations_passed"] is False
    changed_record = next(record for record in summary["scenarios"] if record["id"] == changed["id"])
    assert changed_record["expected_passed"] is False
    assert any(check["passed"] is False for check in changed_record["checks"])
    assert (output / "index.html").is_file()
