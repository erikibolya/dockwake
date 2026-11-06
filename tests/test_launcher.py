from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dockwake.launcher import DockerProxyError, Launcher  # noqa: E402


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeDocker:
    def __init__(self) -> None:
        self.start_count = 0
        self.stop_count = 0
        self.fail_start = False
        self.fail_stop = False

    def start(self) -> int:
        if self.fail_start:
            raise DockerProxyError("start failed")
        self.start_count += 1
        return 204

    def stop(self) -> int:
        if self.fail_stop:
            raise DockerProxyError("stop failed")
        self.stop_count += 1
        return 204


class FakeApi:
    def __init__(self) -> None:
        self.value: str | None = None

    def probe(self) -> str | None:
        return self.value


class FakeUi:
    def __init__(self) -> None:
        self.ready_value = True

    def ready(self) -> bool:
        return self.ready_value


class LauncherTests(unittest.TestCase):
    session = "session-1234"

    def setUp(self) -> None:
        self.clock = FakeClock()
        self.docker = FakeDocker()
        self.api = FakeApi()
        self.ui = FakeUi()
        self.launcher = Launcher(
            self.docker,
            self.api,
            self.ui,
            app_name="Test App",
            app_url="https://app.example/",
            language="en",
            idle_stop_seconds=60,
            session_stale_seconds=600,
            start_timeout_seconds=120,
            clock=self.clock,
        )

    def make_ready(self) -> None:
        self.launcher.start(self.session)
        self.launcher.tick_once()  # calls docker start
        self.api.value = "ok"
        self.launcher.tick_once()  # first UI 200
        self.launcher.tick_once()  # second UI 200 -> ready
        self.assertEqual(self.launcher.status()["state"], "ready")

    def test_start_flow(self) -> None:
        status = self.launcher.start(self.session)
        self.assertEqual(status["state"], "starting")
        self.assertEqual(status["sessions"], 1)

        self.launcher.tick_once()
        self.assertEqual(self.docker.start_count, 1)
        self.assertEqual(self.launcher.status()["state"], "starting")

        self.api.value = "ok"
        self.launcher.tick_once()
        self.assertEqual(self.launcher.status()["state"], "starting")
        self.launcher.tick_once()
        status = self.launcher.status()
        self.assertEqual(status["state"], "ready")
        self.assertEqual(status["target_value"], "ok")
        self.assertTrue(status["managed"])

    def test_start_waits_for_web_ui(self) -> None:
        self.launcher.start(self.session)
        self.launcher.tick_once()
        self.api.value = "ok"
        self.ui.ready_value = False
        self.launcher.tick_once()

        status = self.launcher.status()
        self.assertEqual(status["state"], "starting")
        self.assertFalse(status["ui_ready"])

        self.ui.ready_value = True
        self.launcher.tick_once()
        self.assertEqual(self.launcher.status()["state"], "starting")
        self.launcher.tick_once()
        status = self.launcher.status()
        self.assertEqual(status["state"], "ready")
        self.assertTrue(status["ui_ready"])

    def test_start_timeout(self) -> None:
        self.launcher.start(self.session)
        self.launcher.tick_once()
        self.clock.advance(121)
        self.launcher.tick_once()

        status = self.launcher.status()
        self.assertEqual(status["state"], "error")
        self.assertIn("did not start", status["error"])

    def test_start_timeout_when_web_ui_never_ready(self) -> None:
        self.launcher.start(self.session)
        self.launcher.tick_once()
        self.api.value = "ok"
        self.ui.ready_value = False
        self.clock.advance(121)
        self.launcher.tick_once()

        status = self.launcher.status()
        self.assertEqual(status["state"], "error")
        self.assertIn("web UI did not load", status["error"])

    def test_docker_start_failure(self) -> None:
        self.docker.fail_start = True
        self.launcher.start(self.session)
        self.launcher.tick_once()

        status = self.launcher.status()
        self.assertEqual(status["state"], "error")
        self.assertIn("start failed", status["error"])

    def test_auto_stop_after_last_tab_leaves(self) -> None:
        self.make_ready()
        self.launcher.leave(self.session)
        self.assertEqual(self.launcher.status()["stop_in"], 60)

        self.clock.advance(59)
        self.launcher.tick_once()
        self.assertEqual(self.launcher.status()["state"], "ready")

        self.clock.advance(2)
        self.launcher.tick_once()
        self.assertEqual(self.docker.stop_count, 1)
        status = self.launcher.status()
        self.assertEqual(status["state"], "stopped")
        self.assertFalse(status["managed"])

    def test_second_tab_keeps_container_alive(self) -> None:
        other = "session-5678"
        self.make_ready()
        self.launcher.start(other)
        self.launcher.leave(self.session)

        self.clock.advance(61)
        self.launcher.heartbeat(other)
        self.launcher.tick_once()

        self.assertEqual(self.launcher.status()["state"], "ready")
        self.assertEqual(self.docker.stop_count, 0)

    def test_stop_calls_docker(self) -> None:
        self.make_ready()
        self.launcher.stop()
        self.launcher.tick_once()

        self.assertEqual(self.docker.stop_count, 1)
        self.assertEqual(self.launcher.status()["state"], "stopped")

    def test_ready_failure_after_three_misses(self) -> None:
        self.make_ready()
        self.api.value = None

        self.launcher.tick_once()
        self.launcher.tick_once()
        self.assertEqual(self.launcher.status()["state"], "ready")

        self.launcher.tick_once()
        status = self.launcher.status()
        self.assertEqual(status["state"], "error")
        self.assertIn("stopped responding", status["error"])

    def test_crashed_tab_is_stopped_after_stale_timeout(self) -> None:
        self.make_ready()
        self.clock.advance(601)
        self.launcher.tick_once()
        self.assertEqual(self.launcher.status()["state"], "ready")

        self.clock.advance(61)
        self.launcher.tick_once()
        self.assertEqual(self.docker.stop_count, 1)
        self.assertEqual(self.launcher.status()["state"], "stopped")

    def test_invalid_session_is_ignored(self) -> None:
        self.launcher.start("bad session")
        self.assertEqual(self.launcher.status()["sessions"], 0)

    def test_repeated_start_only_calls_docker_once(self) -> None:
        self.launcher.start(self.session)
        self.launcher.tick_once()
        self.launcher.start(self.session)
        self.launcher.tick_once()
        self.assertEqual(self.docker.start_count, 1)

    def test_status_contains_app_metadata(self) -> None:
        status = self.launcher.status()
        self.assertEqual(status["app_name"], "Test App")
        self.assertEqual(status["app_url"], "https://app.example/")
        self.assertEqual(status["language"], "en")
        self.assertEqual(status["stop_button_position"], "top-right")
        self.assertEqual(status["idle_stop_seconds"], 60)
        self.assertTrue(Launcher.valid_session(self.session))
        self.assertFalse(Launcher.valid_session("short"))

    def test_probe_adopts_already_running_app(self) -> None:
        self.api.value = "ok"
        self.launcher.tick_once()

        status = self.launcher.status()
        self.assertEqual(status["state"], "ready")
        self.assertTrue(status["managed"])
        self.assertEqual(status["stop_in"], 60)

        self.launcher.heartbeat(self.session)
        self.assertIsNone(self.launcher.status()["stop_in"])
        self.launcher.leave(self.session)
        self.clock.advance(61)
        self.launcher.tick_once()
        self.assertEqual(self.docker.stop_count, 1)

    def test_running_app_returns_to_stopped_when_api_disappears(self) -> None:
        self.api.value = "ok"
        self.launcher.tick_once()
        self.assertEqual(self.launcher.status()["state"], "ready")

        self.api.value = None
        self.launcher.tick_once()
        self.launcher.tick_once()
        self.launcher.tick_once()

        status = self.launcher.status()
        self.assertEqual(status["state"], "stopped")
        self.assertIsNone(status["error"])
        self.assertFalse(status["managed"])

    def test_stop_button_position(self) -> None:
        for position in (
            "top-right",
            "top-left",
            "bottom-right",
            "bottom-left",
        ):
            launcher = Launcher(
                self.docker,
                self.api,
                self.ui,
                app_name="Test App",
                app_url="https://app.example/",
                stop_button_position=position,
                clock=self.clock,
            )
            self.assertEqual(launcher.status()["stop_button_position"], position)

        invalid = Launcher(
            self.docker,
            self.api,
            self.ui,
            app_name="Test App",
            app_url="https://app.example/",
            stop_button_position="middle",
            clock=self.clock,
        )
        self.assertEqual(invalid.status()["stop_button_position"], "top-right")

    def test_czech_messages(self) -> None:
        launcher = Launcher(
            self.docker,
            self.api,
            self.ui,
            app_name="Aplikace",
            app_url="https://app.example/",
            language="cs",
            idle_stop_seconds=60,
            session_stale_seconds=600,
            start_timeout_seconds=120,
            clock=self.clock,
        )
        launcher.start(self.session)
        launcher.tick_once()
        self.clock.advance(121)
        launcher.tick_once()
        self.assertIn("Aplikace se nespustila", launcher.status()["error"])


if __name__ == "__main__":
    unittest.main()
