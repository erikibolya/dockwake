"""DockWake state machine and HTTP probe clients.

DockWake never talks to the Docker socket directly. It calls a restricted
socket proxy that only allows starting and stopping the target container.
"""

from __future__ import annotations

import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable

SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
ACTIVE_STATES = ("starting", "ready", "error")

MESSAGES = {
    "en": {
        "start_timeout": "The app did not start within {seconds} s.",
        "ui_timeout": "The app API is up, but its web UI did not load.",
        "stopped_responding": "The app stopped responding.",
    },
    "cs": {
        "start_timeout": "Aplikace se nespustila do {seconds} s.",
        "ui_timeout": "API aplikace běží, ale webové UI se nenačetlo.",
        "stopped_responding": "Aplikace přestala odpovídat.",
    },
}


class DockerProxyError(RuntimeError):
    """The restricted Docker proxy could not be reached or denied the call."""


class DockerProxyClient:
    """Minimal client for the linuxserver/docker-socket-proxy API."""

    def __init__(
        self,
        base_url: str,
        container: str,
        stop_grace_seconds: int = 30,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.container = container
        self.stop_grace_seconds = max(1, int(stop_grace_seconds))
        self.timeout = timeout

    def _post(self, path: str, timeout: float | None = None) -> int:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=b"",
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                return response.status
        except urllib.error.HTTPError as exc:
            if exc.code in (204, 304):
                return exc.code
            body = exc.read().decode("utf-8", "replace").strip()
            raise DockerProxyError(f"Docker API {exc.code}: {body[:300]}") from exc
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI
            raise DockerProxyError(f"Docker API unreachable: {exc}") from exc

    def start(self) -> int:
        return self._post(f"/containers/{self.container}/start")

    def stop(self) -> int:
        return self._post(
            f"/containers/{self.container}/stop?t={self.stop_grace_seconds}",
            timeout=self.stop_grace_seconds + 15.0,
        )


class TargetApiClient:
    """Readiness probe for an application's internal HTTP endpoint."""

    def __init__(self, url: str, timeout: float = 2.0) -> None:
        self.url = url
        self.timeout = timeout

    def probe(self) -> str | None:
        try:
            with urllib.request.urlopen(self.url, timeout=self.timeout) as response:
                if not 200 <= response.status < 300:
                    return None
                value = response.read(256).decode("utf-8", "replace").strip()
                return value or "ok"
        except Exception:  # noqa: BLE001 - "not ready yet" is a normal condition
            return None


class TargetUiClient:
    """Check that the public UI is actually served by the reverse proxy."""

    def __init__(self, url: str, timeout: float = 2.0) -> None:
        self.url = url
        self.timeout = timeout

    def ready(self) -> bool:
        try:
            with urllib.request.urlopen(self.url, timeout=self.timeout) as response:
                return 200 <= response.status < 400
        except urllib.error.HTTPError as exc:
            # A login page is still a working UI.
            return exc.code in (401, 403)
        except Exception:  # noqa: BLE001 - 404/502/timeout all mean "not yet"
            return False


class Launcher:
    """Thread-safe state machine around one Docker container.

    HTTP handlers only change state and read snapshots. The monitor thread
    performs the potentially slow Docker and HTTP calls outside the lock, so
    /api/status always answers quickly.
    """

    def __init__(
        self,
        docker: DockerProxyClient,
        api: TargetApiClient,
        ui: TargetUiClient,
        *,
        app_name: str = "App",
        app_url: str,
        app_icon_url: str = "",
        language: str = "en",
        idle_stop_seconds: int = 60,
        session_stale_seconds: int = 180,
        start_timeout_seconds: int = 120,
        clock: Callable[[], float] = time.monotonic,
        max_sessions: int = 50,
    ) -> None:
        self.docker = docker
        self.api = api
        self.ui = ui
        self.app_name = app_name.strip() or "App"
        self.app_url = app_url
        self.app_icon_url = app_icon_url
        self.language = language if language in MESSAGES else "en"
        self.messages = MESSAGES[self.language]
        self.idle_stop_seconds = max(5, int(idle_stop_seconds))
        self.session_stale_seconds = max(60, int(session_stale_seconds))
        self.start_timeout_seconds = max(10, int(start_timeout_seconds))
        self.clock = clock
        self.max_sessions = max(1, int(max_sessions))

        self.lock = threading.RLock()
        self.state = "stopped"
        self.managed = False
        self.sessions: dict[str, float] = {}
        self.stop_at: float | None = None
        self.last_activity: float | None = None
        self.error: str | None = None
        self.target_value: str | None = None
        self.ui_ready = False
        self.started_at: float | None = None
        self.ready_at: float | None = None
        self.start_in_flight = False
        self.start_done = False
        self.stop_in_flight = False
        self.stop_done = False
        self._fail_count = 0
        self._ui_ok_count = 0
        self.last_probe_at = 0.0

        self._stop_event = threading.Event()
        self._monitor: threading.Thread | None = None

    # ------------------------------------------------------------------ public

    @staticmethod
    def valid_session(session: object) -> bool:
        return isinstance(session, str) and bool(SESSION_RE.fullmatch(session))

    def start_monitor(self) -> None:
        if self._monitor is not None:
            return
        self._monitor = threading.Thread(
            target=self._monitor_loop,
            name="dockwake-monitor",
            daemon=True,
        )
        self._monitor.start()

    def shutdown(self) -> None:
        self._stop_event.set()
        if self._monitor is not None:
            self._monitor.join(timeout=2.0)
            self._monitor = None

    def start(self, session: str) -> dict:
        now = self.clock()
        with self.lock:
            self._register_session_locked(session, now)
            if self.state in ("starting", "ready"):
                self.managed = True
                self.stop_at = None
                return self._status_locked(now)
            if self.state == "stopping":
                return self._status_locked(now)

            self.state = "starting"
            self.managed = True
            self.error = None
            self.target_value = None
            self.ui_ready = False
            self.started_at = now
            self.ready_at = None
            self.start_in_flight = False
            self.start_done = False
            self.stop_in_flight = False
            self.stop_done = False
            self.stop_at = None
            self.last_activity = now
            self._fail_count = 0
            self._ui_ok_count = 0
            return self._status_locked(now)

    def heartbeat(self, session: str) -> dict:
        now = self.clock()
        with self.lock:
            if self.state not in ("starting", "ready"):
                return self._status_locked(now)
            self._register_session_locked(session, now)
            self.stop_at = None
            if self.state == "ready":
                self.managed = True
            return self._status_locked(now)

    def leave(self, session: str) -> dict:
        now = self.clock()
        with self.lock:
            self.sessions.pop(session, None)
            if not self.sessions and self.managed and self.state in ACTIVE_STATES:
                self.stop_at = now + self.idle_stop_seconds
            return self._status_locked(now)

    def stop(self) -> dict:
        now = self.clock()
        with self.lock:
            if self.state == "stopped":
                return self._status_locked(now)
            self.sessions.clear()
            self.stop_at = None
            self.state = "stopping"
            self.stop_in_flight = False
            self.stop_done = False
            self.last_activity = now
            return self._status_locked(now)

    def status(self) -> dict:
        now = self.clock()
        with self.lock:
            return self._status_locked(now)

    # ----------------------------------------------------------------- monitor

    def _monitor_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.tick_once()
            except Exception as exc:  # noqa: BLE001 - keep the monitor alive
                print(f"[dockwake] monitor error: {exc}", flush=True)
            self._stop_event.wait(1.0)

    def tick_once(self) -> None:
        now = self.clock()
        action: str | None = None

        with self.lock:
            self._prune_sessions_locked(now)
            self._maybe_schedule_auto_stop_locked(now)

            if self.state == "starting":
                if not self.start_done and not self.start_in_flight:
                    self.start_in_flight = True
                    action = "start"
                elif self.start_done:
                    action = "poll_starting"
            elif self.state == "ready":
                action = "poll_ready"
            elif self.state == "stopping":
                if not self.stop_done and not self.stop_in_flight:
                    self.stop_in_flight = True
                    action = "stop"
            elif self.state == "stopped":
                if now - self.last_probe_at >= 10:
                    self.last_probe_at = now
                    action = "probe_stopped"

        if action == "start":
            error: str | None = None
            try:
                self.docker.start()
            except DockerProxyError as exc:
                error = str(exc)
            with self.lock:
                self.start_in_flight = False
                if self.state != "starting":
                    return
                self.start_done = True
                if error is not None:
                    self.state = "error"
                    self.error = error
            return

        if action == "stop":
            error = None
            try:
                self.docker.stop()
            except DockerProxyError as exc:
                error = str(exc)
            with self.lock:
                self.stop_in_flight = False
                if self.state != "stopping":
                    return
                self.stop_done = True
                if error is not None:
                    self.state = "error"
                    self.error = error
                    return
                self.state = "stopped"
                self.managed = False
                self.target_value = None
                self.ui_ready = False
                self.sessions.clear()
                self.stop_at = None
            return

        if action == "probe_stopped":
            value = self.api.probe()
            ui_ready = value is not None and self.ui.ready()
            with self.lock:
                if self.state != "stopped":
                    return
                if value is not None and ui_ready:
                    self.state = "ready"
                    self.target_value = value
                    self.ui_ready = True
                    self.ready_at = now
                    self.started_at = None
                    self.managed = True
                    self.error = None
                    self._fail_count = 0
                    self.last_activity = now
                    self.stop_at = now + self.idle_stop_seconds
            return

        if action == "poll_starting":
            value = self.api.probe()
            ui_ready = value is not None and self.ui.ready()
            with self.lock:
                if self.state != "starting":
                    return
                self.ui_ready = bool(value is not None and ui_ready)
                if value is not None and ui_ready:
                    self._ui_ok_count += 1
                else:
                    self._ui_ok_count = 0
                if value is not None and ui_ready and self._ui_ok_count >= 2:
                    self.state = "ready"
                    self.target_value = value
                    self.ready_at = now
                    self.managed = True
                    self.error = None
                    self._fail_count = 0
                elif (
                    self.started_at is not None
                    and now - self.started_at > self.start_timeout_seconds
                ):
                    self.state = "error"
                    if value is not None:
                        self.error = self.messages["ui_timeout"]
                    else:
                        self.error = self.messages["start_timeout"].format(
                            seconds=self.start_timeout_seconds
                        )
            return

        if action == "poll_ready":
            value = self.api.probe()
            with self.lock:
                if self.state != "ready":
                    return
                if value is not None:
                    self.target_value = value
                    self.ui_ready = True
                    self._fail_count = 0
                else:
                    self._fail_count += 1
                    if self._fail_count >= 3:
                        if self.sessions:
                            self.state = "error"
                            self.error = self.messages["stopped_responding"]
                        else:
                            self.state = "stopped"
                            self.target_value = None
                            self.ui_ready = False
                            self.error = None
                            self.sessions.clear()
                            self.stop_at = None
                            self.managed = False

    # ----------------------------------------------------------------- helpers

    def _register_session_locked(self, session: str, now: float) -> None:
        if not self.valid_session(session):
            return
        if session not in self.sessions and len(self.sessions) >= self.max_sessions:
            oldest = min(self.sessions, key=self.sessions.get)
            self.sessions.pop(oldest, None)
        self.sessions[session] = now
        self.last_activity = now

    def _prune_sessions_locked(self, now: float) -> None:
        if not self.sessions:
            return
        stale = [
            session
            for session, seen in self.sessions.items()
            if now - seen > self.session_stale_seconds
        ]
        for session in stale:
            self.sessions.pop(session, None)
        if (
            stale
            and not self.sessions
            and self.managed
            and self.state in ACTIVE_STATES
            and self.stop_at is None
        ):
            self.stop_at = now + self.idle_stop_seconds

    def _maybe_schedule_auto_stop_locked(self, now: float) -> None:
        if self.state not in ACTIVE_STATES or not self.managed:
            return
        if self.sessions:
            return
        if self.stop_at is not None and now >= self.stop_at:
            self.state = "stopping"
            self.stop_in_flight = False
            self.stop_done = False
            self.stop_at = None
            return
        if (
            self.stop_at is None
            and self.last_activity is not None
            and now - self.last_activity > self.session_stale_seconds
        ):
            self.state = "stopping"
            self.stop_in_flight = False
            self.stop_done = False

    def _status_locked(self, now: float) -> dict:
        stop_in: int | None = None
        if self.stop_at is not None and not self.sessions and self.state in ACTIVE_STATES:
            stop_in = max(0, int(round(self.stop_at - now)))
        elapsed: float | None = None
        if self.started_at is not None and self.state in ACTIVE_STATES:
            elapsed = max(0.0, round(now - self.started_at, 1))
        return {
            "state": self.state,
            "target_value": self.target_value,
            "ui_ready": self.ui_ready,
            "managed": self.managed,
            "sessions": len(self.sessions),
            "stop_in": stop_in,
            "error": self.error,
            "elapsed": elapsed,
            "app_name": self.app_name,
            "app_url": self.app_url,
            "app_icon_url": self.app_icon_url,
            "language": self.language,
            "idle_stop_seconds": self.idle_stop_seconds,
        }
