"""DockWake HTTP frontend and JSON API."""

from __future__ import annotations

import json
import os
import signal
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .launcher import (
    DockerProxyClient,
    Launcher,
    TargetApiClient,
    TargetUiClient,
)

STATIC_TYPES = {
    "app.js": "text/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
    "favicon.svg": "image/svg+xml",
}
POST_ROUTES = ("/api/start", "/api/heartbeat", "/api/leave", "/api/stop")
SUPPORTED_LANGUAGES = ("en", "cs")


@dataclass(frozen=True)
class Config:
    port: int = 8080
    app_name: str = "App"
    app_url: str = "https://example.com/"
    app_icon_url: str = ""
    language: str = "en"
    launcher_origin: str = ""
    target_ui_url: str = "https://example.com/"
    static_dir: Path = Path(__file__).resolve().parent / "static"
    stop_grace_seconds: int = 30


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def config_from_env() -> Config:
    app_url = os.environ.get("DOCKWAKE_APP_URL", "").rstrip("/") + "/"
    if app_url == "/":
        raise RuntimeError("DOCKWAKE_APP_URL is required (for example https://app.example.com/)")
    language = os.environ.get("DOCKWAKE_LANGUAGE", "en").lower()
    if language not in SUPPORTED_LANGUAGES:
        language = "en"
    return Config(
        port=env_int("DOCKWAKE_PORT", 8080),
        app_name=os.environ.get("DOCKWAKE_APP_NAME", "App"),
        app_url=app_url,
        app_icon_url=os.environ.get("DOCKWAKE_APP_ICON_URL", ""),
        language=language,
        launcher_origin=os.environ.get("DOCKWAKE_LAUNCHER_ORIGIN", "").rstrip("/"),
        target_ui_url=(os.environ.get("DOCKWAKE_TARGET_UI_URL", app_url).rstrip("/") + "/"),
        static_dir=Path(
            os.environ.get(
                "DOCKWAKE_STATIC_DIR",
                str(Path(__file__).resolve().parent / "static"),
            )
        ),
        stop_grace_seconds=env_int("DOCKWAKE_STOP_GRACE_SECONDS", 30),
    )


def make_handler(launcher: Launcher, config: Config):
    class Handler(BaseHTTPRequestHandler):
        server_version = "DockWake/1.0"

        # ------------------------------------------------------------- GET

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/health":
                self._send_text(200, "ok")
                return
            if path in ("/", "/index.html"):
                self._send_index()
                return
            if path.startswith("/static/"):
                name = path[len("/static/") :]
                if name in STATIC_TYPES:
                    self._send_static(name, STATIC_TYPES[name])
                else:
                    self._send_json(404, {"error": "not found"})
                return
            if path == "/api/status":
                self._send_json(200, launcher.status())
                return
            self._send_json(404, {"error": "not found"})

        def do_HEAD(self) -> None:  # noqa: N802
            self._head_only = True
            self.do_GET()

        # ------------------------------------------------------------ POST

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            if path not in POST_ROUTES:
                self._send_json(404, {"error": "not found"})
                return
            if not self._authorized(path):
                self._send_json(403, {"error": "forbidden"})
                return
            payload = self._read_payload(parsed)
            if payload is None:
                return
            session = payload.get("session")
            if not launcher.valid_session(session):
                self._send_json(400, {"error": "invalid session"})
                return

            if path == "/api/start":
                self._send_json(200, launcher.start(session))
            elif path == "/api/heartbeat":
                self._send_json(200, launcher.heartbeat(session))
            elif path == "/api/leave":
                launcher.leave(session)
                self._send_empty(204)
            elif path == "/api/stop":
                self._send_json(200, launcher.stop())

        # ------------------------------------------------------- auth/body

        def _authorized(self, path: str) -> bool:
            if self.headers.get("X-DockWake-Client") == "1":
                return True
            if path != "/api/leave":
                return False
            content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type != "application/json":
                return False
            origin = self.headers.get("Origin")
            if origin:
                return bool(config.launcher_origin) and (
                    origin.rstrip("/") == config.launcher_origin
                )
            site = self.headers.get("Sec-Fetch-Site")
            return site in ("same-origin", "same-site")

        def _read_payload(self, parsed) -> dict | None:
            raw_length = self.headers.get("Content-Length") or "0"
            try:
                length = int(raw_length)
            except ValueError:
                self._send_json(400, {"error": "invalid content length"})
                return None
            if length > 4096:
                self._send_json(413, {"error": "payload too large"})
                return None
            raw = self.rfile.read(length) if length else b""
            if raw:
                try:
                    data = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._send_json(400, {"error": "invalid JSON"})
                    return None
                if not isinstance(data, dict):
                    self._send_json(400, {"error": "invalid JSON"})
                    return None
                return data
            query = parse_qs(parsed.query)
            return {"session": (query.get("session") or [""])[0]}

        # ------------------------------------------------------ responses

        def _send_index(self) -> None:
            path = config.static_dir / "index.html"
            try:
                body = path.read_bytes()
            except OSError:
                self._send_json(500, {"error": "index.html missing"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", self._csp())
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not getattr(self, "_head_only", False):
                self.wfile.write(body)

        def _send_static(self, name: str, content_type: str) -> None:
            path = config.static_dir / name
            try:
                body = path.read_bytes()
            except OSError:
                self._send_json(404, {"error": "not found"})
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not getattr(self, "_head_only", False):
                self.wfile.write(body)

        def _send_json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not getattr(self, "_head_only", False):
                self.wfile.write(body)

        def _send_text(self, status: int, text: str) -> None:
            body = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not getattr(self, "_head_only", False):
                self.wfile.write(body)

        def _send_empty(self, status: int) -> None:
            self.send_response(status)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _csp(self) -> str:
            ui = urlparse(config.app_url)
            frame_src = f"{ui.scheme}://{ui.netloc}" if ui.scheme and ui.netloc else "'self'"
            return (
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self'; "
                "img-src 'self' data:; "
                "connect-src 'self'; "
                f"frame-src {frame_src}; "
                "base-uri 'none'; "
                "form-action 'none'"
            )

        def log_message(self, fmt: str, *args) -> None:
            print(
                f"[http] {self.address_string()} {self.command} {self.path} - {fmt % args}",
                flush=True,
            )

    return Handler


def create_server(
    launcher: Launcher,
    config: Config,
    host: str = "0.0.0.0",
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, config.port), make_handler(launcher, config))
    server.daemon_threads = True
    return server


def main() -> None:
    config = config_from_env()
    docker = DockerProxyClient(
        os.environ["DOCKER_PROXY_URL"],
        os.environ["DOCKWAKE_TARGET_CONTAINER"],
        stop_grace_seconds=config.stop_grace_seconds,
    )
    api = TargetApiClient(os.environ["DOCKWAKE_TARGET_API_URL"])
    ui = TargetUiClient(config.target_ui_url)
    launcher = Launcher(
        docker,
        api,
        ui,
        app_name=config.app_name,
        app_url=config.app_url,
        app_icon_url=config.app_icon_url,
        language=config.language,
        idle_stop_seconds=env_int("DOCKWAKE_IDLE_STOP_SECONDS", 60),
        session_stale_seconds=env_int("DOCKWAKE_SESSION_STALE_SECONDS", 180),
        start_timeout_seconds=env_int("DOCKWAKE_START_TIMEOUT_SECONDS", 120),
    )
    launcher.start_monitor()
    server = create_server(launcher, config)
    print(
        f"[dockwake] listening on 0.0.0.0:{config.port}; "
        f"app={config.app_name!r}; ui={config.app_url}; language={config.language}",
        flush=True,
    )

    def stop(_signum, _frame) -> None:
        print("[dockwake] shutting down", flush=True)
        launcher.shutdown()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        launcher.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
