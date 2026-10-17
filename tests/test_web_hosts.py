"""Reject DNS rebinding before serving private local drawing data."""

from http.client import HTTPConnection
import json
from pathlib import Path
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from run_web import host_is_allowed, make_server


@pytest.mark.parametrize("authority,bind_host,allowed", [
    ("127.0.0.1:8767", "127.0.0.1", True),
    ("localhost:8767", "127.0.0.1", True),
    ("LOCALHOST:8767", "localhost", True),
    ("[::1]:8767", "127.0.0.1", True),
    ("127.2.3.4:8767", "127.0.0.1", True),
    ("attacker.example:8767", "127.0.0.1", False),
    ("localhost.attacker.example", "127.0.0.1", False),
    ("192.168.1.20:8767", "127.0.0.1", False),
    ("192.168.1.20:8767", "192.168.1.20", True),
    ("192.168.1.21:8767", "192.168.1.20", False),
    ("attacker.example:8767", "0.0.0.0", False),
    ("192.168.1.20:8767", "0.0.0.0", True),
    ("[2001:db8::10]:8767", "0.0.0.0", True),
    ("localhost:8767", "0.0.0.0", True),
    ("192.168.1.20:8767", "", True),
    ("venue-pc.example:8767", "venue-pc.example", True),
    ("attacker.example:8767", "venue-pc.example", False),
    ("localhost@attacker.example", "127.0.0.1", False),
    ("attacker.example@localhost", "127.0.0.1", False),
    ("localhost:invalid", "127.0.0.1", False),
    ("localhost:99999", "127.0.0.1", False),
    ("localhost:0", "127.0.0.1", False),
    ("localhost:", "127.0.0.1", False),
    ("localhost/path", "127.0.0.1", False),
    ("localhost?query", "127.0.0.1", False),
    ("localhost#fragment", "127.0.0.1", False),
    (" localhost", "127.0.0.1", False),
    ("localhost\t", "127.0.0.1", False),
    ("[::1", "127.0.0.1", False),
    ("", "127.0.0.1", False),
])
def test_bind_address_controls_allowed_request_hosts(authority, bind_host, allowed):
    assert host_is_allowed(authority, bind_host) is allowed


@pytest.fixture(scope="module")
def host_server(tmp_path_factory):
    server = make_server(port=0, output=tmp_path_factory.mktemp("host-check"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    assert not thread.is_alive()


def raw_request(server, method, path, hosts, *, origin=None):
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    try:
        connection.putrequest(method, path, skip_host=True)
        for host in hosts:
            connection.putheader("Host", host)
        if origin:
            connection.putheader("Origin", origin)
        if method == "POST":
            # Invalid Host must be rejected before reading this missing body.
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", "100")
        connection.endheaders()
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


@pytest.mark.parametrize("method,path", [("GET", "/api/config"), ("GET", "/"), ("POST", "/api/autocad/inspect")])
def test_rebinding_host_is_rejected_even_with_matching_origin(host_server, method, path):
    authority = f"attacker.example:{host_server.server_port}"
    status, result = raw_request(host_server, method, path, [authority], origin="http://" + authority)
    assert status == 403
    assert "address" in result["error"]


@pytest.mark.parametrize("hosts", [[], ["localhost", "attacker.example"], ["localhost", "localhost"]])
def test_missing_or_duplicate_host_does_not_serve_data(host_server, hosts):
    status, result = raw_request(host_server, "GET", "/api/config", hosts)
    assert status == 403


def test_native_client_without_origin_still_reads_health(host_server):
    status, result = raw_request(host_server, "GET", "/api/health", [f"127.0.0.1:{host_server.server_port}"])
    assert status == 200
    assert result["service"] == "maritime-pipe-router"
