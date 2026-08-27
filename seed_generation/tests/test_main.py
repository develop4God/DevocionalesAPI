"""
test_main.py — Tests for the top-level seed_generation.main menu.

_build_seed launches a real subprocess that hits remote sources (see
seed_extractor_fetch.py's docstring) — never invoked for real here, only
its constructed command and dispatch are tested.
"""

import sys
import unittest
from unittest.mock import MagicMock, patch

from seed_generation.main import _MAIN_OPTIONS, _build_seed, main


class TestBuildSeedCommand(unittest.TestCase):
    def test_launches_seed_extractor_fetch_as_a_module(self):
        with (
            patch("seed_generation.main.subprocess.run") as mock_run,
            patch("builtins.input", return_value=""),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            _build_seed()
        mock_run.assert_called_once_with(
            [sys.executable, "-m", "seed_generation.tools.seed_extractor_fetch"],
            check=False,
        )

    def test_nonzero_exit_does_not_raise(self):
        with (
            patch("seed_generation.main.subprocess.run") as mock_run,
            patch("builtins.input", return_value=""),
        ):
            mock_run.return_value = MagicMock(returncode=1)
            _build_seed()  # must not raise


class TestMainMenuDispatch(unittest.TestCase):
    def test_option_keys_match_dispatch_branches(self):
        # Every option key in _MAIN_OPTIONS must be handled in main()'s
        # dispatch — a typo'd key here would silently do nothing when chosen.
        keys = {key for key, _ in _MAIN_OPTIONS}
        self.assertEqual(keys, {"build_seed", "generate"})

    def test_exit_choice_returns_without_calling_either_stage(self):
        with (
            patch("builtins.input", return_value="0"),
            patch("seed_generation.main._build_seed") as mock_build,
            patch("seed_generation.main.run_dashboard") as mock_dashboard,
        ):
            main()
        mock_build.assert_not_called()
        mock_dashboard.assert_not_called()

    def test_choosing_generate_calls_run_dashboard_then_exits(self):
        with (
            patch("builtins.input", side_effect=["2", "0"]),
            patch("seed_generation.main.run_dashboard") as mock_dashboard,
        ):
            main()
        mock_dashboard.assert_called_once()

    def test_choosing_build_seed_calls_build_seed_then_exits(self):
        with (
            patch("builtins.input", side_effect=["1", "0"]),
            patch("seed_generation.main._build_seed") as mock_build,
        ):
            main()
        mock_build.assert_called_once()


if __name__ == "__main__":
    unittest.main()
