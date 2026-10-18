"""Local connected-network workflow shared by the two existing HTTP servers.

Review tokens refer to server-held results. Uploaded result dictionaries can never
be substituted for the geometry that was independently checked by the server.
"""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
MAX_NETWORK_REQUEST_BYTES = 2 * 1024 * 1024
EXPORT_NAMES = frozenset(("instructions.json", "instructions.md", "components.xlsx", "components.csv", "network-preview.dxf", "network-result.json"))


class NetworkRequestError(ValueError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


class NetworkWorkspace:
    """Bounded, in-memory job/review state; source files are never modified."""
    def __init__(self, output):
        self.output = Path(output)
        self.lock = threading.RLock()
        self.reviews = {}
        self.jobs = {}
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="connected-network")
        self.active = 0

    def close(self):
        with self.lock:
            for job in self.jobs.values():
                job["_cancel"].set()
        self.executor.shutdown(wait=False, cancel_futures=True)

    def examples(self):
        result = []
        for path in sorted((ROOT / "examples" / "connected").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            result.append({"id": path.stem, "name": data.get("name", data.get("id", path.stem)), "description": data.get("description", "Synthetic connected engineering fixture")})
        return result

    def example(self, identifier):
        # Match known stems rather than interpreting any client-controlled path.
        for path in sorted((ROOT / "examples" / "connected").glob("*.json")):
            if path.stem == identifier:
                return json.loads(path.read_text(encoding="utf-8"))
        raise NetworkRequestError(404, "Unknown synthetic network")

    def _network(self, payload):
        from pipe_router.network_model import normalize_network
        raw = payload.get("network")
        if not isinstance(raw, dict):
            raise NetworkRequestError(422, "Provide a connected network object")
        return normalize_network(raw)

    def _trim(self):
        now = time.monotonic()
        for token, record in list(self.reviews.items()):
            if now - record["created"] > 3600:
                del self.reviews[token]
        while len(self.reviews) >= 8:
            del self.reviews[next(iter(self.reviews))]
        for identifier, record in list(self.jobs.items()):
            if len(self.jobs) < 8:
                break
            if record["status"] not in ("queued", "running"):
                del self.jobs[identifier]

    @staticmethod
    def display_geometry(network):
        """Finite physical occupancy used by validation; never a commercial solid.

        The visualization has its own bounded payload budget. Any truncation is
        declared and does not change independent validation of the full network.
        """
        from pipe_router.network_fittings import fitting_envelopes, span_occupied_segments
        spans, count, complete = [], 0, True
        for span in network["spans"]:
            fitting = fitting_envelopes(span)
            occupied = span_occupied_segments(span)
            remaining = max(0, 2000-count)
            segments = occupied[:remaining]
            if len(segments) < len(occupied):
                complete = False
            count += len(segments)
            spans.append({"id":span["id"], "cad_handle":span.get("cad_handle"), "segments":segments,
                          "fittings":[{key:value for key,value in item.items() if key != "capsules"} for item in fitting["fittings"]],
                          "catalog_status":span.get("fitting_catalog",{}).get("status","missing"), "issues":fitting["issues"]})
        return {"spans":spans,"complete":complete,"segment_count":count,
                "scope":"Conservative validation capsules for declared pipe and fitting dimensions; no commercial-solid approval", 
                "notes":[] if complete else ["Physical visualization truncated at 2000 occupancy segments; centerlines and full validation remain available"]}

    def _remember(self, network, result):
        from pipe_router.connected import network_digest
        token = uuid.uuid4().hex
        with self.lock:
            self._trim()
            self.reviews[token] = {"created": time.monotonic(), "network": copy.deepcopy(network), "result": copy.deepcopy(result), "digest": network_digest(network), "revision": network.get("drawing", {}).get("revision")}
        return {"network": network, "result": result, "review_token": token,
                "geometry":{"original":self.display_geometry(network),"proposed":self.display_geometry(result["proposed_network"]) if result.get("proposed_network") else None}}

    @staticmethod
    def _budget(payload):
        value = payload.get("max_evaluations", 24)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 64:
            raise NetworkRequestError(422, "max_evaluations must be an integer from 1 to 64")
        return value

    def _claim(self):
        with self.lock:
            if self.active:
                raise NetworkRequestError(409, "A connected-network calculation is already active")
            self.active += 1

    def _release(self):
        with self.lock:
            self.active -= 1

    def inspect(self, payload):
        from pipe_router.network_model import reconstruct_network
        from pipe_router.network_validation import validate_network
        if "cad" in payload:
            if not isinstance(payload["cad"], dict):
                raise NetworkRequestError(422, "Provide a CAD inspection object")
            cad = copy.deepcopy(payload["cad"])
            bounds = payload.get("bounds", cad.get("permitted_bounds"))
            if bounds is None:
                raise NetworkRequestError(422, "Declare permitted ship bounds; sampled drawing extents do not define routing permission")
            cad["bounds"] = copy.deepcopy(bounds)
            if payload.get("coordinate_system") is not None:
                cad["coordinate_system"] = copy.deepcopy(payload["coordinate_system"])
            if payload.get("drawing") is not None:
                drawing = payload["drawing"]
                if not isinstance(drawing, dict) or not isinstance(drawing.get("id"), str) or not isinstance(drawing.get("revision"), str):
                    raise NetworkRequestError(422, "Supply explicit drawing id and revision strings")
                if len(drawing["id"]) > 256 or len(drawing["revision"]) > 256:
                    raise NetworkRequestError(422, "Drawing identifiers are too long")
                cad["drawing"] = copy.deepcopy(drawing)
            handles = payload.get("selected_handles")
            if handles is not None:
                from pipe_router.autocad import _matches_selection, MAX_SELECTED_HANDLES
                import re
                if not isinstance(handles, list) or not 1 <= len(handles) <= MAX_SELECTED_HANDLES or not all(isinstance(h, str) and re.fullmatch(r"[0-9a-fA-F]{1,16}", h) and int(h, 16) for h in handles):
                    raise NetworkRequestError(422, "Provide valid nonzero selected CAD handles")
                selected = {format(int(h, 16), "X") for h in handles}
                cad["route_candidates"] = [candidate for candidate in cad.get("route_candidates", []) if _matches_selection(candidate, selected)]
                if not cad["route_candidates"]:
                    raise NetworkRequestError(422, "No traceable route candidates match the selected handles")
            network = reconstruct_network(cad, tolerance_mm=payload.get("tolerance_mm", 1))
        else:
            network = self._network(payload)
        return {"network": network, "validation": validate_network(network), "geometry":self.display_geometry(network)}

    def dispatch(self, action, payload):
        if not isinstance(payload, dict):
            raise NetworkRequestError(422, "Input must be an object")
        if action == "inspect":
            return self.inspect(payload)
        if action == "confirm":
            from pipe_router.network_model import confirm_connections
            from pipe_router.network_validation import validate_network
            network = confirm_connections(self._network(payload), payload.get("decisions", []))
            return {"network": network, "validation": validate_network(network), "geometry":self.display_geometry(network)}
        if action == "validate":
            from pipe_router.network_validation import validate_network
            reference = self._network({"network": payload["reference"]}) if payload.get("reference") is not None else None
            from pipe_router.connected import network_digest
            network = self._network(payload)
            return {"network": network, "validation": validate_network(network, reference=reference), "source_digest": network_digest(reference if reference is not None else network)}
        if action == "optimize":
            from pipe_router.connected import optimize_network
            network, budget = self._network(payload), self._budget(payload)
            self._claim()
            try:
                return self._remember(network, optimize_network(network, max_evaluations=budget))
            finally:
                self._release()
        if action == "jobs":
            return self.start_job(payload)
        if action.startswith("jobs/") and action.endswith("/cancel"):
            identifier = action.split("/")[1]
            with self.lock:
                job = self.jobs.get(identifier)
                if job is None:
                    raise NetworkRequestError(404, "Job not found")
                if job["status"] not in ("queued", "running"):
                    return {"status": job["status"], "cancellation_requested": False}
                job["_cancel"].set()
            return {"status": "cancellation_requested"}
        if action in ("review", "export"):
            return self.review(payload)
        raise NetworkRequestError(404, "Unknown network action")

    def start_job(self, payload):
        from pipe_router.connected import optimize_network
        network, budget = self._network(payload), self._budget(payload)
        self._claim()
        identifier = uuid.uuid4().hex
        job = {"id": identifier, "status": "queued", "created": time.monotonic(), "elapsed_seconds": 0, "_cancel": threading.Event(), "response": None, "error": None}
        with self.lock:
            self._trim()
            self.jobs[identifier] = job
        def run():
            job["status"] = "running"
            started = time.perf_counter()
            try:
                result = optimize_network(network, max_evaluations=budget, cancelled=job["_cancel"].is_set)
                if job["_cancel"].is_set():
                    job["status"] = "cancelled"
                else:
                    job["response"] = self._remember(network, result)
                    job["status"] = "complete"
            except Exception as error:
                # A response to the current local client may describe validation;
                # no source geometry is written to logs or telemetry.
                job["error"] = str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__
                job["status"] = "failed"
            finally:
                job["elapsed_seconds"] = time.perf_counter() - started
                self._release()
        self.executor.submit(run)
        return {"id": identifier, "status": "queued"}

    def job(self, identifier):
        with self.lock:
            job = self.jobs.get(identifier)
            if job is None:
                raise NetworkRequestError(404, "Job not found")
            return {key: value for key, value in job.items() if not key.startswith("_") and key != "created"}

    def review(self, payload):
        from pipe_router.connected import network_digest
        from pipe_router.network_exports import export_network
        if payload.get("approved") is not True:
            raise NetworkRequestError(409, "Explicit designer approval is required to export implementation instructions")
        network = self._network(payload)
        token = payload.get("review_token")
        with self.lock:
            record = self.reviews.get(token) if isinstance(token, str) else None
            if record is None or time.monotonic() - record["created"] > 3600:
                raise NetworkRequestError(409, "Review expired. Calculate and review the network again")
            if network_digest(network) != record["digest"] or network.get("drawing", {}).get("revision") != record["revision"]:
                raise NetworkRequestError(409, "Source geometry or drawing revision changed. Calculate and review again")
            result = copy.deepcopy(record["result"])
        if payload.get("proposal_id") not in (None, result.get("proposal_id")):
            raise NetworkRequestError(409, "The reviewed proposal does not match this calculation")
        identifier = uuid.uuid4().hex
        folder = self.output / identifier
        folder.mkdir(parents=True)
        exports = export_network(network, result, folder)
        # A pure filename mapping is returned; adapters control the URL prefix.
        files = {name: filename for name, filename in exports.items() if isinstance(filename, str) and Path(filename).name in EXPORT_NAMES}
        if not files:
            files = {name: name for name in sorted(EXPORT_NAMES) if (folder / name).is_file()}
        return {"approved": True, "native_applied": False, "proposal_id": result.get("proposal_id"), "export_id": identifier, "exports": {name: f"/api/network/downloads/{identifier}/{Path(filename).name}" for name, filename in files.items()}}

    def download(self, identifier, filename):
        if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier) or filename not in EXPORT_NAMES:
            raise NetworkRequestError(404, "Unknown network export")
        target = self.output / identifier / filename
        if not target.is_file():
            raise NetworkRequestError(404, "Network export not found")
        return target
