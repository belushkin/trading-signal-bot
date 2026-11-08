"""Read-only API for persisted Stellar intelligence data."""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import quote, urlsplit, urlunsplit

from fastapi import FastAPI, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import SQLAlchemyError

from stellar_intelligence_bot.app.config import Settings
from stellar_intelligence_bot.storage.database import Database
from stellar_intelligence_bot.storage.models import (
    EventRecord,
    MarketSnapshotRecord,
    RuntimeHealthRecord,
    SignalEvaluationRecord,
    SignalRecord,
    TradeRecord,
)

SeverityFilter = Literal["INFO", "WATCH", "HIGH", "CRITICAL"]
CollectorStatus = Literal["healthy", "error", "stale", "starting", "not_started"]
CategoryFilter = Annotated[str | None, Query(max_length=64, pattern=r"^[a-z][a-z0-9_]*$")]


def create_app(
    *,
    database: Database | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    config = settings or Settings.from_env()
    refresh_seconds = _refresh_seconds()
    owns_database = database is None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.database = database or Database(config.database_url)
        app.state.settings = config
        app.state.refresh_seconds = refresh_seconds
        app.state.owns_database = owns_database
        try:
            yield
        finally:
            if owns_database:
                await app.state.database.close()

    app = FastAPI(
        title="Stellar Intelligence Dashboard API",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": _error_code(exc.status_code), "message": message}},
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "One or more request parameters are invalid",
                    "details": [
                        {
                            "field": ".".join(str(part) for part in error["loc"]),
                            "message": error["msg"],
                        }
                        for error in exc.errors()
                    ],
                }
            },
        )

    @app.get("/api/health", response_model=None)
    async def health(request: Request) -> dict[str, object] | JSONResponse:
        db: Database = request.app.state.database
        config: Settings = request.app.state.settings
        now = datetime.now(UTC)
        try:
            async with db.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            async with db.sessions() as session:
                collector = await session.get(RuntimeHealthRecord, "collector")
        except SQLAlchemyError:
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "service_unavailable",
                        "message": "Dashboard database is unavailable",
                    },
                    "database": {"status": "unavailable"},
                    "collector": {"status": "unknown"},
                    "checked_at": _iso(now),
                    "refresh_seconds": request.app.state.refresh_seconds,
                },
            )

        stale_after = _stale_after(config)
        last_success = (
            _as_utc(collector.last_success_at) if collector and collector.last_success_at else None
        )
        return {
            "database": {"status": "healthy"},
            "collector": {
                "status": _collector_status(collector, now, stale_after),
                "started_at": _iso(collector.started_at) if collector else None,
                "last_success_at": _iso(last_success) if last_success else None,
                "last_error_at": _iso(collector.last_error_at)
                if collector and collector.last_error_at
                else None,
                "last_error": collector.last_error if collector else None,
                "stale_after_seconds": int(stale_after.total_seconds()),
            },
            "checked_at": _iso(now),
            "refresh_seconds": request.app.state.refresh_seconds,
        }

    @app.get("/api/overview")
    async def overview(request: Request) -> dict[str, object]:
        db: Database = request.app.state.database
        config: Settings = request.app.state.settings
        now = datetime.now(UTC)
        try:
            async with db.sessions() as session:
                event_count = await session.scalar(select(func.count()).select_from(EventRecord))
                signal_count = await session.scalar(select(func.count()).select_from(SignalRecord))
                latest_event_at = await session.scalar(select(func.max(EventRecord.occurred_at)))
                latest_signal_at = await session.scalar(select(func.max(SignalRecord.occurred_at)))
                runtime = await session.get(RuntimeHealthRecord, "collector")
        except SQLAlchemyError as exc:
            raise HTTPException(503, "Dashboard data is temporarily unavailable") from exc

        last_success = (
            _as_utc(runtime.last_success_at) if runtime and runtime.last_success_at else None
        )
        stale_after = _stale_after(config)
        collector_status = _collector_status(runtime, now, stale_after)
        warnings: list[dict[str, str]] = []
        if not config.monitored_pairs:
            warnings.append(
                {
                    "code": "no_markets_configured",
                    "message": (
                        "No markets are configured. Set BOT_MONITORED_PAIRS to collect market data."
                    ),
                }
            )
        if runtime is None or runtime.started_at is None:
            warnings.append(
                {"code": "collector_not_started", "message": "The collector has not started."}
            )
        elif last_success is None:
            warnings.append(
                {
                    "code": "collector_starting",
                    "message": (
                        "The collector has started but no successful collection cycle "
                        "has completed yet."
                    ),
                }
            )
        elif now - last_success > stale_after:
            warnings.append(
                {"code": "collector_stale", "message": "No recent successful collection cycle."}
            )
        if runtime and runtime.last_error:
            warnings.append({"code": "collector_error", "message": runtime.last_error})
        if latest_event_at is None:
            warnings.append({"code": "no_events", "message": "No persisted Stellar events yet."})
        elif now - _as_utc(latest_event_at) > _stale_after(config):
            warnings.append(
                {"code": "event_data_stale", "message": "The latest persisted event is stale."}
            )

        try:
            pairs = await _market_summaries(db, config, now)
            recent_signals = await _recent_signals(db, limit=8)
        except SQLAlchemyError as exc:
            raise HTTPException(503, "Dashboard data is temporarily unavailable") from exc
        return {
            "collector": {
                "status": collector_status,
                "last_success_at": _iso(last_success) if last_success else None,
                "last_error_at": _iso(runtime.last_error_at)
                if runtime and runtime.last_error_at
                else None,
                "last_error": runtime.last_error if runtime else None,
            },
            "database": {"status": "healthy"},
            "counts": {"events": event_count or 0, "signals": signal_count or 0},
            "last_event_at": _iso(_as_utc(latest_event_at)) if latest_event_at else None,
            "last_signal_at": _iso(_as_utc(latest_signal_at)) if latest_signal_at else None,
            "data_freshness_seconds": (
                max(0, int((now - _as_utc(latest_event_at)).total_seconds()))
                if latest_event_at
                else None
            ),
            "monitored_pairs": [pair.key for pair in config.monitored_pairs],
            "markets": pairs,
            "recent_signals": recent_signals,
            "warnings": warnings,
            "generated_at": _iso(now),
        }

    @app.get("/api/markets")
    async def markets(request: Request) -> dict[str, object]:
        db: Database = request.app.state.database
        config: Settings = request.app.state.settings
        try:
            items = await _market_summaries(db, config, datetime.now(UTC))
        except SQLAlchemyError as exc:
            raise HTTPException(503, "Market data is temporarily unavailable") from exc
        return {
            "items": items,
            "monitored_pairs": [pair.key for pair in config.monitored_pairs],
            "message": None
            if items
            else "No markets are configured. Set BOT_MONITORED_PAIRS to monitor Stellar pairs.",
        }

    @app.get("/api/markets/{pair_id:path}")
    async def market_detail(
        request: Request,
        pair_id: Annotated[str, Path(min_length=1, max_length=255)],
        history_hours: Annotated[int, Query(ge=1, le=720)] = 24,
    ) -> dict[str, object]:
        config: Settings = request.app.state.settings
        pair = next(
            (candidate for candidate in config.monitored_pairs if candidate.key == pair_id), None
        )
        if pair is None:
            raise HTTPException(404, "Market is not in the configured monitored-pair list")
        db: Database = request.app.state.database
        now = datetime.now(UTC)
        cutoff = now - timedelta(hours=history_hours)
        try:
            summary = await _market_summary(db, pair.key, _stale_after(config), now)
            async with db.sessions() as session:
                snapshots = (
                    await session.scalars(
                        select(MarketSnapshotRecord)
                        .where(
                            MarketSnapshotRecord.pair_key == pair.key,
                            MarketSnapshotRecord.observed_at >= cutoff,
                        )
                        .order_by(MarketSnapshotRecord.observed_at.desc())
                        .limit(1000)
                    )
                ).all()
                trades = (
                    await session.scalars(
                        select(TradeRecord)
                        .where(TradeRecord.pair_key == pair.key, TradeRecord.occurred_at >= cutoff)
                        .order_by(TradeRecord.occurred_at.desc())
                        .limit(2000)
                    )
                ).all()
        except SQLAlchemyError as exc:
            raise HTTPException(503, "Market history is temporarily unavailable") from exc
        history = [
            {
                "observed_at": _iso(_as_utc(item.observed_at)),
                "price": _decimal((item.best_bid + item.best_ask) / 2)
                if item.best_bid is not None
                and item.best_ask is not None
                and item.best_bid > 0
                and item.best_ask >= item.best_bid
                else None,
                "bid_depth": _decimal(item.bid_depth),
                "ask_depth": _decimal(item.ask_depth),
            }
            for item in reversed(snapshots)
        ]
        volume_by_bucket: dict[str, Decimal] = {}
        for trade in trades:
            bucket = _as_utc(trade.occurred_at).replace(
                minute=(_as_utc(trade.occurred_at).minute // 15) * 15, second=0, microsecond=0
            )
            key = _iso(bucket)
            volume_by_bucket[key] = volume_by_bucket.get(key, Decimal(0)) + trade.base_amount
        return {
            "pair": pair.key,
            "summary": summary,
            "history": history,
            "volume_history": [
                {"observed_at": key, "base_volume": _decimal(value)}
                for key, value in sorted(volume_by_bucket.items())
            ],
            "history_hours": history_hours,
            "truncated": len(snapshots) == 1000 or len(trades) == 2000,
        }

    @app.get("/api/signals")
    async def signals(
        request: Request,
        pair: Annotated[str | None, Query(max_length=255)] = None,
        asset: Annotated[str | None, Query(max_length=160)] = None,
        category: CategoryFilter = None,
        severity: SeverityFilter | None = None,
        days: Annotated[int, Query(ge=1, le=3650)] = 30,
        q: Annotated[str | None, Query(max_length=160)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=100000)] = 0,
    ) -> dict[str, object]:
        db: Database = request.app.state.database
        query = select(SignalRecord).where(
            SignalRecord.occurred_at >= datetime.now(UTC) - timedelta(days=days)
        )
        if pair:
            query = query.where(
                or_(
                    SignalRecord.observed_facts["pair"].as_string() == pair,
                    SignalRecord.observed_facts["asset"].as_string() == pair,
                )
            )
        if asset:
            query = query.where(SignalRecord.observed_facts["asset"].as_string() == asset)
        if category:
            query = query.where(SignalRecord.category == category)
        if severity:
            query = query.where(SignalRecord.severity == severity)
        if q:
            query = query.join(EventRecord, EventRecord.event_id == SignalRecord.event_id)
            query = query.where(
                or_(
                    SignalRecord.signal_id.ilike(f"%{q}%"),
                    SignalRecord.category.ilike(f"%{q}%"),
                    SignalRecord.interpretation.ilike(f"%{q}%"),
                    EventRecord.source_ref.ilike(f"%{q}%"),
                )
            )
        try:
            async with db.sessions() as session:
                total = await session.scalar(select(func.count()).select_from(query.subquery()))
                rows = (
                    await session.scalars(
                        query.order_by(SignalRecord.occurred_at.desc()).limit(limit).offset(offset)
                    )
                ).all()
        except SQLAlchemyError as exc:
            raise HTTPException(503, "Signals are temporarily unavailable") from exc
        return {
            "items": [_signal_summary(row) for row in rows],
            "total": total or 0,
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/signals/{signal_id}")
    async def signal_detail(
        request: Request, signal_id: Annotated[str, Path(min_length=1, max_length=255)]
    ) -> dict[str, object]:
        db: Database = request.app.state.database
        try:
            async with db.sessions() as session:
                row = await session.get(SignalRecord, signal_id)
                if row is None:
                    raise HTTPException(404, "Signal was not found")
                event = await session.get(EventRecord, row.event_id)
                evaluations = (
                    await session.scalars(
                        select(SignalEvaluationRecord)
                        .where(SignalEvaluationRecord.signal_id == signal_id)
                        .order_by(SignalEvaluationRecord.horizon_minutes)
                    )
                ).all()
        except SQLAlchemyError as exc:
            raise HTTPException(503, "Signal detail is temporarily unavailable") from exc
        return {
            **_signal_summary(row),
            "observed_facts": _safe_facts(row.observed_facts),
            "interpretation": row.interpretation,
            "expires_at": _iso(_as_utc(row.expires_at)),
            "source": _event_source(event) if event else None,
            "evaluations": [_evaluation(item) for item in evaluations],
        }

    @app.get("/api/activity")
    async def activity(
        request: Request,
        days: Annotated[int, Query(ge=1, le=3650)] = 7,
        event_type: Literal["payment", "trade", "ledger"] | None = None,
        q: Annotated[str | None, Query(max_length=160)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=100000)] = 0,
    ) -> dict[str, object]:
        db: Database = request.app.state.database
        query = select(EventRecord).where(
            EventRecord.occurred_at >= datetime.now(UTC) - timedelta(days=days),
            EventRecord.event_type.in_(("payment", "trade", "ledger")),
        )
        if event_type:
            query = query.where(EventRecord.event_type == event_type)
        if q:
            query = query.where(
                or_(
                    EventRecord.event_id.ilike(f"%{q}%"),
                    EventRecord.event_type.ilike(f"%{q}%"),
                    EventRecord.source_ref.ilike(f"%{q}%"),
                )
            )
        try:
            async with db.sessions() as session:
                total = await session.scalar(select(func.count()).select_from(query.subquery()))
                rows = (
                    await session.scalars(
                        query.order_by(EventRecord.occurred_at.desc()).limit(limit).offset(offset)
                    )
                ).all()
                event_ids = [row.event_id for row in rows]
                matched_signals: dict[str, list[str]] = {}
                if event_ids:
                    matches = (
                        await session.execute(
                            select(SignalRecord.event_id, SignalRecord.category).where(
                                SignalRecord.event_id.in_(event_ids)
                            )
                        )
                    ).all()
                    for event_id, category_value in matches:
                        matched_signals.setdefault(event_id, []).append(category_value)
        except SQLAlchemyError as exc:
            raise HTTPException(503, "Activity is temporarily unavailable") from exc
        return {
            "items": [
                {
                    **_event_source(row),
                    "event_type": row.event_type,
                    "attributes": row.attributes,
                    "signal_categories": matched_signals.get(row.event_id, []),
                }
                for row in rows
            ],
            "total": total or 0,
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/analytics")
    async def analytics(
        request: Request,
        days: Annotated[int, Query(ge=1, le=3650)] = 30,
    ) -> dict[str, object]:
        db: Database = request.app.state.database
        cutoff = datetime.now(UTC) - timedelta(days=days)
        try:
            async with db.sessions() as session:
                by_category = (
                    await session.execute(
                        select(SignalRecord.category, func.count())
                        .where(SignalRecord.occurred_at >= cutoff)
                        .group_by(SignalRecord.category)
                        .order_by(func.count().desc())
                    )
                ).all()
                by_severity = (
                    await session.execute(
                        select(SignalRecord.severity, func.count())
                        .where(SignalRecord.occurred_at >= cutoff)
                        .group_by(SignalRecord.severity)
                    )
                ).all()
                frequency_rows = (
                    await session.execute(
                        select(func.date(SignalRecord.occurred_at), func.count())
                        .where(SignalRecord.occurred_at >= cutoff)
                        .group_by(func.date(SignalRecord.occurred_at))
                        .order_by(func.date(SignalRecord.occurred_at))
                    )
                ).all()
                evaluation_counts = (
                    await session.execute(
                        select(SignalEvaluationRecord.status, func.count())
                        .where(SignalEvaluationRecord.target_at >= cutoff)
                        .group_by(SignalEvaluationRecord.status)
                    )
                ).all()
                evaluation_groups = (
                    await session.execute(
                        select(
                            SignalEvaluationRecord.horizon_minutes,
                            SignalEvaluationRecord.status,
                            func.count(),
                            func.avg(SignalEvaluationRecord.return_percent),
                        )
                        .where(SignalEvaluationRecord.target_at >= cutoff)
                        .group_by(
                            SignalEvaluationRecord.horizon_minutes,
                            SignalEvaluationRecord.status,
                        )
                    )
                ).all()
                event_count = await session.scalar(
                    select(func.count())
                    .select_from(EventRecord)
                    .where(EventRecord.occurred_at >= cutoff)
                )
                market_count = await session.scalar(
                    select(func.count())
                    .select_from(MarketSnapshotRecord)
                    .where(MarketSnapshotRecord.observed_at >= cutoff)
                )
                trade_count = await session.scalar(
                    select(func.count())
                    .select_from(TradeRecord)
                    .where(TradeRecord.occurred_at >= cutoff)
                )
        except SQLAlchemyError as exc:
            raise HTTPException(503, "Analytics are temporarily unavailable") from exc

        counts = {status: count for status, count in evaluation_counts}
        total_evaluations = sum(counts.values())
        measured_count = counts.get("measured", 0)
        missing_count = sum(
            count for status, count in counts.items() if status.startswith("missing")
        )
        measured_by_horizon: dict[int, tuple[int, Decimal | None]] = {}
        for horizon, status, count, mean_change in evaluation_groups:
            if status == "measured" and isinstance(horizon, int) and isinstance(count, int):
                measured_by_horizon[horizon] = (
                    count,
                    Decimal(str(mean_change)) if mean_change is not None else None,
                )
        frequency_by_day = {str(day): count for day, count in frequency_rows}
        today = datetime.now(UTC).date()
        start_day = today - timedelta(days=days - 1)
        return {
            "period_days": days,
            "signals_by_category": [
                {"category": key, "count": count} for key, count in by_category
            ],
            "signals_by_severity": [
                {"severity": key, "count": count} for key, count in by_severity
            ],
            "signal_frequency": [
                {
                    "date": (start_day + timedelta(days=index)).isoformat(),
                    "count": frequency_by_day.get(
                        (start_day + timedelta(days=index)).isoformat(), 0
                    ),
                }
                for index in range(days)
            ],
            "price_evaluations": [
                {
                    "horizon_minutes": horizon,
                    "sample_count": sample_count,
                    "minimum_sample_count": 10,
                    "mean_observed_change_percent": _decimal(mean_change)
                    if sample_count >= 10 and mean_change is not None
                    else None,
                }
                for horizon, (sample_count, mean_change) in sorted(measured_by_horizon.items())
            ],
            "data_coverage": {
                "events": event_count or 0,
                "market_snapshots": market_count or 0,
                "trades": trade_count or 0,
                "evaluations": total_evaluations,
                "measured_evaluations": measured_count,
                "missing_evaluations": missing_count,
                "missing_observation_evaluations": counts.get("missing_observation", 0),
                "missing_observation_rate_percent": _decimal(
                    Decimal(counts.get("missing_observation", 0))
                    * 100
                    / Decimal(measured_count + counts.get("missing_observation", 0))
                )
                if measured_count + counts.get("missing_observation", 0)
                else None,
            },
        }

    return app


async def _market_summaries(
    database: Database, settings: Settings, now: datetime
) -> list[dict[str, object]]:
    return [
        await _market_summary(database, pair.key, _stale_after(settings), now)
        for pair in settings.monitored_pairs
    ]


async def _market_summary(
    database: Database, pair_key: str, stale_after: timedelta, now: datetime
) -> dict[str, object]:
    async with database.sessions() as session:
        latest = await session.scalar(
            select(MarketSnapshotRecord)
            .where(MarketSnapshotRecord.pair_key == pair_key)
            .order_by(MarketSnapshotRecord.observed_at.desc())
            .limit(1)
        )
        cutoff = now - timedelta(hours=24)
        first = await session.scalar(
            select(MarketSnapshotRecord)
            .where(
                MarketSnapshotRecord.pair_key == pair_key,
                MarketSnapshotRecord.observed_at >= cutoff,
                MarketSnapshotRecord.best_bid.is_not(None),
                MarketSnapshotRecord.best_ask.is_not(None),
            )
            .order_by(MarketSnapshotRecord.observed_at)
            .limit(1)
        )
        volume = await session.scalar(
            select(func.sum(TradeRecord.base_amount)).where(
                TradeRecord.pair_key == pair_key, TradeRecord.occurred_at >= cutoff
            )
        )
        latest_trade = await session.scalar(
            select(TradeRecord)
            .where(TradeRecord.pair_key == pair_key)
            .order_by(TradeRecord.occurred_at.desc())
            .limit(1)
        )
    latest_price = _mid(latest) if latest else None
    first_price = _mid(first) if first else None
    changed = (
        (latest_price / first_price - Decimal(1)) * Decimal(100)
        if latest_price is not None and first_price is not None and first_price > 0
        else None
    )
    observed_at = _as_utc(latest.observed_at) if latest else None
    is_stale = observed_at is None or now - observed_at > stale_after
    return {
        "pair": pair_key,
        "status": "no_data" if latest is None else "stale" if is_stale else "fresh",
        "latest_price": _decimal(latest_price),
        "price_change_24h_percent": _decimal(changed),
        "volume_24h_base": _decimal(volume),
        "last_trade_price": _decimal(latest_trade.price) if latest_trade else None,
        "last_trade_at": _iso(_as_utc(latest_trade.occurred_at)) if latest_trade else None,
        "best_bid": _decimal(latest.best_bid) if latest else None,
        "best_ask": _decimal(latest.best_ask) if latest else None,
        "spread_percent": _decimal(latest.spread_percent) if latest else None,
        "bid_depth": _decimal(latest.bid_depth) if latest else None,
        "ask_depth": _decimal(latest.ask_depth) if latest else None,
        "last_observed_at": _iso(observed_at) if observed_at else None,
    }


async def _recent_signals(database: Database, *, limit: int) -> list[dict[str, object]]:
    async with database.sessions() as session:
        rows = (
            await session.scalars(
                select(SignalRecord).order_by(SignalRecord.occurred_at.desc()).limit(limit)
            )
        ).all()
    return [_signal_summary(row) for row in rows]


def _signal_summary(row: SignalRecord) -> dict[str, object]:
    facts = row.observed_facts
    return {
        "signal_id": row.signal_id,
        "occurred_at": _iso(_as_utc(row.occurred_at)),
        "category": row.category,
        "severity": row.severity,
        "confidence": row.confidence,
        "pair": facts.get("pair"),
        "asset": facts.get("asset"),
        "interpretation": row.interpretation,
    }


def _event_source(row: EventRecord) -> dict[str, object]:
    source_ref = row.source_ref
    parsed_source = urlsplit(source_ref)
    safe_source_ref = (
        urlunsplit(("https", "stellar.expert", parsed_source.path, "", ""))
        if parsed_source.scheme == "https"
        and parsed_source.hostname == "stellar.expert"
        and parsed_source.username is None
        and parsed_source.password is None
        else None
    )
    tx_match = (
        re.fullmatch(r"/explorer/public/tx/([A-Fa-f0-9]{64})", parsed_source.path)
        if safe_source_ref
        else None
    )
    return {
        "event_id": row.event_id,
        "occurred_at": _iso(_as_utc(row.occurred_at)),
        "source_ref": safe_source_ref,
        "explorer_url": (
            f"https://stellar.expert/explorer/public/tx/{quote(tx_match.group(1), safe='')}"
            if tx_match
            else None
        ),
    }


def _safe_facts(facts: dict[str, object]) -> dict[str, object]:
    return {
        key: value for key, value in facts.items() if key not in {"reference_source", "source_ref"}
    }


def _evaluation(row: SignalEvaluationRecord) -> dict[str, object]:
    return {
        "horizon_minutes": row.horizon_minutes,
        "target_at": _iso(_as_utc(row.target_at)),
        "reference_price": _decimal(row.reference_price),
        "measured_price": _decimal(row.measured_price),
        "measured_at": _iso(_as_utc(row.measured_at)) if row.measured_at else None,
        "observed_change_percent": _decimal(row.return_percent),
        "status": row.status,
        "data_quality": row.data_quality,
        "observation_count": row.observation_count,
        "largest_gap_seconds": row.largest_gap_seconds,
    }


def _mid(row: MarketSnapshotRecord) -> Decimal | None:
    if (
        row.best_bid is None
        or row.best_ask is None
        or row.best_bid <= 0
        or row.best_ask < row.best_bid
    ):
        return None
    return (row.best_bid + row.best_ask) / Decimal(2)


def _decimal(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _stale_after(settings: Settings) -> timedelta:
    return timedelta(seconds=max(settings.poll_interval_seconds * 4, 300))


def _collector_status(
    runtime: RuntimeHealthRecord | None, now: datetime, stale_after: timedelta
) -> CollectorStatus:
    if runtime is None or runtime.started_at is None:
        return "not_started"
    if runtime.last_error:
        return "error"
    if runtime.last_success_at is None:
        return "starting"
    if now - _as_utc(runtime.last_success_at) > stale_after:
        return "stale"
    return "healthy"


def _refresh_seconds() -> int:
    raw = os.getenv("DASHBOARD_REFRESH_SECONDS", "20")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("DASHBOARD_REFRESH_SECONDS must be an integer") from exc
    if not 5 <= value <= 300:
        raise ValueError("DASHBOARD_REFRESH_SECONDS must be between 5 and 300 seconds")
    return value


def _error_code(status_code: int) -> str:
    return {
        400: "bad_request",
        404: "not_found",
        422: "validation_error",
        503: "service_unavailable",
    }.get(status_code, "request_error")


app = create_app()
