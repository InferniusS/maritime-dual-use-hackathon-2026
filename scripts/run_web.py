"""Local CAD and routing workspace. All processing stays on this computer."""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import time
import threading
from urllib.parse import parse_qs, urlparse
import uuid

from run_demo import ROOT, write_dxf, write_html
from plotly.offline import get_plotlyjs

MAX_REQUEST_BYTES = 68 * 1024 * 1024
RUN_LOCK = threading.Lock()


class Workspace:
    def __init__(self, output, cad_sample=None):
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        paths = cad_sample if isinstance(cad_sample, (list, tuple)) else [cad_sample] if cad_sample else []
        self.cad_samples = {str(i): Path(path).resolve() for i, path in enumerate(paths)}
        self.cad_sample = next(iter(self.cad_samples.values()), None)
        self.plotly = get_plotlyjs().encode("utf-8")
        manifest = json.loads((ROOT / "examples/scenarios.json").read_text())
        self.scenarios = {record["id"]: {**record, "path": ROOT / "examples" / record["layout"]} for record in manifest["scenarios"]}
        self.scenarios["gravity-drain"] = {
            "id": "gravity-drain", "title": "Drain around equipment", "description": "Maintain downhill flow while avoiding the equipment bank.",
            "path": ROOT / "examples/drain-room.json",
        }


class Handler(BaseHTTPRequestHandler):
    server_version = "PipeWorkspace/1"

    def __init__(self, *args, workspace, **kwargs):
        self.workspace = workspace
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        pass

    def send_bytes(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status, record):
        self.send_bytes(status, json.dumps(record, allow_nan=False).encode("utf-8"), "application/json; charset=utf-8")

    def fail(self, status, message):
        self.send_json(status, {"error": message})

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/config":
                profiles = json.loads((ROOT / "examples/rule-profiles.json").read_text())
                self.send_json(200, {
                    "scenarios": [{key: value for key, value in record.items() if key in ("id", "title", "description")} for record in self.workspace.scenarios.values()],
                    "rule_profiles": profiles,
                    "cad_sample_available": bool(self.workspace.cad_sample and self.workspace.cad_sample.is_file()),
                    "cad_sample_name": self.workspace.cad_sample.name if self.workspace.cad_sample else None,
                    "cad_samples": [{"id": key, "name": path.stem} for key, path in self.workspace.cad_samples.items() if path.is_file()],
                })
            elif path == "/api/layout":
                identifier = parse_qs(parsed.query).get("id", [""])[0]
                scenario = self.workspace.scenarios.get(identifier)
                if scenario is None:
                    return self.fail(404, "Unknown layout")
                self.send_json(200, json.loads(scenario["path"].read_text()))
            elif path == "/assets/plotly.min.js":
                self.send_bytes(200, self.workspace.plotly, "text/javascript; charset=utf-8")
            elif path.startswith("/downloads/"):
                parts = path.split("/")
                if len(parts) != 4 or not re_identifier(parts[2]) or parts[3] not in ("result.json", "route-centerlines.dxf", "report.html", "cad-inspection.json"):
                    return self.fail(404, "Unknown export")
                target = self.workspace.output / parts[2] / parts[3]
                if not target.is_file():
                    return self.fail(404, "Export not found")
                mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                self.send_bytes(200, target.read_bytes(), mime)
            else:
                assets = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
                if path not in assets:
                    return self.fail(404, "Page not found")
                target = ROOT / "web" / assets[path]
                mime = mimetypes.guess_type(target.name)[0] or "text/plain"
                self.send_bytes(200, target.read_bytes(), mime + "; charset=utf-8")
        except (OSError, ValueError, KeyError) as error:
            self.fail(500, str(error))

    def do_POST(self):
        # Do not accept cross-site browser requests to the local processing service.
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        if origin and origin != f"http://{host}":
            return self.fail(403, "Requests must come from this local workspace")
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.fail(415, "Send JSON input")
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_REQUEST_BYTES:
                return self.fail(413, "Input is empty or exceeds the 68 MB request limit")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Input must be an object")
        except (ValueError, UnicodeError) as error:
            return self.fail(400, str(error))
        if not RUN_LOCK.acquire(blocking=False):
            return self.fail(409, "Another calculation is running. Try again when it finishes.")
        try:
            if self.path == "/api/run":
                self.run_layout(payload)
            elif self.path in ("/api/cad-sample", "/api/import-cad"):
                self.import_cad(payload)
            elif self.path == "/api/prepare-cad-layout":
                self.prepare_cad_layout(payload)
            elif self.path == "/api/cad-network":
                from pipe_router.cad_network import inspect_network
                _, cad = self.saved_cad(payload)
                self.send_json(200, inspect_network(cad))
            elif self.path == "/api/make-detour":
                from pipe_router.benchmarks import make_detour_case
                layout = payload.get("layout")
                if not isinstance(layout, dict):
                    raise ValueError("Provide a checked layout for the detour test")
                self.send_json(200, {"layout": make_detour_case(layout, offset_mm=payload.get("offset_mm"))})
            else:
                self.fail(404, "Unknown action")
        except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
            self.fail(400, str(error))
        except Exception as error:
            self.fail(500, f"The operation could not finish: {type(error).__name__}: {error}")
        finally:
            RUN_LOCK.release()

    def run_layout(self, payload):
        from pipe_router.workflow import run_workflow
        layout = payload.get("layout")
        if not isinstance(layout, dict):
            raise ValueError("Provide a layout")
        started = time.perf_counter()
        result = run_workflow(layout)
        result["runtime_seconds"] = time.perf_counter() - started
        normalized = result["normalized_layout"]
        identifier = uuid.uuid4().hex
        folder = self.workspace.output / identifier
        folder.mkdir()
        report = {"generated_at": datetime.now(timezone.utc).isoformat(), "input_layout": layout, "layout": normalized, "result": result}
        (folder / "result.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
        write_dxf(normalized, result, folder / "route-centerlines.dxf")
        # The standalone renderer is shared with the original demo.
        display_result = {**result, "search": {**result.get("search", {}), "reason": result.get("search", {}).get("reason", "Finished the implemented checks")}}
        write_html(normalized, display_result, result.get("runtime_seconds", 0), folder / "report.html")
        self.send_json(200, {"layout": normalized, "result": result,
            "exports": {name: f"/downloads/{identifier}/{filename}" for name, filename in (
                ("json", "result.json"), ("dxf", "route-centerlines.dxf"), ("report", "report.html")
            )}})

    def import_cad(self, payload):
        from pipe_router.cad_import import inspect_cad, lightweight_preview
        source_units = payload.get("source_units", "mm")
        if source_units not in ("mm", "m", "in"):
            raise ValueError("Choose mm, m or in for source units")
        identifier = uuid.uuid4().hex
        folder = self.workspace.output / identifier
        folder.mkdir()
        if self.path == "/api/cad-sample":
            source = self.workspace.cad_samples.get(str(payload.get("sample_id", "0")))
            if source is None or not source.is_file():
                raise ValueError("No local organizer drawing was configured. Choose a DXF file instead.")
        else:
            filename = Path(payload.get("filename", "drawing.dxf")).name
            if Path(filename).suffix.lower() != ".dxf":
                raise ValueError("Choose a DXF file. Convert DWG to DXF with ODA File Converter first.")
            try:
                content = base64.b64decode(payload.get("content_base64", ""), validate=True)
            except (ValueError, TypeError) as error:
                raise ValueError("The uploaded file could not be read") from error
            if not content or len(content) > 48 * 1024 * 1024:
                raise ValueError("DXF files must be between 1 byte and 48 MB")
            source = folder / filename
            source.write_bytes(content)
        result = inspect_cad(source, source_units=source_units)
        (folder / "cad-inspection.json").write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
        preview = lightweight_preview(result)
        self.send_json(200, {"cad": preview, "cad_id": identifier, "export": f"/downloads/{identifier}/cad-inspection.json"})

    def saved_cad(self, payload):
        identifier = payload.get("cad_id")
        if not isinstance(identifier, str) or not re_identifier(identifier):
            raise ValueError("Choose a drawing imported in this workspace")
        source = self.workspace.output / identifier / "cad-inspection.json"
        if not source.is_file():
            raise ValueError("The imported drawing was not found. Import it again in this workspace.")
        return identifier, json.loads(source.read_text(encoding="utf-8"))

    def prepare_cad_layout(self, payload):
        from pipe_router.cad_layout import prepare_cad_layout

        identifier, cad = self.saved_cad(payload)
        candidate_id = payload.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise ValueError("Choose a candidate from the imported drawing")
        system_type = payload.get("system_type", "pressurized")
        if system_type not in ("pressurized", "gravity"):
            raise ValueError("system_type must be pressurized or gravity")
        reverse_flow = payload.get("reverse_flow", False)
        if not isinstance(reverse_flow, bool):
            raise ValueError("reverse_flow must be true or false")
        layout = prepare_cad_layout(
            cad, candidate_id,
            system_type=system_type,
            clearance=payload.get("clearance", 50),
            padding=payload.get("padding", 500),
            reverse_flow=reverse_flow,
            endpoint_connections=payload.get("endpoint_connections"),
            max_stub_length=payload.get("max_stub_length", 250),
        )
        layout.setdefault("cad_context", {})["cad_id"] = identifier
        self.send_json(200, {"layout": layout, "cad_id": identifier})


def re_identifier(value):
    return len(value) == 32 and all(character in "0123456789abcdef" for character in value)


def make_server(port=8767, output=None, cad_sample=None, host="127.0.0.1"):
    workspace = Workspace(output or ROOT / "data/generated/workspace", cad_sample)
    return ThreadingHTTPServer((host, port), partial(Handler, workspace=workspace))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="Interface to listen on (use 0.0.0.0 for LAN access)")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--output", type=Path, default=ROOT / "data/generated/workspace")
    parser.add_argument("--cad-sample", type=Path, action="append", help="Local DXF drawing. Repeat to add more drawings.")
    args = parser.parse_args()
    server = make_server(args.port, args.output, args.cad_sample, args.host)
    display_host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    print(f"Workspace: http://{display_host}:{server.server_port}/", flush=True)
    print("Provisional rules. Processing stays on this computer. Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
