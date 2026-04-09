"""
Logging Configuration

Sets up rotating file handler, stream handler, and standard formatting
for the AgentClaw application.
"""

import logging
from logging.handlers import RotatingFileHandler

from config.settings import settings


def setup_logging() -> None:
    """
    Configure logging with rotating file handler and stream handler.

    - RotatingFileHandler: 10MB max size with 3 backup files
    - StreamHandler: Console output for INFO+ level
    - Format: "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    """

    # Create root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(settings.LOG_LEVEL)

    # Formatter
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Rotating file handler (10MB, 3 backups)
    file_handler = RotatingFileHandler(
        settings.LOG_FILE,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=3,
    )
    file_handler.setLevel(settings.LOG_LEVEL)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # Stream handler (console)
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(settings.LOG_LEVEL)
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)

    # Log startup
    logger = logging.getLogger(__name__)
    logger.info(f"Logging initialized: level={settings.LOG_LEVEL}, file={settings.LOG_FILE}")
