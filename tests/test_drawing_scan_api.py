"""Native full scans import real DXF, retain all rows and survive cancellation."""
import hashlib
import json
import threading

import ezdxf
import pytest

from test_autocad import autocad_workspace, solid_snapshot, payload_for
from test_cad_import import cylinder_sat
from test_system_api import wait_job
from test_web import json_request, request
from run_web import RUN_LOCK


def test_all_dxf_sections_are_scanned_exported_and_available_for_preparation(tmp_path, autocad_workspace):
    source = tmp_path / "all-sections.dxf"
    drawing = ezdxf.new("R2010")
    drawing.units = ezdxf.units.MM
    handles = []
    for n in range(57):
        solid = drawing.modelspace().add_3dsolid()
        solid.sat = cylinder_sat(translation=(n * 2000, 0, 0))
        handles.append(solid.dxf.handle)
    drawing.saveas(source)
    payload = payload_for(source, [])
    status, pending = json_request(autocad_workspace, "POST", "/api/autocad/scan", payload)
    assert status == 202, pending
    assert pending["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert pending["document"] == payload["document"]
    job = wait_job(autocad_workspace, pending["status_url"])
    assert job["status"] == "complete", job
    summary = job["summary"]
    assert summary["review"]["total_supported_routes"] == 57
    assert summary["review"]["processed_routes"] == 57
    assert summary["review"]["reviewed_routes"] == 57
    assert summary["review"]["omitted_from_list"] == 0
    assert {handle for row in summary["candidates"] for handle in row["source_handles"]} == set(handles)
    assert {row["number"] for row in summary["candidates"]} == set(range(1, 58))
    assert not summary["review"]["complete_model_verified"]
    assert summary["source_sha256"] == pending["source_sha256"]
    for path in job["exports"].values():
        assert request(autocad_workspace, "GET", path)[0] == 200
    _, saved = json_request(autocad_workspace, "GET", job["exports"]["json"])
    assert saved == summary
    status, prepared = json_request(autocad_workspace, "POST", "/api/prepare-cad-layout", {
        "cad_id": pending["cad_id"], "candidate_id": summary["candidates"][-1]["id"], "clearance": 50.8})
    assert status == 200, prepared
    assert prepared["layout"]["cad_context"]["source_snapshot_sha256"] == pending["source_sha256"]
    cached = json.loads((autocad_workspace["output"] / pending["cad_id"] / "cad-inspection.json").read_text())
    assert cached["autocad_context"]["scope"] == "drawing_scan"


def test_scan_cancellation_is_reachable_while_lock_held_and_preserves_inventory(autocad_workspace, solid_snapshot, monkeypatch):
    import pipe_router.drawing_scan as scanner
    entered, release = threading.Event(), threading.Event()
    original = scanner.scan_drawing
    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(scanner, "scan_drawing", delayed)
    _, payload = solid_snapshot
    try:
        _, pending = json_request(autocad_workspace, "POST", "/api/autocad/scan", payload)
        assert entered.wait(2)
        assert json_request(autocad_workspace, "POST", "/api/autocad/scan", payload)[0] == 409
        assert json_request(autocad_workspace, "GET", pending["status_url"])[0] == 200
        status, stopped = json_request(autocad_workspace, "POST", pending["status_url"] + "/cancel", {})
        assert status == 200 and stopped["cancel_requested"]
    finally:
        release.set()
    job = wait_job(autocad_workspace, pending["status_url"])
    assert job["status"] == "complete", job
    assert job["summary"]["review"]["status"] == "cancelled"
    assert job["summary"]["review"]["not_processed_routes"] == 1
    assert not job["summary"]["candidates"][0]["geometry_checked"]
    assert not RUN_LOCK.locked()


def test_failed_scan_is_visible_and_does_not_leave_calculation_locked(autocad_workspace, solid_snapshot, monkeypatch):
    import pipe_router.drawing_scan as scanner
    def fail(*args, **kwargs):
        raise ValueError("Unsupported fixture")
    monkeypatch.setattr(scanner, "scan_drawing", fail)
    _, payload = solid_snapshot
    status, pending = json_request(autocad_workspace, "POST", "/api/autocad/scan", payload)
    assert status == 202
    job = wait_job(autocad_workspace, pending["status_url"])
    assert job["status"] == "failed" and "Unsupported fixture" in job["error"]
    assert not RUN_LOCK.locked()


@pytest.mark.parametrize("suffix", ["unknown", "../result.json", "a" * 32])
def test_unknown_scan_ids_and_cancel_ids_do_not_reach_files(autocad_workspace, suffix):
    assert request(autocad_workspace, "GET", "/api/drawing-jobs/" + suffix)[0] == 404
    assert json_request(autocad_workspace, "POST", "/api/drawing-jobs/" + suffix + "/cancel", {})[0] == 404
