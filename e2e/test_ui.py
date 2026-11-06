from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
DOCKER_PORT = 23750
APP_PORT = 15001
LAUNCHER_PORT = 18090
LAUNCHER_URL = f"http://127.0.0.1:{LAUNCHER_PORT}"


def wait_http(url: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            pass
        time.sleep(0.2)
    raise AssertionError(f"{url} did not become ready")


def wait_state(state: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{LAUNCHER_URL}/api/status", timeout=2) as response:
                last = json.loads(response.read())["state"]
        except Exception as error:  # noqa: BLE001
            last = str(error)
        if last == state:
            return
        time.sleep(0.25)
    raise AssertionError(f"state {state!r} not reached; last={last!r}")


def test_start_shows_iframe_and_closing_tab_stops_app() -> None:
    env = os.environ.copy()
    env["DOCKER_PORT"] = str(DOCKER_PORT)
    env["APP_PORT"] = str(APP_PORT)

    fake = subprocess.Popen(
        [sys.executable, str(ROOT / "dev" / "fake_stack.py")],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    app_env = env.copy()
    app_env.update(
        {
            "DOCKWAKE_PORT": str(LAUNCHER_PORT),
            "DOCKWAKE_TARGET_CONTAINER": "app",
            "DOCKER_PROXY_URL": f"http://127.0.0.1:{DOCKER_PORT}",
            "DOCKWAKE_TARGET_API_URL": f"http://127.0.0.1:{APP_PORT}/api/ready",
            "DOCKWAKE_TARGET_UI_URL": f"http://127.0.0.1:{APP_PORT}/",
            "DOCKWAKE_APP_URL": f"http://127.0.0.1:{APP_PORT}/",
            "DOCKWAKE_APP_NAME": "Test App",
            "DOCKWAKE_LANGUAGE": "en",
            "DOCKWAKE_LAUNCHER_ORIGIN": LAUNCHER_URL,
            "DOCKWAKE_IDLE_STOP_SECONDS": "5",
            "DOCKWAKE_SESSION_STALE_SECONDS": "30",
            "DOCKWAKE_START_TIMEOUT_SECONDS": "30",
        }
    )
    app = subprocess.Popen(
        [sys.executable, "-m", "dockwake.main"],
        cwd=ROOT,
        env=app_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        wait_http(f"{LAUNCHER_URL}/health")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.goto(f"{LAUNCHER_URL}/")

            expect(page.get_by_role("heading", name="Test App")).to_be_visible()
            expect(page.get_by_role("button", name="Start app")).to_be_visible()
            page.get_by_role("button", name="Start app").click()

            frame = page.locator("#app-frame")
            expect(frame).to_be_visible(timeout=30000)
            expect(page.frame_locator("#app-frame").get_by_text("Fake app")).to_be_visible(
                timeout=30000
            )

            box = page.locator("#stop-button").bounding_box()
            assert box is not None
            assert box["y"] < 40, box
            assert box["x"] + box["width"] > 1280 - 40, box

            # Navigating away fires pagehide, exactly like closing the tab.
            page.goto("about:blank")
            wait_state("stopped", timeout=20)
            browser.close()
    finally:
        for process in (app, fake):
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
