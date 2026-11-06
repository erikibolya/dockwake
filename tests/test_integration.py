from __future__ import annotations

import json
import socket
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dockwake.launcher import (  # noqa: E402
    DockerProxyClient,
    Launcher,
    TargetApiClient,
    TargetUiClient,
)
from dockwake.main import Config, create_server  # noqa: E402


class FakeDockerHandler(BaseHTTPRequestHandler):
    running = False
    started_at = 0.0
    start_count = 0
    stop_count = 0
    lock = threading.Lock()

    def do_POST(self) -> None:  # noqa: N802
        with self.lock:
            if self.path.startswith("/containers/app/start"):
                type(self).running = True
                type(self).started_at = time.time()
                type(self).start_count += 1
                self.send_response(204)
            elif self.path.startswith("/containers/app/stop"):
                type(self).running = False
                type(self).stop_count += 1
                self.send_response(204)
            else:
                self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def log_message(self, fmt: str, *args) -> None:
        return


class FakeAppHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        with FakeDockerHandler.lock:
            ready = FakeDockerHandler.running and time.time() - FakeDockerHandler.started_at >= 0.3
        if self.path.startswith("/api/ready"):
            if ready:
                body = b"ready"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
            return
        if self.path == "/":
            if ready:
                body = b"<html><body>Fake app</body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt: str, *args) -> None:
        return


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class IntegrationTests(unittest.TestCase):
    session = "integration-1234"

    def setUp(self) -> None:
        FakeDockerHandler.running = False
        FakeDockerHandler.started_at = 0.0
        FakeDockerHandler.start_count = 0
        FakeDockerHandler.stop_count = 0

        self.docker_server = ThreadingHTTPServer(("127.0.0.1", 0), FakeDockerHandler)
        self.app_server = ThreadingHTTPServer(("127.0.0.1", 0), FakeAppHandler)
        for server in (self.docker_server, self.app_server):
            server.daemon_threads = True
            threading.Thread(target=server.serve_forever, daemon=True).start()

        self.docker_port = self.docker_server.server_address[1]
        self.app_port = self.app_server.server_address[1]
        self.app_url = f"http://127.0.0.1:{self.app_port}/"

        self.launcher = Launcher(
            DockerProxyClient(
                f"http://127.0.0.1:{self.docker_port}",
                "app",
                stop_grace_seconds=2,
            ),
            TargetApiClient(f"http://127.0.0.1:{self.app_port}/api/ready"),
            TargetUiClient(self.app_url),
            app_name="Integration App",
            app_url=self.app_url,
            language="en",
            idle_stop_seconds=5,
            session_stale_seconds=30,
            start_timeout_seconds=30,
        )
        self.launcher.start_monitor()

        self.port = free_port()
        self.config = Config(
            port=self.port,
            app_name="Integration App",
            app_url=self.app_url,
            language="en",
            launcher_origin=f"http://127.0.0.1:{self.port}",
            target_ui_url=self.app_url,
            static_dir=Path(__file__).resolve().parents[1] / "dockwake" / "static",
            stop_grace_seconds=2,
        )
        self.server = create_server(self.launcher, self.config, host="127.0.0.1")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self) -> None:
        self.launcher.shutdown()
        self.server.shutdown()
        self.server.server_close()
        for server in (self.docker_server, self.app_server):
            server.shutdown()
            server.server_close()

    def request(self, path: str, method: str = "GET", payload: dict | None = None):
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data,
            method=method,
        )
        if data is not None:
            request.add_header("Content-Type", "application/json")
            request.add_header("X-DockWake-Client", "1")
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read()
            return response.status, (json.loads(body) if body else None)

    def wait_for_state(self, state: str, timeout: float = 15.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            _, status = self.request("/api/status")
            if status and status["state"] == state:
                return status
            time.sleep(0.2)
        self.fail(f"state {state!r} not reached; last={status}")

    def test_start_ready_and_auto_stop(self) -> None:
        status, body = self.request("/api/start", "POST", {"session": self.session})
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "starting")

        ready = self.wait_for_state("ready")
        self.assertEqual(ready["target_value"], "ready")
        self.assertTrue(ready["ui_ready"])
        self.assertEqual(FakeDockerHandler.start_count, 1)

        status, _ = self.request("/api/leave", "POST", {"session": self.session})
        self.assertEqual(status, 204)

        stopped = self.wait_for_state("stopped")
        self.assertFalse(stopped["managed"])
        self.assertEqual(FakeDockerHandler.stop_count, 1)


if __name__ == "__main__":
    unittest.main()
