import logging
import sys
import os
from pathlib import Path
from typing import Optional

DEFAULT_LOG_DIR = Path.home() / ".net_deepagent" / "logs"

# ---------------------------------------------------------------------------
# Register custom NOTICE level (25 — between INFO=20 and WARNING=30, per syslog)
# ---------------------------------------------------------------------------
_NOTICE_LEVEL = 25
if not hasattr(logging, "NOTICE"):
    logging.addLevelName(_NOTICE_LEVEL, "NOTICE")
    logging.NOTICE = _NOTICE_LEVEL  # type: ignore[attr-defined]
    # Convenience method on Logger instances: logger.notice("msg")
    def _notice(self, message, *args, **kwargs):
        if self.isEnabledFor(_NOTICE_LEVEL):
            self._log(_NOTICE_LEVEL, message, args, **kwargs)
    logging.Logger.notice = _notice  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
_PROCESS_LOG_FILE: Optional[Path] = None

# File handler level  → controlled by --log-level
_PROCESS_LOG_LEVEL: int = logging.INFO

# Console/screen handler level → controlled by --monitor (falls back to _PROCESS_LOG_LEVEL)
_PROCESS_SCREEN_LEVEL: int = logging.INFO


def _effective_logger_level() -> int:
    """
    The logger-level gate must be the most permissive (lowest) of the two
    handler levels so that records are not dropped before reaching either handler.
    """
    return min(_PROCESS_LOG_LEVEL, _PROCESS_SCREEN_LEVEL)


def set_process_log_file(log_file: str):
    """
    Sets a unified log file for the current process.
    All subsequent calls to setup_logger without an explicit log_file will use this.
    Also configures the root logger to catch all unconfigured module logs.
    """
    global _PROCESS_LOG_FILE
    path = Path(log_file)
    if not path.is_absolute():
        path = DEFAULT_LOG_DIR / path
    _PROCESS_LOG_FILE = path

    # Ensure directory exists immediately
    _PROCESS_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(_effective_logger_level())

    # Clear existing handlers
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # Root Console Handler — gated at _PROCESS_SCREEN_LEVEL
    root_console_handler = logging.StreamHandler(sys.stdout)
    root_console_handler.setFormatter(formatter)
    root_console_handler.setLevel(_PROCESS_SCREEN_LEVEL)
    root_logger.addHandler(root_console_handler)

    # Root File Handler — gated at _PROCESS_LOG_LEVEL
    try:
        root_file_handler = logging.FileHandler(str(_PROCESS_LOG_FILE))
        root_file_handler.setFormatter(formatter)
        root_file_handler.setLevel(_PROCESS_LOG_LEVEL)
        root_logger.addHandler(root_file_handler)
    except Exception as e:
        print(f"Warning: Could not setup root file logging at {_PROCESS_LOG_FILE}: {e}")


def setup_logger(name: str, log_file: Optional[str] = None, level=None):
    """
    Function to setup a logger.
    Priority for file destination:
    1. Explicit log_file argument
    2. Global _PROCESS_LOG_FILE (if set)
    3. Default to name.log in DEFAULT_LOG_DIR

    Console handler level respects _PROCESS_SCREEN_LEVEL.
    File handler level respects _PROCESS_LOG_LEVEL.
    The logger itself is set to the most permissive of the two.
    """

    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # Create logger
    logger = logging.getLogger(name)
    effective = _effective_logger_level() if level is None else level
    logger.setLevel(effective)

    # Disable propagation to root to avoid double logging
    logger.propagate = False

    # Avoid duplicate handlers if setup is called multiple times
    if logger.hasHandlers():
        logger.handlers.clear()

    # Console handler — gated at _PROCESS_SCREEN_LEVEL
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(_PROCESS_SCREEN_LEVEL)
    logger.addHandler(console_handler)

    # Determine final log path
    final_path: Path
    if log_file is not None:
        path = Path(log_file)
        final_path = path if path.is_absolute() else DEFAULT_LOG_DIR / path
    elif _PROCESS_LOG_FILE is not None:
        final_path = _PROCESS_LOG_FILE
    else:
        final_path = DEFAULT_LOG_DIR / f"{name.replace('.', '_')}.log"

    # File handler — gated at _PROCESS_LOG_LEVEL
    try:
        final_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(str(final_path))
        file_handler.setFormatter(formatter)
        file_handler.setLevel(_PROCESS_LOG_LEVEL)
        logger.addHandler(file_handler)
    except Exception as e:
        print(f"Warning: Could not setup file logging at {final_path}: {e}")

    return logger


def set_log_level(level):
    """
    Dynamically update the FILE handler log level for all project loggers.
    'level' can be a string (e.g., 'DEBUG') or a logging level constant.

    The effective logger gate is automatically adjusted to min(file_level, screen_level)
    so that neither handler ever drops records it should receive.
    """
    global _PROCESS_LOG_LEVEL

    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    _PROCESS_LOG_LEVEL = level
    effective = _effective_logger_level()

    # Update root logger gate + its file handlers
    root = logging.getLogger()
    root.setLevel(effective)
    for handler in root.handlers:
        if isinstance(handler, logging.FileHandler):
            handler.setLevel(_PROCESS_LOG_LEVEL)

    # Update all project-namespace loggers
    project_namespaces = ("net_deepagent_cli", "communication", "utils", "a2a_capability", "net_deepagent")
    for name in logging.root.manager.loggerDict:
        if any(name.startswith(ns) for ns in project_namespaces):
            lg = logging.getLogger(name)
            lg.setLevel(effective)
            for handler in lg.handlers:
                if isinstance(handler, logging.FileHandler):
                    handler.setLevel(_PROCESS_LOG_LEVEL)

    # Update comm_logger itself
    comm_logger.setLevel(effective)
    for handler in comm_logger.handlers:
        if isinstance(handler, logging.FileHandler):
            handler.setLevel(_PROCESS_LOG_LEVEL)

    comm_logger.info(f"File log level set to {logging.getLevelName(_PROCESS_LOG_LEVEL)}")


def set_screen_log_level(level):
    """
    Dynamically update the SCREEN (console) handler log level for all project loggers.
    File handlers are unaffected.

    The effective logger gate is automatically adjusted to min(file_level, screen_level)
    so that records are never dropped before reaching the screen handler.
    """
    global _PROCESS_SCREEN_LEVEL

    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    _PROCESS_SCREEN_LEVEL = level
    effective = _effective_logger_level()

    # Update root logger gate + its console handlers
    root = logging.getLogger()
    root.setLevel(effective)
    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            handler.setLevel(_PROCESS_SCREEN_LEVEL)

    # Update all project-namespace loggers
    project_namespaces = ("net_deepagent_cli", "communication", "utils", "a2a_capability", "net_deepagent")
    for name in logging.root.manager.loggerDict:
        if any(name.startswith(ns) for ns in project_namespaces):
            lg = logging.getLogger(name)
            lg.setLevel(effective)
            for handler in lg.handlers:
                if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
                    handler.setLevel(_PROCESS_SCREEN_LEVEL)

    # Update comm_logger itself
    comm_logger.setLevel(effective)
    for handler in comm_logger.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            handler.setLevel(_PROCESS_SCREEN_LEVEL)

    comm_logger.info(f"Screen log level set to {logging.getLevelName(_PROCESS_SCREEN_LEVEL)}")


# Default logger for the communication module
# Initially setup, but will follow the process log if set later
comm_logger = setup_logger("communication")
