"""Local request logs distinguish processing failures from disconnected clients."""

from http.client import HTTPConnection, IncompleteRead
import json
from pathlib import Path
import re
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from run_web import Handler, RUN_LOCK, make_server


@pytest.fixture
def diagnostic_server(tmp_path, monkeypatch):
    finished = threading.Event()
    original = Handler.do_POST

    def observed_post(handler):
        try:
            original(handler)
        finally:
            finished.set()

    monkeypatch.setattr(Handler, "do_POST", observed_post)
    server = make_server(port=0, output=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, finished
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    assert not thread.is_alive()


def post(server_state, payload, *, path="/api/run", extra_headers=None):
    server, finished = server_state
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    try:
        connection.request("POST", path, json.dumps(payload), {
            "Content-Type": "application/json", **(extra_headers or {}),
        })
        response = connection.getresponse()
        result = response.read()
        assert finished.wait(5)
        return response.status, json.loads(result)
    finally:
        connection.close()


def test_success_logs_path_status_and_elapsed_without_payload(diagnostic_server, monkeypatch, capsys):
    monkeypatch.setattr(Handler, "run_layout", lambda handler, payload: handler.send_json(200, {"ok": True}))
    status, result = post(diagnostic_server, {"private_drawing": "coordinates 1234.5678"})
    assert (status, result) == (200, {"ok": True})
    output = capsys.readouterr().out
    assert re.search(r"POST /api/run received status=pending elapsed=\d+\.\d+s", output)
    assert re.search(r"POST /api/run finished status=200 elapsed=\d+\.\d+s", output)
    assert "private_drawing" not in output
    assert "1234.5678" not in output


def test_real_runs_explicitly_close_and_repeated_client_requests_succeed(diagnostic_server):
    server, finished = diagnostic_server
    layout = json.loads((ROOT / "examples/scenarios/equipment-detour.json").read_text())
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    exports = set()
    try:
        for _ in range(3):
            finished.clear()
            connection.request("POST", "/api/run", json.dumps({"layout": layout}), {
                "Content-Type": "application/json", "Connection": "keep-alive",
            })
            response = connection.getresponse()
            assert response.status == 200
            assert response.getheader("Connection") == "close"
            assert response.will_close
            body = response.read()
            assert len(body) == int(response.getheader("Content-Length"))
            result = json.loads(body)
            assert result["result"]["status"] == "improved"
            assert result["result"]["candidate"]["valid"]
            assert result["result"]["candidate"]["length"] == 8000
            exports.add(result["exports"]["json"])
            assert finished.wait(5)
        assert len(exports) == 3
    finally:
        connection.close()


@pytest.mark.parametrize("error,status", [(ValueError("private coordinate 1234.5678"), 400),
                                        (ArithmeticError("private coordinate 1234.5678"), 500)])
def test_processing_error_logs_type_and_stack_without_cad_values(diagnostic_server, monkeypatch, capsys, error, status):
    def failed_operation(handler, payload):
        raise error

    monkeypatch.setattr(Handler, "run_layout", failed_operation)
    response_status, result = post(diagnostic_server, {"layout": {}})
    assert response_status == status
    assert "private coordinate" in result["error"]
    output = capsys.readouterr().out
    assert f"type={type(error).__name__}" in output
    assert "test_web_diagnostics.py:" in output
    assert "failed_operation" in output
    assert f"failed status={status}" in output
    assert "private coordinate" not in output
    assert "1234.5678" not in output
    assert RUN_LOCK.acquire(blocking=False)
    RUN_LOCK.release()


@pytest.mark.parametrize("headers,status", [({"Origin": "https://other.example"}, 403),
                                          ({"Host": "other.example"}, 403),
                                          ({"Content-Type": "text/plain"}, 415)])
def test_rejected_input_is_logged_without_running_calculation(diagnostic_server, monkeypatch, capsys, headers, status):
    def unexpected_operation(handler, payload):
        pytest.fail("Rejected input reached the calculation")

    monkeypatch.setattr(Handler, "run_layout", unexpected_operation)
    response_status, _ = post(diagnostic_server, {"layout": {}}, extra_headers=headers)
    assert response_status == status
    output = capsys.readouterr().out
    assert "received" in output
    assert f"failed status={status}" in output


def test_client_disconnect_after_headers_does_not_send_another_response(diagnostic_server, monkeypatch, capsys):
    original = Handler.send_bytes
    attempts = []

    class DisconnectedBodyWriter:
        def __init__(self, writer):
            self.writer = writer

        def write(self, data):
            if data.startswith(b"HTTP/"):
                return self.writer.write(data)
            raise BrokenPipeError(32, "Client closed its connection")

        def __getattr__(self, name):
            return getattr(self.writer, name)

    def interrupted_response(handler, status, body, content_type):
        attempts.append(status)
        handler.wfile = DisconnectedBodyWriter(handler.wfile)
        original(handler, status, body, content_type)

    monkeypatch.setattr(Handler, "send_bytes", interrupted_response)
    monkeypatch.setattr(Handler, "run_layout", lambda handler, payload: handler.send_json(200, {"ok": True}))
    with pytest.raises(IncompleteRead):
        post(diagnostic_server, {"layout": {}})
    assert diagnostic_server[1].wait(5)
    assert attempts == [200]
    output = capsys.readouterr().out
    assert "type=BrokenPipeError errno=32" in output
    assert "failed status=200" in output
    assert "finished status=200" not in output
    assert RUN_LOCK.acquire(blocking=False)
    RUN_LOCK.release()


def test_unknown_action_logs_path_without_query(diagnostic_server, capsys):
    status, _ = post(diagnostic_server, {}, path="/unknown?drawing=private123")
    assert status == 404
    output = capsys.readouterr().out
    assert "POST /unknown" in output
    assert "failed status=404" in output
    assert "private123" not in output
