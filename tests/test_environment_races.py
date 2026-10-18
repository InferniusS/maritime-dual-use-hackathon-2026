"""Deterministic real-service interleavings around hypothetical edits."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import threading
import time

import pytest

from pipe_router.network_service import NetworkRequestError, NetworkWorkspace
from test_network_api import network


def _wall(source):
    return {"network":source,"operation":{"action":"add","obstacle_id":"race-review-wall","obstacle":{
        "id":"race-review-wall","kind":"wall","simulation":True,"editable":True,"fixed":False,"protected":False,
        "min":[900,-200,-200],"max":[1100,200,200]}}}


def test_environment_edit_during_bundle_serialization_cannot_publish_approval(tmp_path, monkeypatch, network):
    import pipe_router.network_exports as module
    workspace = NetworkWorkspace(tmp_path)
    entered, release = threading.Event(), threading.Event()
    real_export = module.export_network
    def paused_export(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return real_export(*args, **kwargs)
    try:
        calculated = workspace.dispatch("optimize",{"network":network,"max_evaluations":1})
        monkeypatch.setattr(module,"export_network",paused_export)
        payload = {"network":calculated["network"],"approved":True,"review_token":calculated["review_token"]}
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(workspace.review,payload)
            assert entered.wait(5), "Real export did not reach its serialization boundary"
            workspace.dispatch("obstacles/edit",_wall(network))
            release.set()
            with pytest.raises(NetworkRequestError) as error:
                future.result(10)
            assert error.value.status == 409
        assert calculated["review_token"] not in workspace.reviews
    finally:
        release.set()
        workspace.close()


def test_edit_during_sync_display_preparation_cannot_return_stale_calculation(tmp_path, monkeypatch, network):
    workspace = NetworkWorkspace(tmp_path)
    entered, release = threading.Event(), threading.Event()
    real_geometry = workspace.display_geometry
    def paused_geometry(source):
        if source["id"] == network["id"] and source.get("environment_revision",0) == 0:
            entered.set()
            assert release.wait(10)
        return real_geometry(source)
    monkeypatch.setattr(workspace,"display_geometry",paused_geometry)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(workspace.dispatch,"optimize",{"network":network,"max_evaluations":1})
            assert entered.wait(5)
            workspace.dispatch("obstacles/edit",_wall(network))
            release.set()
            with pytest.raises(NetworkRequestError) as error:
                future.result(10)
            assert error.value.status == 409
        assert not workspace.reviews
        assert workspace.active == 0
    finally:
        release.set()
        workspace.close()


def test_environment_cancellation_before_remember_reports_cancelled_without_token(tmp_path, monkeypatch, network):
    workspace = NetworkWorkspace(tmp_path)
    entered, release = threading.Event(), threading.Event()
    real_remember = workspace._remember
    def paused_remember(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return real_remember(*args, **kwargs)
    monkeypatch.setattr(workspace,"_remember",paused_remember)
    try:
        job = workspace.dispatch("jobs",{"network":network,"max_evaluations":1})
        assert entered.wait(5)
        workspace.dispatch("obstacles/edit",_wall(network))
        release.set()
        deadline = time.monotonic()+10
        while time.monotonic() < deadline:
            state = workspace.job(job["id"])
            if state["status"] not in ("queued","running"):
                break
            time.sleep(.01)
        assert state["status"] == "cancelled", state
        assert state["response"] is None
        assert not workspace.reviews
    finally:
        release.set()
        workspace.close()


def test_edit_in_another_drawing_does_not_revoke_valid_same_named_network(tmp_path, network):
    workspace = NetworkWorkspace(tmp_path)
    other = deepcopy(network)
    other["drawing"]["id"] = "other-synthetic-drawing"
    try:
        calculated = workspace.dispatch("optimize",{"network":network,"max_evaluations":1})
        workspace.dispatch("obstacles/edit",_wall(other))
        assert calculated["review_token"] in workspace.reviews
        result = workspace.review({"network":calculated["network"],"approved":True,"review_token":calculated["review_token"]})
        assert result["approved"] is True
        assert result["exports"]
    finally:
        workspace.close()
