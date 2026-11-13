"""Environment-backed application settings."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from importlib.resources import files

from stellar_intelligence_bot.core.markets import MarketPair, StellarAsset


@dataclass(frozen=True, slots=True)
class WatchlistEntry:
    pair: MarketPair
    tier: int
    enabled: bool


def _watchlist() -> tuple[WatchlistEntry, ...]:
    records = json.loads(
        files("stellar_intelligence_bot.app").joinpath("watchlist.json").read_text()
    )
    entries = tuple(
        WatchlistEntry(MarketPair.parse(row["pair"]), row["tier"], row["enabled"])
        for row in records
    )
    if any(
        type(entry.tier) is not int or entry.tier not in (1, 2) or type(entry.enabled) is not bool
        for entry in entries
    ):
        raise ValueError("Invalid watchlist tier or enabled setting")
    if len({entry.pair.key for entry in entries}) != len(entries):
        raise ValueError("Duplicate watchlist pair")
    if len({entry.pair.quote.code for entry in entries}) != len(entries):
        raise ValueError("Watchlist asset codes must be unique for enabled selection")
    return entries


def _usdc_cross_markets(watchlist: tuple[WatchlistEntry, ...]) -> tuple[WatchlistEntry, ...]:
    assets = {entry.pair.quote.code: entry for entry in watchlist}
    usdc = assets["USDC"]
    return tuple(
        WatchlistEntry(
            MarketPair(usdc.pair.quote, assets[code].pair.quote),
            tier=1,
            enabled=usdc.enabled and assets[code].enabled,
        )
        for code in ("VELO", "SHX", "AQUA")
    )


@dataclass(frozen=True, slots=True)
class Settings:
    log_level: str = "INFO"
    horizon_url: str = "https://horizon.stellar.org"
    database_url: str = "sqlite+aiosqlite:///./stellar-intelligence.db"
    poll_interval_seconds: int = 15
    monitored_pairs: tuple[MarketPair, ...] = ()
    watchlist: tuple[WatchlistEntry, ...] = ()
    whale_thresholds: dict[str, Decimal] = field(default_factory=dict)
    volume_baseline_samples: int = 20
    volume_anomaly_multiplier: Decimal = Decimal("3")
    large_order_threshold: Decimal = Decimal("10000")
    large_trade_min_xlm: Decimal = Decimal("10000")
    spread_threshold_percent: Decimal = Decimal("1")
    price_anomaly_floor_percent: Decimal = Decimal("1")
    price_windows_minutes: tuple[int, ...] = (5, 15, 60, 240)
    retention_days: int = 30
    account_diagnostics_enabled: bool = False

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
            large_trade_min = Decimal(os.getenv("BOT_LARGE_TRADE_MIN_XLM", "10000"))
            spread_threshold = Decimal(os.getenv("BOT_SPREAD_THRESHOLD_PERCENT", "1"))
            price_floor = Decimal(os.getenv("BOT_PRICE_ANOMALY_FLOOR_PERCENT", "1"))
            price_windows = tuple(
                int(value.strip())
                for value in os.getenv("BOT_PRICE_WINDOWS_MINUTES", "5,15,60,240").split(",")
            )
            retention_days = int(os.getenv("BOT_RETENTION_DAYS", str(cls().retention_days)))
        except (ValueError, InvalidOperation) as exc:
            raise ValueError("Numeric BOT settings contain an invalid value") from exc
        if poll_interval < 5:
            raise ValueError("BOT_POLL_INTERVAL_SECONDS must be at least 5")
        if baseline_samples < 3:
            raise ValueError("BOT_VOLUME_BASELINE_SAMPLES must be at least 3")
        if retention_days < 1:
            raise ValueError("BOT_RETENTION_DAYS must be at least 1")
        if not price_windows or len(set(price_windows)) != len(price_windows) or any(
            value not in (5, 15, 60, 240) for value in price_windows
        ):
            raise ValueError("BOT_PRICE_WINDOWS_MINUTES must select unique windows: 5,15,60,240")
        if (
            not anomaly_multiplier.is_finite()
            or not large_order_threshold.is_finite()
            or not large_trade_min.is_finite()
            or not spread_threshold.is_finite()
            or not price_floor.is_finite()
            or anomaly_multiplier <= 1
            or large_order_threshold <= 0
            or large_trade_min <= 0
            or spread_threshold <= 0
            or price_floor <= 0
        ):
            raise ValueError("Volume multiplier must exceed 1 and order threshold must be positive")

        watchlist = _watchlist()
        selected = os.getenv("BOT_WATCHLIST_ENABLED")
        if selected is not None:
            names = {name.strip() for name in selected.split(",") if name.strip()}
            valid = {entry.pair.quote.code for entry in watchlist}
            if names - valid:
                raise ValueError(f"Unknown BOT_WATCHLIST_ENABLED entries: {sorted(names - valid)}")
            watchlist = tuple(
                WatchlistEntry(entry.pair, entry.tier, entry.pair.quote.code in names)
                for entry in watchlist
            )
        watchlist += _usdc_cross_markets(watchlist)
        legacy_pairs = tuple(
            MarketPair.parse(value.strip())
            for value in os.getenv("BOT_MONITORED_PAIRS", "").split(",")
            if value.strip()
        )
        if legacy_pairs:
            watchlist = tuple(
                WatchlistEntry(entry.pair, entry.tier, entry.pair in legacy_pairs)
                for entry in watchlist
            )
            pairs = legacy_pairs
        else:
            pairs = tuple(entry.pair for entry in watchlist if entry.enabled)
        if len({pair.key for pair in pairs}) != len(pairs):
            raise ValueError("BOT_MONITORED_PAIRS must not contain duplicate pairs")
        return cls(
            log_level=log_level,
            horizon_url=os.getenv("BOT_HORIZON_URL", cls().horizon_url).rstrip("/"),
            database_url=os.getenv("BOT_DATABASE_URL", cls().database_url),
            poll_interval_seconds=poll_interval,
            monitored_pairs=pairs,
            watchlist=watchlist,
            whale_thresholds=_parse_thresholds(os.getenv("BOT_WHALE_THRESHOLDS", "")),
            volume_baseline_samples=baseline_samples,
            volume_anomaly_multiplier=anomaly_multiplier,
            large_order_threshold=large_order_threshold,
            large_trade_min_xlm=large_trade_min,
            spread_threshold_percent=spread_threshold,
            price_anomaly_floor_percent=price_floor,
            price_windows_minutes=price_windows,
            retention_days=retention_days,
            account_diagnostics_enabled=os.getenv("BOT_ACCOUNT_DIAGNOSTICS", "").lower()
            in {"1", "true", "yes"},
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
