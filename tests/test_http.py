from __future__ import annotations

import http.client
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dockwake.main import Config, create_server  # noqa: E402


class FakeLauncher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.state = {
            "state": "stopped",
            "target_value": None,
            "ui_ready": False,
            "managed": False,
            "sessions": 0,
            "stop_in": None,
            "error": None,
            "elapsed": None,
            "app_name": "Test App",
            "app_url": "https://app.example/",
            "app_icon_url": "",
            "language": "en",
            "stop_button_position": "top-right",
            "idle_stop_seconds": 60,
        }

    def valid_session(self, session: object) -> bool:
        return isinstance(session, str) and 8 <= len(session) <= 64

    def status(self) -> dict:
        return dict(self.state)

    def start(self, session: str) -> dict:
        self.calls.append(("start", session))
        self.state = dict(self.state, state="starting", sessions=1)
        return dict(self.state)

    def heartbeat(self, session: str) -> dict:
        self.calls.append(("heartbeat", session))
        return dict(self.state)

    def leave(self, session: str) -> dict:
        self.calls.append(("leave", session))
        return dict(self.state)

    def stop(self) -> dict:
        self.calls.append(("stop", ""))
        self.state = dict(self.state, state="stopping")
        return dict(self.state)


class HttpTests(unittest.TestCase):
    session = "session-1234"

    def setUp(self) -> None:
        self.launcher = FakeLauncher()
        self.config = Config(
            port=0,
            app_name="Test App",
            app_url="https://app.example/",
            language="en",
            launcher_origin="https://launcher.example",
            target_ui_url="https://app.example/",
            static_dir=Path(__file__).resolve().parents[1] / "dockwake" / "static",
        )
        self.server = create_server(self.launcher, self.config, host="127.0.0.1")
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path: str) -> tuple[int, dict, bytes]:
        try:
            with urllib.request.urlopen(self.url(path), timeout=5) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def post(
        self,
        path: str,
        payload: dict | None = None,
        headers: dict | None = None,
    ) -> tuple[int, dict, bytes]:
        body = json.dumps(payload or {}).encode("utf-8")
        request = urllib.request.Request(
            self.url(path),
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def test_index_is_served_with_security_headers(self) -> None:
        status, headers, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"DockWake", body)
        self.assertIn(b"Start app", body)
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")
        self.assertIn(
            "frame-src https://app.example",
            headers.get("Content-Security-Policy", ""),
        )

    def test_static_file_is_served(self) -> None:
        status, headers, body = self.get("/static/app.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers.get("Content-Type", ""))
        self.assertIn(b"heartbeat", body)
        self.assertIn(b"X-DockWake-Client", body)

    def test_static_whitelist_rejects_other_paths(self) -> None:
        status, _, _ = self.get("/static/secret.txt")
        self.assertEqual(status, 404)

    def test_path_traversal_is_rejected(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request("GET", "/static/../main.py")
        response = connection.getresponse()
        self.assertEqual(response.status, 404)
        response.read()
        connection.close()

    def test_health(self) -> None:
        status, _, body = self.get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"ok")

    def test_head_index(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request("HEAD", "/")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertGreater(int(response.getheader("Content-Length", "0")), 0)
        self.assertEqual(response.read(), b"")
        connection.close()

    def test_status(self) -> None:
        status, _, body = self.get("/api/status")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["app_name"], "Test App")
        self.assertEqual(data["app_url"], "https://app.example/")

    def test_start_requires_client_header(self) -> None:
        status, _, _ = self.post("/api/start", {"session": self.session})
        self.assertEqual(status, 403)
        self.assertEqual(self.launcher.calls, [])

    def test_start_with_client_header(self) -> None:
        status, _, body = self.post(
            "/api/start",
            {"session": self.session},
            {"X-DockWake-Client": "1"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["state"], "starting")
        self.assertEqual(self.launcher.calls, [("start", self.session)])

    def test_invalid_session_is_rejected(self) -> None:
        status, _, _ = self.post(
            "/api/start",
            {"session": "short"},
            {"X-DockWake-Client": "1"},
        )
        self.assertEqual(status, 400)

    def test_leave_beacon_with_matching_origin(self) -> None:
        status, _, body = self.post(
            "/api/leave",
            {"session": self.session},
            {"Origin": "https://launcher.example"},
        )
        self.assertEqual(status, 204)
        self.assertEqual(body, b"")
        self.assertEqual(self.launcher.calls, [("leave", self.session)])

    def test_leave_from_foreign_origin_is_rejected(self) -> None:
        status, _, _ = self.post(
            "/api/leave",
            {"session": self.session},
            {"Origin": "https://evil.example"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(self.launcher.calls, [])


if __name__ == "__main__":
    unittest.main()
