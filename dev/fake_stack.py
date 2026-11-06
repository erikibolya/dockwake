"""Local fake Docker proxy + fake application for UI development.

Run this in one terminal:

    python dev/fake_stack.py

Then run DockWake with the environment from the README. The fake application
becomes ready three seconds after the fake Docker proxy receives a start call.
"""

from __future__ import annotations

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

RUNNING = threading.Event()
STARTED_AT = 0.0
LOCK = threading.Lock()


def log(message: str) -> None:
    print(f"[fake {time.strftime('%H:%M:%S')}] {message}", flush=True)


class DockerProxyHandler(BaseHTTPRequestHandler):
    server_version = "FakeDockerProxy/1.0"

    def do_POST(self) -> None:  # noqa: N802
        global STARTED_AT
        if self.path.startswith("/containers/app/start"):
            with LOCK:
                if RUNNING.is_set():
                    log("start -> already running (304)")
                    self.send_response(304)
                else:
                    RUNNING.set()
                    STARTED_AT = time.time()
                    log("start -> starting (204)")
                    self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
            return
        if self.path.startswith("/containers/app/stop"):
            with LOCK:
                if not RUNNING.is_set():
                    log("stop -> already stopped (304)")
                    self.send_response(304)
                else:
                    RUNNING.clear()
                    log("stop -> stopping (204)")
                    self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
            return
        log(f"POST {self.path} -> 403")
        self.send_response(403)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/version", "/_ping"):
            body = b'{"ApiVersion":"1.41"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(403)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt: str, *args) -> None:
        return


FAKE_UI = b"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fake app</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; min-height: 100vh; background: #0b0f17; color: #e5edf9;
         font-family: ui-sans-serif, system-ui, sans-serif; }
  header { display: flex; align-items: center; gap: 12px; padding: 14px 20px;
           border-bottom: 1px solid #1e293b; background: #0f172a; }
  .logo { width: 28px; height: 28px; border-radius: 8px;
          background: linear-gradient(135deg, #38bdf8, #2563eb); }
  h1 { margin: 0; font-size: 16px; font-weight: 650; letter-spacing: -.02em; }
  .live { margin-left: auto; padding: 4px 9px; border-radius: 999px;
          background: rgba(34,197,94,.12); color: #86efac; font-size: 11px; }
  main { padding: 20px; display: grid; gap: 14px;
         grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); }
  .tile { aspect-ratio: 16 / 9; border-radius: 12px; border: 1px solid #1e293b;
          background: linear-gradient(150deg, #111c2e, #0a111d);
          display: grid; place-items: center; color: #6480a8; font-size: 13px; }
  .tile:nth-child(3n) { background: linear-gradient(150deg, #10202a, #0a111d); }
</style>
</head>
<body>
<header><div class="logo"></div><h1>Fake app</h1><span class="live">LIVE</span></header>
<main>
  <div class="tile">Panel 01</div><div class="tile">Panel 02</div>
  <div class="tile">Panel 03</div><div class="tile">Panel 04</div>
  <div class="tile">Panel 05</div><div class="tile">Panel 06</div>
</main>
</body>
</html>
"""


class FakeAppHandler(BaseHTTPRequestHandler):
    server_version = "FakeApp/1.0"

    def _ready(self) -> bool:
        with LOCK:
            return RUNNING.is_set() and time.time() - STARTED_AT >= 3.0

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        ready = self._ready()
        if path.startswith("/api/ready"):
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
        if path in ("/", "/index.html"):
            if ready:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(FAKE_UI)))
                self.end_headers()
                self.wfile.write(FAKE_UI)
            else:
                # Simulate the reverse proxy returning 404 before the route exists.
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt: str, *args) -> None:
        return


def serve(server: ThreadingHTTPServer) -> None:
    server.serve_forever()


def main() -> None:
    docker_port = int(os.environ.get("DOCKER_PORT", "2375"))
    app_port = int(os.environ.get("APP_PORT", "5001"))
    docker = ThreadingHTTPServer(("127.0.0.1", docker_port), DockerProxyHandler)
    app = ThreadingHTTPServer(("127.0.0.1", app_port), FakeAppHandler)
    docker.daemon_threads = True
    app.daemon_threads = True
    threading.Thread(target=serve, args=(docker,), daemon=True).start()
    threading.Thread(target=serve, args=(app,), daemon=True).start()
    log(f"docker proxy on 127.0.0.1:{docker_port}")
    log(f"fake app api/ui on 127.0.0.1:{app_port}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("shutting down")
    finally:
        docker.shutdown()
        app.shutdown()
        docker.server_close()
        app.server_close()


if __name__ == "__main__":
    main()
