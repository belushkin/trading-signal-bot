"""Async persistence for events, market data, signals, and evaluations."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from statistics import median
from typing import Any

from sqlalchemy import delete, desc, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from stellar_intelligence_bot.chains.base import TradeProvenance
from stellar_intelligence_bot.core.events import ChainEvent
from stellar_intelligence_bot.core.markets import MarketPair
from stellar_intelligence_bot.core.signals import Signal
from stellar_intelligence_bot.storage.models import (
    Base,
    CursorRecord,
    EventRecord,
    JsonDocument,
    MarketSnapshotRecord,
    RuntimeHealthRecord,
    SignalEvaluationRecord,
    SignalRecord,
    TradeRecord,
    VolumeWindowRecord,
)

EVALUATION_HORIZONS_MINUTES = (5, 15, 60, 240, 1440)
EVALUATION_OBSERVATION_GRACE = timedelta(minutes=5)
# Trade-price fallback: used only when no valid order-book snapshot exists within the
# snapshot grace window. The measured trade must fall in the same grace window, so the
# fallback never delays a verdict. Prices are never interpolated; each endpoint is a real trade.
TRADE_REFERENCE_LOOKBACK = timedelta(minutes=15)
MIN_TRADE_OBSERVATIONS = 3
PRICE_BASIS_SNAPSHOT = "snapshot_mid"
PRICE_BASIS_TRADE = "trade_price"


class Database:
    def __init__(self, url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(url, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def initialize(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        await self.engine.dispose()

    async def start_health(self, started_at: datetime) -> None:
        async with self.sessions.begin() as session:
            record = await session.get(RuntimeHealthRecord, "collector")
            if record is None:
                record = RuntimeHealthRecord(
                    health_key="collector",
                    started_at=_as_utc(started_at),
                )
                session.add(record)
            else:
                record.started_at = _as_utc(started_at)
                record.last_success_at = None
                record.last_error_at = None
                record.last_error = None

    async def record_health(
        self,
        *,
        started_at: datetime,
        successful: bool,
        error: str | None = None,
        now: datetime | None = None,
    ) -> None:
        recorded_at = _as_utc(now or datetime.now(UTC))
        async with self.sessions.begin() as session:
            record = await session.get(RuntimeHealthRecord, "collector")
            if record is None:
                record = RuntimeHealthRecord(
                    health_key="collector",
                    started_at=_as_utc(started_at),
                )
                session.add(record)
            if successful:
                record.last_success_at = recorded_at
                record.last_error = None
                record.consecutive_failures = 0
            else:
                if (record.consecutive_failures or 0) == 0:
                    record.interruption_count = (record.interruption_count or 0) + 1
                    record.last_interruption_at = recorded_at
                record.consecutive_failures = (record.consecutive_failures or 0) + 1
                record.last_error_at = recorded_at
                record.last_error = (error or "Unknown collector failure")[:2000]

    async def apply_trade_provenance(
        self, entries: list[tuple[str, TradeProvenance, Decimal, Decimal]]
    ) -> dict[str, int]:
        """Explicit backfill: fill empty provenance columns of already-stored trades.

        Never inserts trades, never overwrites a populated column, and skips records whose
        amounts differ from the stored trade. Safe to run repeatedly.
        """
        counts = {"updated": 0, "already_populated": 0, "not_stored": 0, "amount_mismatch": 0}
        async with self.sessions.begin() as session:
            for trade_id, provenance, base_amount, quote_amount in entries:
                trade = await session.get(TradeRecord, trade_id)
                if trade is None:
                    counts["not_stored"] += 1
                    continue
                if trade.base_amount != base_amount or trade.quote_amount != quote_amount:
                    counts["amount_mismatch"] += 1
                    continue
                changed = False
                for column, value in _provenance_columns(provenance).items():
                    if value is not None and getattr(trade, column) is None:
                        setattr(trade, column, value)
                        changed = True
                counts["updated" if changed else "already_populated"] += 1
        return counts

    async def trade_provenance_gaps(
        self, pair: MarketPair, since: datetime
    ) -> tuple[datetime, datetime, str] | None:
        """Earliest/latest time and first trade id of stored trades lacking provenance."""
        async with self.sessions() as session:
            row = (
                await session.execute(
                    select(
                        func.min(TradeRecord.occurred_at),
                        func.max(TradeRecord.occurred_at),
                    ).where(
                        TradeRecord.pair_key == pair.key,
                        TradeRecord.venue.is_(None),
                        TradeRecord.occurred_at >= since,
                    )
                )
            ).one()
            if row[0] is None:
                return None
            first = await session.scalar(
                select(TradeRecord.trade_id)
                .where(
                    TradeRecord.pair_key == pair.key,
                    TradeRecord.venue.is_(None),
                    TradeRecord.occurred_at == row[0],
                )
                .order_by(TradeRecord.trade_id)
                .limit(1)
            )
        return _as_utc(row[0]), _as_utc(row[1]), str(first)

    async def record_skipped_trade(
        self,
        reason: str,
        *,
        started_at: datetime | None = None,
        stream_key: str | None = None,
        cursor: str | None = None,
        now: datetime | None = None,
    ) -> None:
        """Count a skipped trade and advance its watermark in one transaction."""
        recorded_at = _as_utc(now or datetime.now(UTC))
        async with self.sessions.begin() as session:
            record = await session.get(RuntimeHealthRecord, "collector")
            if record is None:
                record = RuntimeHealthRecord(
                    health_key="collector", started_at=_as_utc(started_at or recorded_at)
                )
                session.add(record)
            record.skipped_trade_count = (record.skipped_trade_count or 0) + 1
            record.last_skipped_at = recorded_at
            record.last_skipped_reason = reason[:2000]
            if stream_key is not None and cursor is not None:
                await _save_cursor(session, stream_key, cursor)

    async def health_status(
        self, *, stale_after: timedelta, now: datetime | None = None
    ) -> dict[str, object]:
        current_time = _as_utc(now or datetime.now(UTC))
        async with self.sessions() as session:
            record = await session.get(RuntimeHealthRecord, "collector")
        if record is None:
            return {"healthy": False, "reason": "collector has not reported status"}
        last_success = _as_utc(record.last_success_at) if record.last_success_at else None
        healthy = (
            last_success is not None
            and current_time - last_success <= stale_after
            and record.last_error is None
        )
        return {
            "healthy": healthy,
            "started_at": _as_utc(record.started_at),
            "last_success_at": last_success,
            "last_error_at": _as_utc(record.last_error_at) if record.last_error_at else None,
            "last_error": record.last_error,
            "consecutive_failures": record.consecutive_failures or 0,
            "interruption_count": record.interruption_count or 0,
            "skipped_trade_count": record.skipped_trade_count or 0,
            "reason": None
            if healthy
            else "no recent successful collection cycle or latest cycle failed",
        }

    async def check_connection(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def prune_before(self, cutoff: datetime) -> dict[str, int]:
        boundary = _as_utc(cutoff)
        async with self.sessions.begin() as session:
            old_event_ids = select(EventRecord.event_id).where(EventRecord.occurred_at < boundary)
            signal_predicate = or_(
                SignalRecord.occurred_at < boundary,
                SignalRecord.event_id.in_(old_event_ids),
            )
            old_signal_ids = select(SignalRecord.signal_id).where(signal_predicate)
            evaluations = await _delete_count(
                session,
                SignalEvaluationRecord,
                SignalEvaluationRecord.signal_id.in_(old_signal_ids),
            )
            signals = await _delete_count(
                session,
                SignalRecord,
                signal_predicate,
            )
            trades = await _delete_count(session, TradeRecord, TradeRecord.occurred_at < boundary)
            snapshots = await _delete_count(
                session,
                MarketSnapshotRecord,
                MarketSnapshotRecord.observed_at < boundary,
            )
            windows = await _delete_count(
                session,
                VolumeWindowRecord,
                VolumeWindowRecord.window_start < boundary,
            )
            events = await _delete_count(session, EventRecord, EventRecord.occurred_at < boundary)
        return {
            "events": events,
            "trades": trades,
            "market_snapshots": snapshots,
            "volume_windows": windows,
            "signals": signals,
            "signal_evaluations": evaluations,
        }

    async def get_cursor(self, stream_key: str) -> str | None:
        async with self.sessions() as session:
            record = await session.get(CursorRecord, stream_key)
            return record.cursor if record else None

    async def advance_cursor(self, stream_key: str, cursor: str) -> None:
        async with self.sessions.begin() as session:
            await _save_cursor(session, stream_key, cursor)

    async def save_event_with_cursor(self, event: ChainEvent, stream_key: str, cursor: str) -> bool:
        async with self.sessions.begin() as session:
            if await session.get(EventRecord, event.event_id) is not None:
                await _save_cursor(session, stream_key, cursor)
                return False
            session.add(_event_record(event))
            await _save_cursor(session, stream_key, cursor)
            return True

    async def save_trade_event(
        self,
        event: ChainEvent,
        stream_key: str,
        cursor: str,
        pair: MarketPair,
        base_amount: Decimal,
        quote_amount: Decimal,
        provenance: TradeProvenance | None = None,
    ) -> bool:
        if base_amount <= 0 or quote_amount < 0:
            raise ValueError("Trade base amount must be positive and quote amount nonnegative")
        if quote_amount == 0:
            price = _optional_decimal(event.attributes.get("price"))
            if (
                price is None
                or price <= 0
                or event.attributes.get("price_source") != "horizon_rational"
            ):
                raise ValueError("Zero-quote trade requires a positive Horizon rational price")
        else:
            price = quote_amount / base_amount
        window_start = _five_minute_window(event.occurred_at)
        async with self.sessions.begin() as session:
            if await session.get(EventRecord, event.event_id) is not None:
                await _save_cursor(session, stream_key, cursor)
                return False
            session.add(_event_record(event))
            session.add(
                TradeRecord(
                    trade_id=event.event_id,
                    pair_key=pair.key,
                    occurred_at=event.occurred_at,
                    base_amount=base_amount,
                    quote_amount=quote_amount,
                    price=price,
                    source_ref=event.source_ref,
                    **_provenance_columns(provenance),
                )
            )
            window = await session.scalar(
                select(VolumeWindowRecord).where(
                    VolumeWindowRecord.pair_key == pair.key,
                    VolumeWindowRecord.window_start == window_start,
                )
            )
            if window is None:
                session.add(
                    VolumeWindowRecord(
                        pair_key=pair.key,
                        window_start=window_start,
                        base_volume=base_amount,
                        trade_count=1,
                    )
                )
            else:
                window.base_volume += base_amount
                window.trade_count += 1
            await _save_cursor(session, stream_key, cursor)
            return True

    async def save_signal(self, signal: Signal, cooldown: timedelta) -> bool:
        async with self.sessions.begin() as session:
            if await session.get(SignalRecord, signal.signal_id) is not None:
                return False
            recent = await session.scalar(
                select(SignalRecord.signal_id)
                .where(
                    SignalRecord.deduplication_key == signal.deduplication_key,
                    SignalRecord.occurred_at >= signal.occurred_at - cooldown,
                    SignalRecord.occurred_at <= signal.occurred_at,
                )
                .limit(1)
            )
            if recent is not None:
                return False

            session.add(
                SignalRecord(
                    signal_id=signal.signal_id,
                    deduplication_key=signal.deduplication_key,
                    event_id=signal.event_id,
                    occurred_at=signal.occurred_at,
                    category=signal.category,
                    severity=signal.severity.value,
                    confidence=signal.confidence.value,
                    observed_facts=_json_values(signal.observed_facts),
                    interpretation=signal.interpretation,
                    expires_at=signal.expires_at,
                )
            )
            pair_key = signal.observed_facts.get("pair")
            if isinstance(pair_key, str):
                await session.flush()
                reference = _optional_decimal(signal.observed_facts.get("reference_price"))
                reference_source = signal.observed_facts.get("reference_source")
                for horizon in EVALUATION_HORIZONS_MINUTES:
                    session.add(
                        SignalEvaluationRecord(
                            signal_id=signal.signal_id,
                            pair_key=pair_key,
                            horizon_minutes=horizon,
                            target_at=signal.occurred_at + timedelta(minutes=horizon),
                            reference_at=signal.occurred_at,
                            reference_price=reference,
                            reference_source=(
                                reference_source if isinstance(reference_source, str) else None
                            ),
                            status=(
                                "pending"
                                if reference is not None and reference > 0
                                else "missing_reference"
                            ),
                            data_quality=(
                                "pending"
                                if reference is not None and reference > 0
                                else "missing_reference"
                            ),
                            evaluated_at=(
                                None
                                if reference is not None and reference > 0
                                else signal.occurred_at
                            ),
                        )
                    )
            return True

    async def volume_baseline(
        self, pair: MarketPair, samples: int, current_window: datetime
    ) -> tuple[Decimal, int] | None:
        async with self.sessions() as session:
            records = (
                await session.scalars(
                    select(VolumeWindowRecord)
                    .where(
                        VolumeWindowRecord.pair_key == pair.key,
                        VolumeWindowRecord.window_start < current_window,
                    )
                    .order_by(desc(VolumeWindowRecord.window_start))
                    .limit(samples)
                )
            ).all()
        if len(records) < samples:
            return None
        mean = sum((record.base_volume for record in records), Decimal(0)) / Decimal(len(records))
        return mean, len(records)

    async def current_window_volume(self, pair: MarketPair, window_start: datetime) -> Decimal:
        async with self.sessions() as session:
            record = await session.scalar(
                select(VolumeWindowRecord).where(
                    VolumeWindowRecord.pair_key == pair.key,
                    VolumeWindowRecord.window_start == window_start,
                )
            )
            return record.base_volume if record else Decimal(0)

    async def book_observations(
        self, pair: MarketPair, since: datetime, until: datetime
    ) -> list[MarketSnapshotRecord]:
        async with self.sessions() as session:
            return list(
                (
                    await session.scalars(
                        select(MarketSnapshotRecord)
                        .where(
                            MarketSnapshotRecord.pair_key == pair.key,
                            MarketSnapshotRecord.observed_at >= since,
                            MarketSnapshotRecord.observed_at <= until,
                            MarketSnapshotRecord.best_bid > 0,
                            MarketSnapshotRecord.best_ask >= MarketSnapshotRecord.best_bid,
                        )
                        .order_by(MarketSnapshotRecord.observed_at)
                    )
                ).all()
            )

    async def volume_windows(
        self, pair: MarketPair, start: datetime, end: datetime
    ) -> dict[datetime, Decimal]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(VolumeWindowRecord).where(
                        VolumeWindowRecord.pair_key == pair.key,
                        VolumeWindowRecord.window_start >= start,
                        VolumeWindowRecord.window_start < end,
                    )
                )
            ).all()
        return {_as_utc(row.window_start): row.base_volume for row in rows}

    async def trades_with_events(
        self, pair: MarketPair, start: datetime, end: datetime
    ) -> list[tuple[TradeRecord, EventRecord]]:
        async with self.sessions() as session:
            rows = await session.execute(
                select(TradeRecord, EventRecord)
                .join(EventRecord, EventRecord.event_id == TradeRecord.trade_id)
                .where(
                    TradeRecord.pair_key == pair.key,
                    TradeRecord.occurred_at >= start,
                    TradeRecord.occurred_at <= end,
                )
                .order_by(TradeRecord.occurred_at, TradeRecord.trade_id)
            )
            return [(trade, event) for trade, event in rows]

    async def save_market_snapshot(
        self,
        event: ChainEvent,
        pair: MarketPair,
        observed_at: datetime,
        best_bid: Decimal | None,
        best_ask: Decimal | None,
        bid_depth: Decimal,
        ask_depth: Decimal,
        facts: dict[str, object],
    ) -> bool:
        spread = (
            (best_ask - best_bid) / best_ask * Decimal(100)
            if best_bid is not None
            and best_ask is not None
            and best_ask > 0
            and best_ask >= best_bid
            else None
        )
        async with self.sessions.begin() as session:
            if await session.get(EventRecord, event.event_id) is not None:
                return False
            session.add(_event_record(event))
            session.add(
                MarketSnapshotRecord(
                    pair_key=pair.key,
                    observed_at=observed_at,
                    best_bid=best_bid,
                    best_ask=best_ask,
                    bid_depth=bid_depth,
                    ask_depth=ask_depth,
                    spread_percent=spread,
                    observed_facts=_json_values(facts),
                )
            )
            return True

    async def evaluate_due_signals(
        self,
        *,
        now: datetime | None = None,
        grace: timedelta = EVALUATION_OBSERVATION_GRACE,
        batch_size: int = 500,
    ) -> int:
        evaluation_time = _as_utc(now or datetime.now(UTC))
        finalized = 0
        async with self.sessions.begin() as session:
            pending = (
                await session.scalars(
                    select(SignalEvaluationRecord)
                    .where(
                        SignalEvaluationRecord.status == "pending",
                        SignalEvaluationRecord.target_at <= evaluation_time,
                    )
                    .order_by(SignalEvaluationRecord.target_at)
                    .limit(batch_size)
                )
            ).all()
            for evaluation in pending:
                deadline = _as_utc(evaluation.target_at) + grace
                observation = await session.scalar(
                    select(MarketSnapshotRecord)
                    .where(
                        MarketSnapshotRecord.pair_key == evaluation.pair_key,
                        MarketSnapshotRecord.observed_at >= evaluation.target_at,
                        MarketSnapshotRecord.observed_at <= min(evaluation_time, deadline),
                        MarketSnapshotRecord.best_bid.is_not(None),
                        MarketSnapshotRecord.best_ask.is_not(None),
                        MarketSnapshotRecord.best_bid > 0,
                        MarketSnapshotRecord.best_ask >= MarketSnapshotRecord.best_bid,
                    )
                    .order_by(MarketSnapshotRecord.observed_at)
                    .limit(1)
                )
                if observation is None:
                    if evaluation_time < deadline:
                        continue
                    outcome = await _apply_trade_fallback(
                        session, evaluation, evaluation_time, grace
                    )
                    if outcome == "missing":
                        evaluation.status = "missing_observation"
                        evaluation.data_quality = "missing_observation"
                        evaluation.evaluated_at = evaluation_time
                    finalized += 1
                    continue

                reference = evaluation.reference_price
                measured_price = _mid_price(observation)
                if reference is None or reference <= 0:
                    evaluation.status = "missing_reference"
                    evaluation.data_quality = "missing_reference"
                    evaluation.missing_reason = "no_reference_snapshot_price"
                    evaluation.evaluated_at = evaluation_time
                    finalized += 1
                    continue
                if measured_price is None or measured_price <= 0:
                    continue

                series = (
                    await session.scalars(
                        select(MarketSnapshotRecord)
                        .where(
                            MarketSnapshotRecord.pair_key == evaluation.pair_key,
                            MarketSnapshotRecord.observed_at >= evaluation.reference_at,
                            MarketSnapshotRecord.observed_at <= evaluation.target_at,
                            MarketSnapshotRecord.best_bid.is_not(None),
                            MarketSnapshotRecord.best_ask.is_not(None),
                            MarketSnapshotRecord.best_bid > 0,
                            MarketSnapshotRecord.best_ask >= MarketSnapshotRecord.best_bid,
                        )
                        .order_by(MarketSnapshotRecord.observed_at)
                    )
                ).all()
                prices = [
                    (snapshot.observed_at, price)
                    for snapshot in series
                    if (price := _mid_price(snapshot)) is not None and price > 0
                ]
                changes = [(price / reference - Decimal(1)) * Decimal(100) for _, price in prices]
                times = [_as_utc(evaluation.reference_at)]
                times.extend(_as_utc(observed_at) for observed_at, _ in prices)
                times.append(_as_utc(evaluation.target_at))
                gaps = [
                    int((right - left).total_seconds())
                    for left, right in zip(times, times[1:], strict=False)
                ]
                evaluation.measured_price = measured_price
                evaluation.measured_at = _as_utc(observation.observed_at)
                evaluation.return_percent = (measured_price / reference - Decimal(1)) * Decimal(100)
                evaluation.max_increase_percent = max([Decimal(0), *changes])
                evaluation.max_decrease_percent = min([Decimal(0), *changes])
                evaluation.observation_count = len(prices)
                evaluation.largest_gap_seconds = max(gaps, default=None)
                evaluation.status = "measured"
                evaluation.data_quality = "sampled" if len(prices) >= 2 else "single_observation"
                evaluation.price_basis = PRICE_BASIS_SNAPSHOT
                evaluation.observation_ref = f"snapshot:{observation.id}"
                evaluation.reference_observed_at = _as_utc(evaluation.reference_at)
                evaluation.missing_reason = None
                evaluation.evaluated_at = evaluation_time
                finalized += 1
        return finalized

    async def reevaluate_missing_with_trades(
        self,
        *,
        now: datetime | None = None,
        dry_run: bool = False,
        grace: timedelta = EVALUATION_OBSERVATION_GRACE,
    ) -> dict[str, int]:
        """Explicit, idempotent backfill: retry old missing evaluations with trade prices.

        Only evaluations that are still "missing_observation" and have no price basis are
        considered. Evaluations that cannot be resolved are left untouched.
        """
        evaluation_time = _as_utc(now or datetime.now(UTC))
        counts = {"candidates": 0, "measured": 0, "unresolved": 0}
        async with self.sessions.begin() as session:
            candidates = (
                await session.scalars(
                    select(SignalEvaluationRecord)
                    .where(
                        SignalEvaluationRecord.status == "missing_observation",
                        SignalEvaluationRecord.price_basis.is_(None),
                        SignalEvaluationRecord.target_at <= evaluation_time - grace,
                    )
                    .order_by(SignalEvaluationRecord.target_at)
                )
            ).all()
            counts["candidates"] = len(candidates)
            for evaluation in candidates:
                outcome = await _apply_trade_fallback(session, evaluation, evaluation_time, grace)
                counts["measured" if outcome == "measured" else "unresolved"] += 1
            if dry_run:
                await session.rollback()
        return counts

    async def evaluation_report(
        self,
        *,
        since: datetime,
        horizon_minutes: int | None = None,
        engine_version: str | None = None,
    ) -> list[dict[str, object]]:
        async with self.sessions() as session:
            query = (
                select(SignalRecord, SignalEvaluationRecord)
                .join(
                    SignalEvaluationRecord,
                    SignalEvaluationRecord.signal_id == SignalRecord.signal_id,
                )
                .where(SignalRecord.occurred_at >= since)
            )
            if horizon_minutes is not None:
                query = query.where(SignalEvaluationRecord.horizon_minutes == horizon_minutes)
            if engine_version is not None:
                query = query.where(
                    SignalRecord.observed_facts["engine_version"].as_string() == engine_version
                )
            rows = (await session.execute(query)).all()

        grouped: dict[
            tuple[str, str, str, str, int, str],
            list[tuple[SignalRecord, SignalEvaluationRecord]],
        ] = defaultdict(list)
        for signal, evaluation in rows:
            grouped[
                (
                    signal.category,
                    evaluation.pair_key,
                    signal.severity,
                    signal.confidence,
                    evaluation.horizon_minutes,
                    _price_basis_label(evaluation),
                )
            ].append((signal, evaluation))

        report: list[dict[str, object]] = []
        for (category, pair, severity, confidence, horizon, basis), entries in sorted(
            grouped.items()
        ):
            measured = [
                evaluation
                for _, evaluation in entries
                if evaluation.status == "measured" and evaluation.return_percent is not None
            ]
            returns = [
                evaluation.return_percent
                for evaluation in measured
                if evaluation.return_percent is not None
            ]
            missing = sum(evaluation.status.startswith("missing") for _, evaluation in entries)
            pending = sum(evaluation.status == "pending" for _, evaluation in entries)
            quality_counts: dict[str, int] = defaultdict(int)
            for _, evaluation in entries:
                quality_counts[evaluation.data_quality] += 1
            report.append(
                {
                    "category": category,
                    "pair": pair,
                    "severity": severity,
                    "confidence": confidence,
                    "horizon_minutes": horizon,
                    "price_basis": basis,
                    "signals": len(entries),
                    "measured": len(measured),
                    "missing": missing,
                    "pending": pending,
                    "data_quality_counts": dict(sorted(quality_counts.items())),
                    "mean_observed_change_percent": (
                        sum(returns, Decimal(0)) / Decimal(len(returns)) if returns else None
                    ),
                    "median_observed_change_percent": (
                        Decimal(str(median(returns))) if returns else None
                    ),
                    "positive_percent": (
                        Decimal(sum(value > 0 for value in returns)) * 100 / Decimal(len(returns))
                        if returns else None
                    ),
                    "negative_percent": (
                        Decimal(sum(value < 0 for value in returns)) * 100 / Decimal(len(returns))
                        if returns else None
                    ),
                    "mean_max_observed_increase_percent": _mean(
                        evaluation.max_increase_percent for evaluation in measured
                    ),
                    "mean_max_observed_decrease_percent": _mean(
                        evaluation.max_decrease_percent for evaluation in measured
                    ),
                    "mean_largest_gap_seconds": _mean(
                        Decimal(evaluation.largest_gap_seconds)
                        if evaluation.largest_gap_seconds is not None
                        else None
                        for evaluation in measured
                    ),
                }
            )
        return report


async def _apply_trade_fallback(
    session: Any,
    evaluation: SignalEvaluationRecord,
    evaluation_time: datetime,
    grace: timedelta,
) -> str:
    """Measure an evaluation from persisted trades; returns "measured" or "missing".

    The reference and measured prices both come from trades, so snapshot and trade
    price bases are never mixed inside one evaluation.
    """
    target = _as_utc(evaluation.target_at)
    reference_at = _as_utc(evaluation.reference_at)
    usable = (
        TradeRecord.pair_key == evaluation.pair_key,
        TradeRecord.quote_amount > 0,
        TradeRecord.base_amount > 0,
        TradeRecord.price > 0,
    )
    measured = await session.scalar(
        select(TradeRecord)
        .where(
            *usable,
            TradeRecord.occurred_at >= target,
            TradeRecord.occurred_at <= target + grace,
            TradeRecord.occurred_at <= evaluation_time,
        )
        .order_by(TradeRecord.occurred_at, TradeRecord.trade_id)
        .limit(1)
    )
    if measured is None:
        evaluation.missing_reason = "no_snapshot_and_no_trade_in_grace"
        return "missing"
    reference = await session.scalar(
        select(TradeRecord)
        .where(
            *usable,
            TradeRecord.occurred_at <= reference_at,
            TradeRecord.occurred_at >= reference_at - TRADE_REFERENCE_LOOKBACK,
        )
        .order_by(TradeRecord.occurred_at.desc(), TradeRecord.trade_id.desc())
        .limit(1)
    )
    if reference is None:
        evaluation.missing_reason = "no_reference_trade_in_lookback"
        return "missing"
    series = (
        await session.scalars(
            select(TradeRecord)
            .where(
                *usable,
                TradeRecord.occurred_at >= reference.occurred_at,
                TradeRecord.occurred_at <= measured.occurred_at,
            )
            .order_by(TradeRecord.occurred_at, TradeRecord.trade_id)
        )
    ).all()
    if len(series) < MIN_TRADE_OBSERVATIONS:
        evaluation.missing_reason = "insufficient_trade_observations"
        return "missing"
    changes = [(trade.price / reference.price - Decimal(1)) * Decimal(100) for trade in series]
    times = [_as_utc(trade.occurred_at) for trade in series]
    gaps = [
        int((right - left).total_seconds()) for left, right in zip(times, times[1:], strict=False)
    ]
    evaluation.reference_price = reference.price
    evaluation.reference_source = f"trade:{reference.trade_id}"
    evaluation.reference_observed_at = _as_utc(reference.occurred_at)
    evaluation.measured_price = measured.price
    evaluation.measured_at = _as_utc(measured.occurred_at)
    evaluation.return_percent = (measured.price / reference.price - Decimal(1)) * Decimal(100)
    evaluation.max_increase_percent = max([Decimal(0), *changes])
    evaluation.max_decrease_percent = min([Decimal(0), *changes])
    evaluation.observation_count = len(series)
    evaluation.largest_gap_seconds = max(gaps, default=None)
    evaluation.status = "measured"
    evaluation.data_quality = "trade_sampled"
    evaluation.price_basis = PRICE_BASIS_TRADE
    evaluation.observation_ref = f"trade:{measured.trade_id}"
    evaluation.missing_reason = None
    evaluation.evaluated_at = evaluation_time
    return "measured"


async def _save_cursor(session: Any, stream_key: str, cursor: str) -> None:
    record = await session.get(CursorRecord, stream_key)
    if record is None:
        session.add(CursorRecord(stream_key=stream_key, cursor=cursor))
    else:
        record.cursor = cursor


async def _delete_count(session: Any, model: type[Base], predicate: Any) -> int:
    count = await session.scalar(select(func.count()).select_from(model).where(predicate))
    await session.execute(delete(model).where(predicate))
    return count or 0


def _provenance_columns(provenance: TradeProvenance | None) -> dict[str, Any]:
    if provenance is None:
        return {}
    return {
        "venue": provenance.venue,
        "base_is_seller": provenance.base_is_seller,
        "base_asset": provenance.base_asset,
        "counter_asset": provenance.counter_asset,
        "operation_id": provenance.operation_id,
        "transaction_hash": provenance.transaction_hash,
        "base_account": provenance.base_account,
        "counter_account": provenance.counter_account,
        "liquidity_pool_id": provenance.liquidity_pool_id,
        "observed_at": provenance.observed_at,
    }


def _event_record(event: ChainEvent) -> EventRecord:
    return EventRecord(
        event_id=event.event_id,
        chain=event.chain.value,
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        source_ref=event.source_ref,
        attributes=_json_values(event.attributes),
    )


def _json_values(values: Any) -> JsonDocument:
    return {
        key: str(value) if isinstance(value, Decimal) else value for key, value in values.items()
    }


def _optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (ValueError, ArithmeticError):
        return None
    return result if result.is_finite() else None


def _price_basis_label(evaluation: SignalEvaluationRecord) -> str:
    """Rows are never mixed across price bases; unmeasured evaluations have basis "none"."""
    if evaluation.status != "measured":
        return "none"
    # Measured rows written before the fallback existed were snapshot-based by construction.
    return evaluation.price_basis or PRICE_BASIS_SNAPSHOT


def _mid_price(record: MarketSnapshotRecord) -> Decimal | None:
    if (
        record.best_bid is None
        or record.best_ask is None
        or record.best_bid <= 0
        or record.best_ask < record.best_bid
    ):
        return None
    return (record.best_bid + record.best_ask) / Decimal(2)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _five_minute_window(value: datetime) -> datetime:
    return value.replace(minute=(value.minute // 5) * 5, second=0, microsecond=0)


def _mean(values: Any) -> Decimal | None:
    items = [value for value in values if value is not None]
    return sum(items, Decimal(0)) / Decimal(len(items)) if items else None
