"""Read-only API for persisted Stellar intelligence data."""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator, Sequence
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
from sqlalchemy.ext.asyncio import AsyncSession

from stellar_intelligence_bot.app.config import Settings
from stellar_intelligence_bot.app.trade_evidence import account_diagnostics, trade_window_evidence
from stellar_intelligence_bot.core.market_anomalies import (
    LARGE_TRADE_MIN_SAMPLES,
    BookObservation,
    market_readiness,
    trade_watermark,
    volume_coverage,
)
from stellar_intelligence_bot.core.markets import MarketPair
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
                "consecutive_failures": collector.consecutive_failures if collector else 0,
                "interruption_count": collector.interruption_count if collector else 0,
                "last_interruption_at": _iso(collector.last_interruption_at)
                if collector and collector.last_interruption_at
                else None,
                "skipped_trade_count": collector.skipped_trade_count if collector else 0,
                "last_skipped_at": _iso(collector.last_skipped_at)
                if collector and collector.last_skipped_at
                else None,
                "last_skipped_reason": collector.last_skipped_reason if collector else None,
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
                notable_events_24h = await session.scalar(
                    select(func.count())
                    .select_from(SignalRecord)
                    .where(
                        SignalRecord.occurred_at >= now - timedelta(hours=24),
                        SignalRecord.severity.in_(("WATCH", "HIGH", "CRITICAL")),
                    )
                )
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
                        "No markets are configured. Set BOT_WATCHLIST_ENABLED or "
                        "BOT_MONITORED_PAIRS to collect market data."
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
            recent_signals = await _recent_signals(db, limit=12)
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
                "consecutive_failures": runtime.consecutive_failures if runtime else 0,
                "interruption_count": runtime.interruption_count if runtime else 0,
                "skipped_trade_count": runtime.skipped_trade_count if runtime else 0,
                "last_skipped_reason": runtime.last_skipped_reason if runtime else None,
            },
            "database": {"status": "healthy"},
            "counts": {
                "events": event_count or 0,
                "signals": signal_count or 0,
                "notable_events_24h": notable_events_24h or 0,
            },
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
            for entry in config.watchlist:
                if not entry.enabled and entry.pair not in config.monitored_pairs:
                    summary = await _market_summary(db, entry.pair.key, config, datetime.now(UTC))
                    summary["tier"] = entry.tier
                    summary["enabled"] = False
                    items.append(summary)
        except SQLAlchemyError as exc:
            raise HTTPException(503, "Market data is temporarily unavailable") from exc
        return {
            "items": items,
            "monitored_pairs": [pair.key for pair in config.monitored_pairs],
            "message": None
            if items
            else "No markets are configured. Enable watchlist entries to monitor Stellar pairs.",
        }

    @app.get("/api/markets/{pair_id:path}")
    async def market_detail(
        request: Request,
        pair_id: Annotated[str, Path(min_length=1, max_length=255)],
        history_hours: Annotated[int, Query(ge=1, le=720)] = 24,
    ) -> dict[str, object]:
        config: Settings = request.app.state.settings
        candidates = config.monitored_pairs + tuple(entry.pair for entry in config.watchlist)
        pair = next((candidate for candidate in candidates if candidate.key == pair_id), None)
        if pair is None:
            raise HTTPException(404, "Market is not in the configured monitored-pair list")
        db: Database = request.app.state.database
        now = datetime.now(UTC)
        cutoff = now - timedelta(hours=history_hours)
        try:
            summary = await _market_summary(db, pair.key, config, now)
            summary["tier"] = next(
                (entry.tier for entry in config.watchlist if entry.pair == pair), None
            )
            summary["enabled"] = pair in config.monitored_pairs
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
                trade_events = (
                    await session.scalars(
                        select(EventRecord).where(
                            EventRecord.event_id.in_([item.trade_id for item in trades[:20]])
                        )
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
                "bid_depth": _decimal(item.bid_depth)
                if item.observed_facts.get("bid_depth_units") == "base"
                else None,
                "ask_depth": _decimal(item.ask_depth),
                "best_bid": _decimal(item.best_bid),
                "best_ask": _decimal(item.best_ask),
                "spread_percent": _decimal(item.spread_percent),
            }
            for item in reversed(snapshots)
        ]
        volume_by_bucket: dict[str, Decimal] = {}
        trade_types = {item.event_id: item.attributes.get("trade_type") for item in trade_events}
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
            "recent_trades": [
                {
                    "occurred_at": _iso(_as_utc(item.occurred_at)),
                    "price": _decimal(item.price) if item.quote_amount > 0 else None,
                    "base_amount": _decimal(item.base_amount),
                    "quote_amount": _decimal(item.quote_amount),
                    "trade_type": trade_types.get(item.trade_id),
                }
                for item in trades[:20]
            ],
            "data_source": "Persisted Horizon trades and order-book snapshots",
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
                summaries = await _summaries_with_outcomes(session, rows)
        except SQLAlchemyError as exc:
            raise HTTPException(503, "Signals are temporarily unavailable") from exc
        return {
            "items": summaries,
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
                evidence = await _volume_evidence(session, row)
        except SQLAlchemyError as exc:
            raise HTTPException(503, "Signal detail is temporarily unavailable") from exc
        return {
            **_signal_summary(row),
            "volume_evidence": evidence,
            "account_diagnostics_available": request.app.state.settings.account_diagnostics_enabled,
            "observed_facts": _safe_facts(row.observed_facts),
            "interpretation": row.interpretation,
            "expires_at": _iso(_as_utc(row.expires_at)),
            "deduplication_key": row.deduplication_key,
            "source": _event_source(event) if event else None,
            "evaluations": [_evaluation(item) for item in evaluations],
        }

    @app.get("/api/signals/{signal_id}/account-diagnostics")
    async def signal_account_diagnostics(
        request: Request, signal_id: Annotated[str, Path(min_length=1, max_length=255)]
    ) -> dict[str, object]:
        if not request.app.state.settings.account_diagnostics_enabled:
            raise HTTPException(404, "Account diagnostics are disabled")
        db: Database = request.app.state.database
        try:
            async with db.sessions() as session:
                row = await session.get(SignalRecord, signal_id)
                if row is None:
                    raise HTTPException(404, "Signal was not found")
                trades = await _window_trades(session, row)
        except SQLAlchemyError as exc:
            raise HTTPException(503, "Account diagnostics are temporarily unavailable") from exc
        if trades is None:
            raise HTTPException(404, "Signal has no trade window")
        return account_diagnostics(trades)

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
        current_engine = SignalRecord.observed_facts["engine_version"].as_string() == (
            "market-anomaly-v1"
        )
        try:
            async with db.sessions() as session:
                all_signals = await session.scalar(
                    select(func.count()).select_from(SignalRecord).where(
                        SignalRecord.occurred_at >= cutoff
                    )
                )
                by_category = (
                    await session.execute(
                        select(SignalRecord.category, func.count())
                        .where(SignalRecord.occurred_at >= cutoff, current_engine)
                        .group_by(SignalRecord.category)
                        .order_by(func.count().desc())
                    )
                ).all()
                by_severity = (
                    await session.execute(
                        select(SignalRecord.severity, func.count())
                        .where(SignalRecord.occurred_at >= cutoff, current_engine)
                        .group_by(SignalRecord.severity)
                    )
                ).all()
                frequency_rows = (
                    await session.execute(
                        select(func.date(SignalRecord.occurred_at), func.count())
                        .where(SignalRecord.occurred_at >= cutoff, current_engine)
                        .group_by(func.date(SignalRecord.occurred_at))
                        .order_by(func.date(SignalRecord.occurred_at))
                    )
                ).all()
                evaluation_counts = (
                    await session.execute(
                        select(SignalEvaluationRecord.status, func.count())
                        .join(
                            SignalRecord, SignalEvaluationRecord.signal_id == SignalRecord.signal_id
                        )
                        .where(SignalRecord.occurred_at >= cutoff, current_engine)
                        .group_by(SignalEvaluationRecord.status)
                    )
                ).all()
                evaluation_groups = (
                    await session.execute(
                        select(
                            SignalEvaluationRecord.horizon_minutes,
                            SignalEvaluationRecord.status,
                            SignalEvaluationRecord.price_basis,
                            func.count(),
                            func.avg(SignalEvaluationRecord.return_percent),
                        )
                        .join(
                            SignalRecord, SignalEvaluationRecord.signal_id == SignalRecord.signal_id
                        )
                        .where(SignalRecord.occurred_at >= cutoff, current_engine)
                        .group_by(
                            SignalEvaluationRecord.horizon_minutes,
                            SignalEvaluationRecord.status,
                            SignalEvaluationRecord.price_basis,
                        )
                    )
                ).all()
                trades_with_provenance = await session.scalar(
                    select(func.count())
                    .select_from(TradeRecord)
                    .where(TradeRecord.occurred_at >= cutoff, TradeRecord.venue.is_not(None))
                )
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
            outcome_report = await db.evaluation_report(
                since=cutoff, engine_version="market-anomaly-v1"
            )
            market_quality = await _market_summaries(
                db, request.app.state.settings, datetime.now(UTC)
            )
        except SQLAlchemyError as exc:
            raise HTTPException(503, "Analytics are temporarily unavailable") from exc

        counts = {status: count for status, count in evaluation_counts}
        total_evaluations = sum(counts.values())
        measured_count = counts.get("measured", 0)
        missing_count = sum(
            count for status, count in counts.items() if status.startswith("missing")
        )
        measured_by_horizon: dict[tuple[int, str], tuple[int, Decimal | None]] = {}
        measured_by_basis: dict[str, int] = {}
        for horizon, status, basis, count, mean_change in evaluation_groups:
            if status == "measured" and isinstance(horizon, int) and isinstance(count, int):
                label = str(basis or "snapshot_mid")
                measured_by_horizon[(horizon, label)] = (
                    count,
                    Decimal(str(mean_change)) if mean_change is not None else None,
                )
                measured_by_basis[label] = measured_by_basis.get(label, 0) + count
        frequency_by_day = {str(day): count for day, count in frequency_rows}
        today = datetime.now(UTC).date()
        start_day = today - timedelta(days=days - 1)
        return {
            "period_days": days,
            "market_quality": market_quality,
            "excluded_legacy_signals": (all_signals or 0) - sum(count for _, count in by_category),
            "outcomes_by_pair": [_public_outcome(row) for row in outcome_report],
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
                    "price_basis": basis,
                    "sample_count": sample_count,
                    "minimum_sample_count": 10,
                    "mean_observed_change_percent": _decimal(mean_change)
                    if sample_count >= 10 and mean_change is not None
                    else None,
                }
                for (horizon, basis), (sample_count, mean_change) in sorted(
                    measured_by_horizon.items()
                )
            ],
            "data_coverage": {
                "events": event_count or 0,
                "market_snapshots": market_count or 0,
                "trades": trade_count or 0,
                "trades_with_provenance": trades_with_provenance or 0,
                "measured_by_price_basis": dict(sorted(measured_by_basis.items())),
                "evaluations": total_evaluations,
                "measured_evaluations": measured_count,
                "missing_evaluations": missing_count,
                "pending_evaluations": counts.get("pending", 0),
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
    items = []
    for pair in settings.monitored_pairs:
        summary = await _market_summary(database, pair.key, settings, now)
        summary["tier"] = next(
            (entry.tier for entry in settings.watchlist if entry.pair == pair), None
        )
        summary["enabled"] = True
        items.append(summary)
    return items


def _public_outcome(row: dict[str, object]) -> dict[str, object]:
    result = dict(row)
    sample_count = row.get("measured")
    for key in (
        "mean_observed_change_percent",
        "median_observed_change_percent",
        "positive_percent",
        "negative_percent",
        "mean_max_observed_increase_percent",
        "mean_max_observed_decrease_percent",
    ):
        value = row.get(key)
        result[key] = (
            _decimal(value)
            if isinstance(sample_count, int) and sample_count >= 10 and isinstance(value, Decimal)
            else None
        )
    return result


async def _market_summary(
    database: Database, pair_key: str, settings: Settings, now: datetime
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
        volume, trade_count = (
            await session.execute(
                select(func.sum(TradeRecord.base_amount), func.count()).where(
                TradeRecord.pair_key == pair_key, TradeRecord.occurred_at >= cutoff
            )
            )
        ).one()
        snapshot_count = await session.scalar(
            select(func.count()).select_from(MarketSnapshotRecord).where(
                MarketSnapshotRecord.pair_key == pair_key
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
        if latest_price is not None
        and first_price is not None
        and first_price > 0
        and first is not None
        and _as_utc(first.observed_at) <= cutoff + _stale_after(settings)
        else None
    )
    observed_at = _as_utc(latest.observed_at) if latest else None
    is_stale = observed_at is None or now - observed_at > _stale_after(settings)
    last_trade_scan = trade_watermark(latest.observed_facts) if latest else None
    trade_scan_status = (
        "unverified"
        if last_trade_scan is None
        else "current"
        if not is_stale and timedelta(0) <= now - last_trade_scan <= timedelta(minutes=2)
        else "stale"
    )
    valid_book = (
        latest_price is not None
        and not is_stale
        and any(pair.key == pair_key for pair in settings.monitored_pairs)
    )
    readiness: dict[str, bool] = {}
    volume_ready = False
    large_trade_ready = False
    if (
        valid_book
        and observed_at is not None
        and timedelta(0) <= now - observed_at <= timedelta(minutes=2)
    ):
        rows = await database.book_observations(
            MarketPair.parse(pair_key),
            observed_at - timedelta(
                minutes=max(
                    60, max(settings.price_windows_minutes) * 4,
                    (settings.volume_baseline_samples + 2) * 5,
                )
            ),
            observed_at,
        )
        books = [
            BookObservation(
                _as_utc(row.observed_at), row.best_bid, row.best_ask,
                trade_watermark(row.observed_facts),
            )
            for row in rows
            if row.best_bid is not None and row.best_ask is not None
        ]
        readiness = market_readiness(books, observed_at, settings.price_windows_minutes)
        coverage = volume_coverage(books, observed_at, settings.volume_baseline_samples)
        if coverage is not None:
            volumes = await database.volume_windows(MarketPair.parse(pair_key), *coverage)
            volume_ready = sum(
                (value for start, value in volumes.items()
                 if start < coverage[1] - timedelta(minutes=5)),
                Decimal(0),
            ) > 0
        trade_coverage = volume_coverage(books, observed_at, 12)
        if trade_coverage is not None and MarketPair.parse(pair_key).base.code is None:
            trades = await database.trades_with_events(
                MarketPair.parse(pair_key), observed_at - timedelta(hours=1), observed_at
            )
            large_trade_ready = any(
                sum(source.attributes.get("trade_type") == venue for _, source in trades)
                >= LARGE_TRADE_MIN_SAMPLES
                for venue in ("orderbook", "liquidity_pool")
            )
    return {
        "pair": pair_key,
        "status": (
            "no_data"
            if latest is None
            else "stale"
            if is_stale
            else "no_data"
            if latest_price is None
            else "fresh"
        ),
        "latest_price": _decimal(latest_price),
        "price_change_24h_percent": _decimal(changed),
        "volume_24h_base": _decimal(volume),
        "trade_count_24h": trade_count if trade_count else None,
        "snapshot_count": snapshot_count,
        "trade_ingestion_status": trade_scan_status,
        "last_trade_scan_through": _iso(last_trade_scan) if last_trade_scan else None,
        "readiness": {
            "unusual_volume": {
                "ready": volume_ready,
                "requirement": (
                    f"{settings.volume_baseline_samples} prior complete five-minute windows, "
                    "positive mean, and continuous cursor-based trade scans with fresh "
                    "Horizon ledger watermarks."
                ),
            },
            "spread_anomaly": {
                "ready": readiness.get("spread_anomaly", False),
                "requirement": "Fresh two-sided book and 20 samples over a continuous hour.",
            },
            **{
                f"price_{minutes}m": {
                    "ready": readiness.get(f"price_{minutes}m", False),
                    "requirement": (
                        f"Four continuous {minutes}-minute windows with at least three "
                        "valid prices at each end; gaps at most two minutes."
                    ),
                }
                for minutes in settings.price_windows_minutes
            },
            "liquidity_change": {
                "ready": False,
                "requirement": (
                    "Not implemented: top-20 snapshots do not cover a fixed price range."
                ),
            },
            "large_trade": {
                "ready": large_trade_ready,
                "requirement": (
                    "At least 100 same-venue trades in a continuously covered hour; "
                    "a fresh trade must also exceed the XLM floor and 10 times the "
                    "pair/venue median quote notional."
                ),
            },
        },
        "last_trade_price": (
            _decimal(latest_trade.price)
            if latest_trade and latest_trade.quote_amount > 0 else None
        ),
        "last_trade_at": _iso(_as_utc(latest_trade.occurred_at)) if latest_trade else None,
        "best_bid": _decimal(latest.best_bid) if latest else None,
        "best_ask": _decimal(latest.best_ask) if latest else None,
        "spread_percent": _decimal(latest.spread_percent) if latest else None,
        "bid_depth": _decimal(latest.bid_depth)
        if latest and latest.observed_facts.get("bid_depth_units") == "base"
        else None,
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
        return await _summaries_with_outcomes(session, rows)


async def _summaries_with_outcomes(
    session: AsyncSession, rows: Sequence[SignalRecord]
) -> list[dict[str, object]]:
    """Summaries plus facts and price evaluations so cards can be explained without extra calls."""
    by_signal: dict[str, list[dict[str, object]]] = {row.signal_id: [] for row in rows}
    if by_signal:
        evaluations = (
            await session.scalars(
                select(SignalEvaluationRecord)
                .where(SignalEvaluationRecord.signal_id.in_(by_signal))
                .order_by(SignalEvaluationRecord.horizon_minutes)
            )
        ).all()
        for evaluation in evaluations:
            by_signal[evaluation.signal_id].append(_evaluation(evaluation))
    return [
        {
            **_signal_summary(row),
            "observed_facts": _safe_facts(row.observed_facts),
            "expires_at": _iso(_as_utc(row.expires_at)),
            "evaluations": by_signal[row.signal_id],
        }
        for row in rows
    ]


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
    if (
        safe_source_ref is None
        and parsed_source.scheme == "https"
        and parsed_source.hostname == "horizon.stellar.org"
        and parsed_source.username is None
        and parsed_source.password is None
        and re.fullmatch(r"/(?:trades|operations)/[A-Za-z0-9-]+", parsed_source.path)
    ):
        safe_source_ref = urlunsplit(("https", "horizon.stellar.org", parsed_source.path, "", ""))
    tx_match = (
        re.fullmatch(r"/explorer/public/tx/([A-Fa-f0-9]{64})", parsed_source.path)
        if safe_source_ref and parsed_source.hostname == "stellar.expert"
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


async def _window_trades(session: AsyncSession, signal: SignalRecord) -> list[TradeRecord] | None:
    facts = signal.observed_facts
    pair = facts.get("pair")
    try:
        start = datetime.fromisoformat(str(facts["window_start"]))
        end = datetime.fromisoformat(str(facts["window_end"]))
    except (KeyError, ValueError):
        return None
    if signal.category != "volume_anomaly" or not isinstance(pair, str):
        return None
    return list(
        (
            await session.scalars(
                select(TradeRecord)
                .where(
                    TradeRecord.pair_key == pair,
                    TradeRecord.occurred_at >= start,
                    TradeRecord.occurred_at < end,
                )
                .order_by(TradeRecord.occurred_at, TradeRecord.trade_id)
            )
        ).all()
    )


async def _volume_evidence(
    session: AsyncSession, signal: SignalRecord
) -> dict[str, object] | None:
    trades = await _window_trades(session, signal)
    if trades is None:
        return None
    evidence = trade_window_evidence(trades)
    stored = _decimal_or_none(signal.observed_facts.get("observed_base_volume"))
    evidence["signal_observed_base_volume"] = _decimal(stored)
    evidence["matches_signal_volume"] = stored is not None and stored == sum(
        (trade.base_amount for trade in trades), Decimal(0)
    )
    return evidence


def _decimal_or_none(value: object) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None


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
        "price_basis": _evaluation_basis(row),
        "observation_ref": row.observation_ref,
        "reference_observed_at": _iso(row.reference_observed_at)
        if row.reference_observed_at
        else None,
        "missing_reason": _missing_reason(row),
    }


def _evaluation_basis(row: SignalEvaluationRecord) -> str | None:
    if row.status != "measured":
        return None
    return row.price_basis or "snapshot_mid"


def _missing_reason(row: SignalEvaluationRecord) -> str | None:
    if not row.status.startswith("missing"):
        return None
    if row.missing_reason:
        return row.missing_reason
    return "no_snapshot_in_grace_window_legacy_no_trade_fallback"


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
