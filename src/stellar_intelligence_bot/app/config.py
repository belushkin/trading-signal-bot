"""Environment-backed application settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from stellar_intelligence_bot.core.markets import MarketPair, StellarAsset


@dataclass(frozen=True, slots=True)
class Settings:
    log_level: str = "INFO"
    horizon_url: str = "https://horizon.stellar.org"
    database_url: str = "sqlite+aiosqlite:///./stellar-intelligence.db"
    poll_interval_seconds: int = 15
    monitored_pairs: tuple[MarketPair, ...] = ()
    whale_thresholds: dict[str, Decimal] = field(default_factory=dict)
    volume_baseline_samples: int = 20
    volume_anomaly_multiplier: Decimal = Decimal("3")
    large_order_threshold: Decimal = Decimal("10000")
    spread_threshold_percent: Decimal = Decimal("1")
    retention_days: int = 90

    @classmethod
    def from_env(cls) -> Settings:
        log_level = os.getenv("BOT_LOG_LEVEL", cls().log_level).upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"Unsupported BOT_LOG_LEVEL: {log_level}")
        try:
            poll_interval = int(os.getenv("BOT_POLL_INTERVAL_SECONDS", "15"))
            baseline_samples = int(os.getenv("BOT_VOLUME_BASELINE_SAMPLES", "20"))
            anomaly_multiplier = Decimal(os.getenv("BOT_VOLUME_ANOMALY_MULTIPLIER", "3"))
            large_order_threshold = Decimal(os.getenv("BOT_LARGE_ORDER_THRESHOLD", "10000"))
            spread_threshold = Decimal(os.getenv("BOT_SPREAD_THRESHOLD_PERCENT", "1"))
            retention_days = int(os.getenv("BOT_RETENTION_DAYS", "90"))
        except (ValueError, InvalidOperation) as exc:
            raise ValueError("Numeric BOT settings contain an invalid value") from exc
        if poll_interval < 5:
            raise ValueError("BOT_POLL_INTERVAL_SECONDS must be at least 5")
        if baseline_samples < 3:
            raise ValueError("BOT_VOLUME_BASELINE_SAMPLES must be at least 3")
        if retention_days < 1:
            raise ValueError("BOT_RETENTION_DAYS must be at least 1")
        if (
            not anomaly_multiplier.is_finite()
            or not large_order_threshold.is_finite()
            or not spread_threshold.is_finite()
            or anomaly_multiplier <= 1
            or large_order_threshold <= 0
            or spread_threshold <= 0
        ):
            raise ValueError("Volume multiplier must exceed 1 and order threshold must be positive")

        pairs = tuple(
            MarketPair.parse(value.strip())
            for value in os.getenv("BOT_MONITORED_PAIRS", "").split(",")
            if value.strip()
        )
        if len({pair.key for pair in pairs}) != len(pairs):
            raise ValueError("BOT_MONITORED_PAIRS must not contain duplicate pairs")
        return cls(
            log_level=log_level,
            horizon_url=os.getenv("BOT_HORIZON_URL", cls().horizon_url).rstrip("/"),
            database_url=os.getenv("BOT_DATABASE_URL", cls().database_url),
            poll_interval_seconds=poll_interval,
            monitored_pairs=pairs,
            whale_thresholds=_parse_thresholds(os.getenv("BOT_WHALE_THRESHOLDS", "")),
            volume_baseline_samples=baseline_samples,
            volume_anomaly_multiplier=anomaly_multiplier,
            large_order_threshold=large_order_threshold,
            spread_threshold_percent=spread_threshold,
            retention_days=retention_days,
        )


def _parse_thresholds(value: str) -> dict[str, Decimal]:
    thresholds: dict[str, Decimal] = {}
    for item in value.split(","):
        if not item.strip():
            continue
        try:
            asset, amount = item.strip().rsplit("=", maxsplit=1)
            key = StellarAsset.parse(asset.strip()).key
            threshold = Decimal(amount.strip())
        except (ValueError, InvalidOperation) as exc:
            raise ValueError(f"Invalid whale threshold {item!r}; expected ASSET=AMOUNT") from exc
        if not threshold.is_finite() or threshold <= 0:
            raise ValueError(f"Whale threshold for {key} must be a positive finite amount")
        if key in thresholds:
            raise ValueError(f"Duplicate whale threshold for {key}")
        thresholds[key] = threshold
    return thresholds
