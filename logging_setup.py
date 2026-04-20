"""
logging_setup.py — Loguru configuration (console + rotating file).
Import this module once at entry-point; everywhere else just use `from loguru import logger`.
"""
import sys
from loguru import logger
from config import settings


def setup_logging() -> None:
    logger.remove()  # Remove default handler

    # Console — human-readable
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> — <level>{message}</level>",
        level="DEBUG",
        colorize=True,
    )

    # File — structured JSON for machine consumption
    logger.add(
        settings.log_file,
        format="{time} | {level} | {name}:{function}:{line} — {message}",
        level="DEBUG",
        rotation="10 MB",
        retention="30 days",
        compression="gz",
        serialize=False,
    )

    logger.info("Logging initialised. File={log_file}", log_file=settings.log_file)
