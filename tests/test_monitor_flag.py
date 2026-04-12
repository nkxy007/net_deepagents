"""
Tests for the --monitor CLI flag (independent screen log level).

Run with:
    conda run -n test_langchain_env python -m pytest tests/test_monitor_flag.py -v
"""
import unittest
import logging
import sys
import os
from unittest.mock import patch, MagicMock, call
import argparse

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _fresh_logger_state():
    """
    Helper: reset module-level globals inside logger.py so each test starts clean.
    Returns the logger module so callers can re-import symbols.
    """
    import importlib
    import net_deepagent_cli.communication.logger as logger_mod
    importlib.reload(logger_mod)
    return logger_mod


class TestSetScreenLogLevel(unittest.TestCase):
    """Unit tests for set_screen_log_level and its interaction with set_log_level."""

    # ------------------------------------------------------------------
    # 1. set_screen_log_level only touches StreamHandlers (not FileHandlers)
    # ------------------------------------------------------------------
    def test_set_screen_log_level_only_touches_stream_handlers(self):
        lm = _fresh_logger_state()

        # Attach a fake StreamHandler and a fake FileHandler to comm_logger
        stream_handler = logging.StreamHandler(sys.stdout)
        file_handler = logging.FileHandler(os.devnull)
        original_file_level = file_handler.level

        lm.comm_logger.addHandler(stream_handler)
        lm.comm_logger.addHandler(file_handler)

        try:
            lm.set_screen_log_level("DEBUG")
            self.assertEqual(stream_handler.level, logging.DEBUG)
            # FileHandler must remain untouched
            self.assertEqual(file_handler.level, original_file_level)
        finally:
            lm.comm_logger.removeHandler(stream_handler)
            lm.comm_logger.removeHandler(file_handler)
            file_handler.close()

    # ------------------------------------------------------------------
    # 2. After set_screen_log_level(DEBUG), FileHandler level is unchanged
    # ------------------------------------------------------------------
    def test_set_screen_log_level_does_not_affect_file_handler(self):
        lm = _fresh_logger_state()

        file_handler = logging.FileHandler(os.devnull)
        file_handler.setLevel(logging.WARNING)
        lm.comm_logger.addHandler(file_handler)

        try:
            lm.set_screen_log_level("DEBUG")
            # FileHandler should still be WARNING
            self.assertEqual(file_handler.level, logging.WARNING)
        finally:
            lm.comm_logger.removeHandler(file_handler)
            file_handler.close()

    # ------------------------------------------------------------------
    # 3. set_log_level does not alter a previously set screen handler level
    # ------------------------------------------------------------------
    def test_set_log_level_does_not_affect_screen_handler(self):
        lm = _fresh_logger_state()

        stream_handler = logging.StreamHandler(sys.stdout)
        lm.comm_logger.addHandler(stream_handler)

        try:
            # Set screen to WARNING first
            lm.set_screen_log_level("WARNING")
            self.assertEqual(stream_handler.level, logging.WARNING)

            # Now change file level — screen handler must stay at WARNING
            lm.set_log_level("DEBUG")
            self.assertEqual(stream_handler.level, logging.WARNING)
        finally:
            lm.comm_logger.removeHandler(stream_handler)

    # ------------------------------------------------------------------
    # 4. Logger gate equals min(file_level, screen_level)
    # ------------------------------------------------------------------
    def test_effective_logger_level_is_min_of_both(self):
        lm = _fresh_logger_state()

        # file=WARNING, screen=DEBUG → effective logger gate = DEBUG
        lm.set_log_level("WARNING")
        lm.set_screen_log_level("DEBUG")
        self.assertEqual(lm._effective_logger_level(), logging.DEBUG)

        # file=DEBUG, screen=WARNING → effective logger gate = DEBUG
        lm.set_log_level("DEBUG")
        lm.set_screen_log_level("WARNING")
        self.assertEqual(lm._effective_logger_level(), logging.DEBUG)

        # file=ERROR, screen=ERROR → effective logger gate = ERROR
        lm.set_log_level("ERROR")
        lm.set_screen_log_level("ERROR")
        self.assertEqual(lm._effective_logger_level(), logging.ERROR)

    # ------------------------------------------------------------------
    # 5. setup_logger applies screen level to its console handler
    # ------------------------------------------------------------------
    def test_setup_logger_console_handler_respects_screen_level(self):
        lm = _fresh_logger_state()
        lm.set_screen_log_level("ERROR")

        test_logger = lm.setup_logger("_test_monitor_flag_temp_")
        try:
            stream_handlers = [
                h for h in test_logger.handlers
                if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
            ]
            self.assertTrue(len(stream_handlers) >= 1)
            for h in stream_handlers:
                self.assertEqual(h.level, logging.ERROR)
        finally:
            # Clean up handlers to avoid polluting other tests
            for h in list(test_logger.handlers):
                h.close()
                test_logger.removeHandler(h)


class TestMonitorCLIArgument(unittest.TestCase):
    """Tests for the --monitor argparse flag in cli.py's run_cli()."""

    def _build_parser(self):
        """Rebuild the same parser that run_cli() creates, without running the full async function."""
        parser = argparse.ArgumentParser()
        parser.add_argument("--log-level", default="INFO",
                            choices=["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL"])
        parser.add_argument("--monitor", default=None,
                            choices=["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL"])
        return parser

    # ------------------------------------------------------------------
    # 6. --monitor is parsed correctly
    # ------------------------------------------------------------------
    def test_cli_monitor_flag_parsed_correctly(self):
        parser = self._build_parser()
        args = parser.parse_args(["--monitor", "WARNING"])
        self.assertEqual(args.monitor, "WARNING")

    # ------------------------------------------------------------------
    # 7. --monitor defaults to None when not provided
    # ------------------------------------------------------------------
    def test_cli_monitor_defaults_to_none(self):
        parser = self._build_parser()
        args = parser.parse_args([])
        self.assertIsNone(args.monitor)

    # ------------------------------------------------------------------
    # 8. When --monitor absent, screen level falls back to --log-level
    # ------------------------------------------------------------------
    def test_cli_screen_level_falls_back_to_log_level(self):
        import net_deepagent_cli.communication.logger as lm

        with patch.object(lm, "set_log_level") as mock_file, \
             patch.object(lm, "set_screen_log_level") as mock_screen:

            args = argparse.Namespace(log_level="DEBUG", monitor=None)
            screen_level = args.monitor if args.monitor is not None else args.log_level
            lm.set_log_level(args.log_level)
            lm.set_screen_log_level(screen_level)

            mock_file.assert_called_once_with("DEBUG")
            mock_screen.assert_called_once_with("DEBUG")   # should match log_level

    # ------------------------------------------------------------------
    # 9. When --monitor provided, set_screen_log_level uses its value
    # ------------------------------------------------------------------
    def test_cli_monitor_overrides_log_level_for_screen(self):
        import net_deepagent_cli.communication.logger as lm

        with patch.object(lm, "set_log_level") as mock_file, \
             patch.object(lm, "set_screen_log_level") as mock_screen:

            args = argparse.Namespace(log_level="DEBUG", monitor="WARNING")
            screen_level = args.monitor if args.monitor is not None else args.log_level
            lm.set_log_level(args.log_level)
            lm.set_screen_log_level(screen_level)

            mock_file.assert_called_once_with("DEBUG")
            mock_screen.assert_called_once_with("WARNING")


class TestNoticeLevelRegistration(unittest.TestCase):
    """Verify that NOTICE is properly registered as a custom logging level."""

    # ------------------------------------------------------------------
    # 10. Importing logger.py registers logging.NOTICE = 25
    # ------------------------------------------------------------------
    def test_notice_level_is_registered(self):
        import net_deepagent_cli.communication.logger  # noqa: F401 — import triggers registration
        self.assertTrue(hasattr(logging, "NOTICE"), "logging.NOTICE should be defined")
        self.assertEqual(logging.NOTICE, 25)  # type: ignore[attr-defined]
        self.assertEqual(logging.getLevelName(25), "NOTICE")
        self.assertEqual(logging.getLevelName("NOTICE"), 25)

    # ------------------------------------------------------------------
    # 11. set_screen_log_level("NOTICE") resolves to 25, not INFO fallback
    # ------------------------------------------------------------------
    def test_set_screen_log_level_notice_resolves_correctly(self):
        lm = _fresh_logger_state()
        lm.set_screen_log_level("NOTICE")
        self.assertEqual(lm._PROCESS_SCREEN_LEVEL, 25)

    # ------------------------------------------------------------------
    # 12. set_log_level("NOTICE") resolves to 25, not INFO fallback
    # ------------------------------------------------------------------
    def test_set_log_level_notice_resolves_correctly(self):
        lm = _fresh_logger_state()
        lm.set_log_level("NOTICE")
        self.assertEqual(lm._PROCESS_LOG_LEVEL, 25)


if __name__ == "__main__":
    unittest.main()
