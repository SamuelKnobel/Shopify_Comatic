"""
sync_runner.py — CLI entry point for the daily cron job.

Usage:
    python sync_runner.py
    python sync_runner.py --hours 48    # override lookback window

Cron example (daily at 02:00):
    0 2 * * * /path/to/venv/bin/python /path/to/project/sync_runner.py >> /var/log/sync.log 2>&1

Exit codes:
    0 — success (even if some orders failed; they will retry tomorrow)
    1 — fatal unhandled exception (cron alert warranted)
"""
import asyncio
import argparse
import sys

from loguru import logger
from logging_setup import setup_logging
from database import init_db
from services.sync_engine import run_sync


async def main(hours: int) -> None:
    setup_logging()
    await init_db()
    stats = await run_sync(since_hours=hours)
    logger.info(
        "Sync complete. found={found} synced={synced} skipped={skipped} failed={failed}",
        **stats,
    )
    if stats["failed"] > 0:
        logger.warning(
            "{failed} order(s) failed to sync and will be retried on the next run.",
            failed=stats["failed"],
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Shopify → Comatic daily sync")
    parser.add_argument(
        "--hours",
        type=int,
        default=None,
        help="Lookback window in hours (default: SYNC_LOOKBACK_HOURS from .env)",
    )
    args = parser.parse_args()

    try:
        asyncio.run(main(args.hours))
    except Exception:
        logger.exception("Fatal error in sync_runner. Exiting with code 1.")
        sys.exit(1)
