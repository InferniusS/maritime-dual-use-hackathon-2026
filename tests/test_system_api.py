"""The native client can poll a traceable job and export all section outcomes."""
from copy import deepcopy
from io import StringIO
import json
import threading
import time
from zipfile import ZipFile

import ezdxf
import pytest

from pipe_router.system_optimization import optimize_sections
from pipe_router.system_export import write_system_review
from test_system_optimization import crossing_drawing, selections, drawing
from test_autocad import autocad_workspace, solid_snapshot
from test_web import json_request, request
from run_web import RUN_LOCK


def wait_job(workspace, path):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        status, job = json_request(workspace, "GET", path)
        assert status == 200, job
        if job["status"] in ("complete", "failed"):
            return job
        time.sleep(.02)
    pytest.fail("Job did not finish")


def submit_source(workspace, payload):
    _, inspected = json_request(workspace, "POST", "/api/autocad/review", payload)
    return {"cad_id": inspected["cad_id"], "source_sha256": inspected["source_sha256"],
            "document": payload["document"], "sections": [{"candidate_id": inspected["candidates"][0]["id"],
            "clearance": 0, "padding": 100, "grid_step": 10, "bend_radius_mm": 2}]}


def test_async_snapshot_job_has_traceable_results_and_downloads(autocad_workspace, solid_snapshot):
    _, payload = solid_snapshot
    body = submit_source(autocad_workspace, payload)
    status, pending = json_request(autocad_workspace, "POST", "/api/autocad/optimize", body)
    assert status == 202, pending
    job = wait_job(autocad_workspace, pending["status_url"])
    assert job["status"] == "complete", job
    assert job["source_sha256"] == body["source_sha256"]
    assert job["document"] == body["document"]
    assert job["summary"]["coverage"]["requested_sections"] == 1
    row = job["summary"]["routes"][0]
    assert row["layout"]["cad_context"]["cad_id"] == body["cad_id"]
    assert not job["summary"]["complete_system_verified"]
    for path in job["exports"].values():
        assert request(autocad_workspace, "GET", path)[0] == 200
    _, saved = json_request(autocad_workspace, "GET", job["exports"]["json"])
    assert saved == job["summary"]


@pytest.mark.parametrize("field,value", [("source_sha256", "wrong"), ("document", {"fingerprint": "other"}),
    ("sections", []), ("sections", [{"candidate_id": "x"}]*13)])
def test_mismatched_source_or_invalid_selection_stops_before_worker(autocad_workspace, solid_snapshot, field, value):
    _, payload = solid_snapshot
    body = submit_source(autocad_workspace, payload)
    body[field] = value
    status, error = json_request(autocad_workspace, "POST", "/api/autocad/optimize", body)
    assert status == 400, error
    assert not RUN_LOCK.locked()


def test_job_poll_remains_available_while_new_calculation_is_blocked(autocad_workspace, solid_snapshot, monkeypatch):
    import pipe_router.system_optimization as system
    _, payload = solid_snapshot
    body = submit_source(autocad_workspace, payload)
    entered, finish = threading.Event(), threading.Event()
    original = system.optimize_sections
    def delayed(*args, **kwargs):
        entered.set()
        assert finish.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(system, "optimize_sections", delayed)
    try:
        _, job = json_request(autocad_workspace, "POST", "/api/autocad/optimize", body)
        assert entered.wait(2)
        assert json_request(autocad_workspace, "GET", job["status_url"])[0] == 200
        assert json_request(autocad_workspace, "POST", "/api/autocad/optimize", body)[0] == 409
    finally:
        finish.set()
    assert wait_job(autocad_workspace, job["status_url"])["status"] == "complete"


def test_worker_failure_is_visible_and_releases_lock(autocad_workspace, solid_snapshot, monkeypatch):
    import pipe_router.system_optimization as system
    _, payload = solid_snapshot
    body = submit_source(autocad_workspace, payload)
    def fail(*args, **kwargs):
        raise RuntimeError("fixture failure")
    monkeypatch.setattr(system, "optimize_sections", fail)
    _, pending = json_request(autocad_workspace, "POST", "/api/autocad/optimize", body)
    job = wait_job(autocad_workspace, pending["status_url"])
    assert job["status"] == "failed"
    assert "fixture failure" in job["error"]
    assert not RUN_LOCK.locked()


def test_combined_exports_keep_source_identity_and_only_accepted_proposals(tmp_path):
    summary = optimize_sections(crossing_drawing(), selections(2))
    summary["routes"].append({"index": 3, "candidate_id": "missing", "source_handles": ["=DANGEROUS()"],
        "label": "<script>bad()</script>", "message": "Explicitly unavailable", "status": "needs_review"})
    before = deepcopy(summary)
    write_system_review(summary, tmp_path)
    assert summary == before
    model = ezdxf.readfile(tmp_path / "route-centerlines.dxf")
    assert not model.audit().errors
    assert model.units == ezdxf.units.MM
    assert "SECTION_03_PROPOSAL" not in model.layers
    for row in summary["routes"][:2]:
        if row["status"] in ("proposed", "unchanged"):
            faces = list(model.modelspace().query(f'3DFACE[layer=="SECTION_{row["index"]:02d}_PROPOSAL"]'))
            assert faces and faces[0].get_xdata("MARITIME_REVIEW")
    page = (tmp_path / "report.html").read_text()
    assert "<script>bad()</script>" not in page
    assert "&lt;script&gt;" in page and "Explicitly unavailable" in page
    assert "plotly.js" in page
    with ZipFile(tmp_path / "route-review.xlsx") as book:
        sheets = ''.join(book.read(name).decode() for name in book.namelist() if name.startswith("xl/worksheets/"))
        assert "=DANGEROUS()" in sheets and "<f>" not in sheets
        assert "Start XYZ mm" in sheets and "Full system verified" in sheets


def test_unknown_job_and_path_traversal_do_not_access_files(autocad_workspace):
    for path in ("/api/system-jobs/../result.json", "/api/system-jobs/" + "a"*32):
        assert request(autocad_workspace, "GET", path)[0] == 404
