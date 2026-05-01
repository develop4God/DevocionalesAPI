#!/usr/bin/env python3
"""
test_main.py — Comprehensive tests for main.py interactive launcher

Tests cover:
1. Session management (load, save, defaults)
2. Menu navigation and input handling
3. Command building for all pipeline stages
4. Integration with lang_registry
5. Provider loading from providers.yml
6. Edge cases and error handling
"""

import json
import sys
from dataclasses import asdict
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add parent directory to path to import main
sys.path.insert(0, str(Path(__file__).parent.parent))

import main
from main import Session


class TestSession:
    """Test Session dataclass and persistence."""

    def test_session_defaults(self):
        """Test that Session has correct default values."""
        s = Session()
        assert s.lang == "es"
        assert s.version == "RVR1960"
        assert s.year == 2025
        assert s.local_file == ""

    def test_session_custom_values(self):
        """Test Session with custom values."""
        s = Session(
            lang="pt", version="NVI", year=2024, local_file="/path/to/file.json"
        )
        assert s.lang == "pt"
        assert s.version == "NVI"
        assert s.year == 2024
        assert s.local_file == "/path/to/file.json"

    def test_save_and_load_session(self, tmp_path):
        """Test saving and loading session to/from JSON."""
        session_file = tmp_path / ".gep_session.json"

        # Create session and save
        s1 = Session(lang="en", version="KJV", year=2023, local_file="test.json")
        session_file.write_text(json.dumps(asdict(s1), indent=2), encoding="utf-8")

        # Load session
        with patch.object(main, "_SESSION_FILE", session_file):
            s2 = main._load_session()

        assert s2.lang == "en"
        assert s2.version == "KJV"
        assert s2.year == 2023
        assert s2.local_file == "test.json"

    def test_load_session_missing_file(self, tmp_path):
        """Test loading session when file doesn't exist returns defaults."""
        session_file = tmp_path / ".gep_session_nonexistent.json"

        with patch.object(main, "_SESSION_FILE", session_file):
            s = main._load_session()

        assert s.lang == "es"
        assert s.version == "RVR1960"
        assert s.year == 2025

    def test_load_session_invalid_json(self, tmp_path):
        """Test loading session with invalid JSON returns defaults."""
        session_file = tmp_path / ".gep_session.json"
        session_file.write_text("{ invalid json", encoding="utf-8")

        with patch.object(main, "_SESSION_FILE", session_file):
            s = main._load_session()

        assert s.lang == "es"
        assert s.version == "RVR1960"

    def test_save_session(self, tmp_path):
        """Test saving session creates valid JSON file."""
        session_file = tmp_path / ".gep_session.json"
        s = Session(lang="fil", version="ASND", year=2026, local_file="")

        with patch.object(main, "_SESSION_FILE", session_file):
            main._save_session(s)

        assert session_file.exists()
        data = json.loads(session_file.read_text(encoding="utf-8"))
        assert data["lang"] == "fil"
        assert data["version"] == "ASND"
        assert data["year"] == 2026


class TestBoxDrawing:
    """Test box-drawing and UI helper functions."""

    def test_box_top_no_title(self):
        """Test box top without title."""
        result = main._box_top()
        assert result.startswith("╔")
        assert result.endswith("╗")
        assert len(result) == main._W + 2

    def test_box_top_with_title(self):
        """Test box top with title."""
        result = main._box_top("TEST")
        assert "TEST" in result
        assert result.startswith("╔")
        assert result.endswith("╗")

    def test_box_mid_with_title(self):
        """Test box middle divider with title."""
        result = main._box_mid("SECTION")
        assert "SECTION" in result
        assert result.startswith("╠")
        assert result.endswith("╣")

    def test_box_bot(self):
        """Test box bottom."""
        result = main._box_bot()
        assert result.startswith("╚")
        assert result.endswith("╝")

    def test_box_row_plain_text(self):
        """Test box row with plain text."""
        result = main._box_row("Hello World")
        assert result.startswith("║")
        assert result.endswith("║")
        assert "Hello World" in result

    def test_box_row_with_ansi_colors(self):
        """Test box row with ANSI color codes (should be stripped for padding calculation)."""
        colored_text = main._c("Colored", main._CYAN, main._BOLD)
        result = main._box_row(colored_text)
        assert result.startswith("║")
        assert result.endswith("║")

    def test_ansi_color_helper(self):
        """Test _c() ANSI color helper."""
        result = main._c("test", main._CYAN, main._BOLD)
        assert result.startswith(main._CYAN)
        assert main._BOLD in result
        assert result.endswith(main._RESET)
        assert "test" in result


class TestInputHelpers:
    """Test user input helper functions."""

    @patch("builtins.input", return_value="test_value")
    def test_ask_with_input(self, mock_input):
        """Test _ask() returns user input."""
        result = main._ask("Enter value", "default")
        assert result == "test_value"

    @patch("builtins.input", return_value="")
    def test_ask_returns_default(self, mock_input):
        """Test _ask() returns default when user presses Enter."""
        result = main._ask("Enter value", "default_value")
        assert result == "default_value"

    @patch("builtins.input", side_effect=KeyboardInterrupt)
    def test_ask_keyboard_interrupt(self, mock_input):
        """Test _ask() handles KeyboardInterrupt gracefully."""
        result = main._ask("Enter value", "default")
        assert result == "default"

    @patch("builtins.input", return_value="y")
    def test_confirm_yes(self, mock_input):
        """Test _confirm() returns True for 'y'."""
        result = main._confirm("Confirm?")
        assert result is True

    @patch("builtins.input", return_value="n")
    def test_confirm_no(self, mock_input):
        """Test _confirm() returns False for 'n'."""
        result = main._confirm("Confirm?")
        assert result is False

    @patch("builtins.input", return_value="")
    def test_confirm_default_true(self, mock_input):
        """Test _confirm() returns default True when pressing Enter."""
        result = main._confirm("Confirm?", default=True)
        assert result is True

    @patch("builtins.input", return_value="")
    def test_confirm_default_false(self, mock_input):
        """Test _confirm() returns default False when pressing Enter."""
        result = main._confirm("Confirm?", default=False)
        assert result is False

    @patch("builtins.input", side_effect=KeyboardInterrupt)
    def test_confirm_keyboard_interrupt(self, mock_input):
        """Test _confirm() handles KeyboardInterrupt."""
        result = main._confirm("Confirm?", default=True)
        assert result is True


class TestNumberedMenu:
    """Test _numbered_menu() function."""

    @patch("builtins.input", return_value="1")
    def test_numbered_menu_first_option(self, mock_input):
        """Test selecting first option."""
        options = [("opt1", "Option 1"), ("opt2", "Option 2")]
        result = main._numbered_menu("Test Menu", options)
        assert result == "opt1"

    @patch("builtins.input", return_value="2")
    def test_numbered_menu_second_option(self, mock_input):
        """Test selecting second option."""
        options = [("opt1", "Option 1"), ("opt2", "Option 2")]
        result = main._numbered_menu("Test Menu", options)
        assert result == "opt2"

    @patch("builtins.input", return_value="0")
    def test_numbered_menu_back(self, mock_input):
        """Test selecting back/exit (0)."""
        options = [("opt1", "Option 1")]
        result = main._numbered_menu("Test Menu", options)
        assert result == "0"

    @patch("builtins.input", side_effect=["99", "1"])
    def test_numbered_menu_invalid_then_valid(self, mock_input):
        """Test invalid input followed by valid input."""
        options = [("opt1", "Option 1")]
        result = main._numbered_menu("Test Menu", options)
        assert result == "opt1"

    @patch("builtins.input", side_effect=KeyboardInterrupt)
    def test_numbered_menu_keyboard_interrupt(self, mock_input):
        """Test KeyboardInterrupt returns '0'."""
        options = [("opt1", "Option 1")]
        result = main._numbered_menu("Test Menu", options)
        assert result == "0"


class TestProviderLoading:
    """Test provider loading from providers.yml."""

    def test_load_providers_for_phase_fallback(self):
        """Test fallback when providers.yml cannot be loaded."""
        with patch("main._load_config", side_effect=Exception("File not found")):
            providers = main._load_providers_for_phase(1)

        assert len(providers) >= 2
        assert any("Fireworks" in p[1] for p in providers)
        assert any("DashScope" in p[1] for p in providers)

    @patch("main._load_config")
    def test_load_providers_from_yml(self, mock_config):
        """Test loading providers from providers.yml."""
        mock_config.return_value = {
            "providers": [
                {
                    "id": "test_provider",
                    "phase": "phase1",
                    "name": "Test",
                    "model": "test-model",
                },
                {
                    "id": "other_provider",
                    "phase": "phase2",
                    "name": "Other",
                    "model": "other-model",
                },
            ]
        }

        providers = main._load_providers_for_phase(1)

        assert len(providers) == 1
        assert providers[0][0] == "test_provider"
        assert "Test" in providers[0][1]
        assert "test-model" in providers[0][1]


class TestStagePrepare:
    """Test _stage_prepare() function."""

    @patch("builtins.input", side_effect=["1", "1", "2025", "n", "y"])
    @patch("os.system")
    @patch("main.list_languages", return_value=["es", "pt", "en"])
    @patch("main.get")
    @patch("main.list_versions", return_value=["RVR1960", "NVI"])
    def test_stage_prepare_basic_flow(
        self, mock_list_vers, mock_get, mock_list_langs, mock_sys, mock_input
    ):
        """Test basic PREPARE flow."""
        from lang_registry import LangConfig

        mock_get.return_value = LangConfig(
            code="es",
            language_name="Spanish",
            country="Colombia",
            known_versions=("RVR1960", "NVI"),
            labels={},
            filename_pattern="test.json",
            persona="",
        )

        session = Session()
        result = main._stage_prepare(session)

        assert result.lang == "es"
        assert result.version == "RVR1960"
        assert result.year == 2025


class TestStageCritique:
    """Test _stage_critique() function."""

    @patch("main._run_cmd")
    @patch("builtins.input", side_effect=["1", "1", "1"])  # batch, phase1, provider1
    @patch("os.system")
    @patch(
        "main._load_providers_for_phase", return_value=[("test_prov", "Test Provider")]
    )
    def test_critique_batch_mode(self, mock_prov, mock_sys, mock_input, mock_run):
        """Test CRITIQUE in batch mode."""
        session = Session(lang="es", version="RVR1960", year=2025)
        main._stage_critique(session)

        # Verify _run_cmd was called with correct command
        assert mock_run.called
        cmd = mock_run.call_args[0][0]
        assert "batch_pipeline.py" in cmd
        assert "--lang" in cmd and "es" in cmd
        assert "--version" in cmd and "RVR1960" in cmd
        assert "--phase" in cmd

    @patch("main._run_cmd")
    @patch("builtins.input", side_effect=["2", "1", "1"])  # dry, phase1, provider1
    @patch("os.system")
    @patch(
        "main._load_providers_for_phase", return_value=[("test_prov", "Test Provider")]
    )
    def test_critique_dry_run_mode(self, mock_prov, mock_sys, mock_input, mock_run):
        """Test CRITIQUE in dry-run mode."""
        session = Session(lang="pt", version="NVI", year=2024)
        main._stage_critique(session)

        # Verify --dry-run flag is added
        cmd = mock_run.call_args[0][0]
        assert "--dry-run" in cmd

    @patch("main._run_cmd")
    @patch("builtins.input", side_effect=["3", "1"])  # overnight, phase1
    @patch("os.system")
    def test_critique_overnight_mode(self, mock_sys, mock_input, mock_run):
        """Test CRITIQUE in overnight mode."""
        session = Session(lang="en", version="KJV", year=2025)
        main._stage_critique(session)

        # Verify critic_v3.py is called
        cmd = mock_run.call_args[0][0]
        assert "critic_v3.py" in cmd
        assert "--mode" in cmd and "overnight" in cmd


class TestStageReview:
    """Test _stage_review() function."""

    @patch("main._run_cmd")
    @patch(
        "builtins.input", side_effect=["1", "1", "n"]
    )  # flags report, phase1, no specific files
    @patch("os.system")
    def test_review_flags_report(self, mock_sys, mock_input, mock_run):
        """Test REVIEW flags report."""
        session = Session(lang="es", version="RVR1960", year=2025)
        main._stage_review(session)

        cmd = mock_run.call_args[0][0]
        assert "review_flags.py" in cmd
        assert "--verdict" in cmd and "FLAG" in cmd

    @patch("main._run_cmd")
    @patch(
        "builtins.input", side_effect=["2", "1", "n"]
    )  # all report, phase1, no specific files
    @patch("os.system")
    def test_review_all_report(self, mock_sys, mock_input, mock_run):
        """Test REVIEW all verdicts report."""
        session = Session(lang="pt", version="NVI", year=2024)
        main._stage_review(session)

        cmd = mock_run.call_args[0][0]
        assert "--verdict" in cmd and "ALL" in cmd


class TestCommandRunner:
    """Test _run_cmd() function."""

    @patch("subprocess.run", return_value=MagicMock(returncode=0))
    @patch(
        "builtins.input", side_effect=["y", ""]
    )  # confirm run, press enter after completion
    def test_run_cmd_success(self, mock_input, mock_subprocess):
        """Test running command successfully."""
        cmd = [sys.executable, "test_script.py", "--arg", "value"]
        main._run_cmd(cmd)

        assert mock_subprocess.called
        assert mock_subprocess.call_args[0][0] == cmd

    @patch("subprocess.run", return_value=MagicMock(returncode=1))
    @patch("builtins.input", side_effect=["y", ""])
    def test_run_cmd_failure(self, mock_input, mock_subprocess):
        """Test running command that fails."""
        cmd = [sys.executable, "test_script.py"]
        main._run_cmd(cmd)

        assert mock_subprocess.called

    @patch("builtins.input", side_effect=["n"])
    def test_run_cmd_cancel(self, mock_input):
        """Test canceling command execution."""
        cmd = [sys.executable, "test_script.py"]
        with patch("subprocess.run") as mock_subprocess:
            main._run_cmd(cmd)
            assert not mock_subprocess.called


class TestGenomeStatus:
    """Test _show_genome_status() function."""

    @patch("builtins.input", return_value="")  # press enter to continue
    @patch("os.system")
    @patch("main.load_genome", return_value=None)
    def test_genome_status_no_genome(self, mock_load, mock_sys, mock_input):
        """Test genome status when no genome exists."""
        session = Session(lang="es", version="RVR1960", year=2025)
        main._show_genome_status(session)

        assert mock_load.called
        assert mock_load.call_args[0] == ("es", "RVR1960", 2025)


class TestPrintSessionRow:
    """Test _print_session_row() function."""

    @patch("main.get")
    def test_print_session_row_valid_lang(self, mock_get):
        """Test printing session row with valid language."""
        from lang_registry import LangConfig

        mock_get.return_value = LangConfig(
            code="es",
            language_name="Spanish",
            country="Colombia",
            known_versions=("RVR1960",),
            labels={},
            filename_pattern="test.json",
            persona="",
        )

        session = Session(lang="es", version="RVR1960", year=2025)
        # Should not raise any exceptions
        main._print_session_row(session)

    @patch("main.get", side_effect=ValueError("Unknown language"))
    def test_print_session_row_invalid_lang(self, mock_get):
        """Test printing session row with invalid language (fallback to code)."""
        session = Session(lang="invalid", version="RVR1960", year=2025)
        # Should not raise, should fallback to lang code
        main._print_session_row(session)


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_session_year_zero(self):
        """Test session with year 0."""
        s = Session(year=0)
        assert s.year == 0

    def test_session_negative_year(self):
        """Test session with negative year."""
        s = Session(year=-1)
        assert s.year == -1

    def test_session_very_large_year(self):
        """Test session with very large year."""
        s = Session(year=9999)
        assert s.year == 9999

    def test_empty_local_file_path(self):
        """Test session with empty local_file."""
        s = Session(local_file="")
        assert s.local_file == ""


class TestIntegration:
    """Integration tests for main.py."""

    @patch("builtins.input", side_effect=["0"])  # Exit immediately
    @patch("os.system")
    def test_main_exit_immediately(self, mock_sys, mock_input):
        """Test main() can exit cleanly."""
        main.main()
        assert mock_input.called

    def test_main_imports_successfully(self):
        """Test that main.py imports without errors."""
        import main

        assert hasattr(main, "main")
        assert hasattr(main, "Session")
        assert hasattr(main, "_stage_prepare")
        assert hasattr(main, "_stage_critique")
        assert hasattr(main, "_stage_review")


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v"])
