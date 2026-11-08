"""Application entry point for the read-only Stellar collector."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
from datetime import UTC, datetime, timedelta
from time import monotonic

import aiohttp
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import SQLAlchemyError

from stellar_intelligence_bot.app.config import Settings
from stellar_intelligence_bot.app.logging_config import configure_logging
from stellar_intelligence_bot.chains.stellar.client import HorizonClient
from stellar_intelligence_bot.chains.stellar.collector import StellarCollector
from stellar_intelligence_bot.storage.database import Database
from stellar_intelligence_bot.workers.processor import EventProcessor

logger = logging.getLogger(__name__)


async def run() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    await asyncio.to_thread(_upgrade_schema, settings.database_url)
    database = Database(settings.database_url)
    await database.initialize()
    started_at = datetime.now(UTC)
    await database.start_health(started_at)
    last_retention_run = 0.0
    loop = asyncio.get_running_loop()
    stop_requested = asyncio.Event()
    for stop_signal in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(stop_signal, stop_requested.set)
    try:
        async with HorizonClient(settings.horizon_url) as client:
            collector = StellarCollector(client, settings.monitored_pairs, database)
            processor = EventProcessor(
                database,
                whale_thresholds=settings.whale_thresholds,
                baseline_samples=settings.volume_baseline_samples,
                anomaly_multiplier=settings.volume_anomaly_multiplier,
                large_order_threshold=settings.large_order_threshold,
                spread_threshold_percent=settings.spread_threshold_percent,
            )
            delay = settings.poll_interval_seconds
            logger.info(
                "Starting read-only Stellar collector",
                extra={
                    "horizon_url": settings.horizon_url,
                    "monitored_pairs": [pair.key for pair in settings.monitored_pairs],
                },
            )
            try:
                while not stop_requested.is_set():
                    try:
                        async for item in collector.events():
                            await processor.process(item)
                        await database.evaluate_due_signals()
                        now_monotonic = monotonic()
                        if now_monotonic - last_retention_run >= 24 * 60 * 60:
                            pruned = await database.prune_before(
                                datetime.now(UTC) - timedelta(days=settings.retention_days)
                            )
                            logger.info("Pruned expired records", extra=pruned)
                            last_retention_run = now_monotonic
                        await database.record_health(started_at=started_at, successful=True)
                        delay = settings.poll_interval_seconds
                        await _wait_for_stop(stop_requested, settings.poll_interval_seconds)
                    except (
                        TimeoutError,
                        aiohttp.ClientError,
                        SQLAlchemyError,
                        ValueError,
                        KeyError,
                    ):
                        await database.record_health(
                            started_at=started_at,
                            successful=False,
                            error="Collection cycle failed; details are in application logs",
                        )
                        logger.exception(
                            "Stellar collection cycle failed; saved cursors will be retried",
                            extra={"retry_seconds": delay},
                        )
                        await _wait_for_stop(stop_requested, delay)
                        delay = min(delay * 2, 300)
            finally:
                await collector.close()
                logger.info("Collector stopped cleanly")
    finally:
        await database.close()
        for stop_signal in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(stop_signal)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Stellar intelligence service")
    subparsers = parser.add_subparsers(dest="command")
    report_parser = subparsers.add_parser(
        "report", help="Print descriptive signal evaluation statistics as JSON"
    )
    report_parser.add_argument("--since-days", type=int, default=30)
    report_parser.add_argument(
        "--horizon-minutes",
        type=int,
        choices=(5, 15, 60, 240, 1440),
    )
    subparsers.add_parser("health", help="Check database and collector heartbeat")
    args = parser.parse_args()
    try:
        if args.command == "report":
            if args.since_days <= 0:
                parser.error("--since-days must be positive")
            asyncio.run(_print_report(args.since_days, args.horizon_minutes))
        elif args.command == "health":
            asyncio.run(_check_health())
        else:
            asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Shutdown requested")


async def _print_report(since_days: int, horizon_minutes: int | None) -> None:
    settings = Settings.from_env()
    await asyncio.to_thread(_upgrade_schema, settings.database_url)
    database = Database(settings.database_url)
    try:
        rows = await database.evaluation_report(
            since=datetime.now(UTC) - timedelta(days=since_days),
            horizon_minutes=horizon_minutes,
        )
        print(json.dumps(rows, indent=2, default=str))
    finally:
        await database.close()


async def _check_health() -> None:
    settings = Settings.from_env()
    database = Database(settings.database_url)
    try:
        await database.check_connection()
        status = await database.health_status(
            stale_after=timedelta(seconds=max(settings.poll_interval_seconds * 4, 300))
        )
        print(json.dumps(status, indent=2, default=str))
        if not status["healthy"]:
            sys.exit(1)
    except SQLAlchemyError as exc:
        print(json.dumps({"healthy": False, "reason": str(exc)}), file=sys.stderr)
        sys.exit(1)
    finally:
        await database.close()


def _upgrade_schema(database_url: str) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")


async def _wait_for_stop(stop_requested: asyncio.Event, timeout: int) -> None:
    try:
        await asyncio.wait_for(stop_requested.wait(), timeout=timeout)
    except TimeoutError:
        pass
