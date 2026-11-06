from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dockwake.main import config_from_env  # noqa: E402


class ConfigTests(unittest.TestCase):
    def test_stop_button_position_from_env(self) -> None:
        for position in (
            "top-right",
            "top-left",
            "bottom-right",
            "bottom-left",
        ):
            with patch.dict(
                os.environ,
                {
                    "DOCKWAKE_APP_URL": "https://app.example/",
                    "DOCKWAKE_STOP_BUTTON_POSITION": position,
                },
                clear=True,
            ):
                config = config_from_env()
                self.assertEqual(config.stop_button_position, position)

    def test_invalid_stop_button_position_falls_back(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DOCKWAKE_APP_URL": "https://app.example/",
                "DOCKWAKE_STOP_BUTTON_POSITION": "middle",
            },
            clear=True,
        ):
            self.assertEqual(config_from_env().stop_button_position, "top-right")

    def test_app_url_is_required(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                config_from_env()


if __name__ == "__main__":
    unittest.main()
