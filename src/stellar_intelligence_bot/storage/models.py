"""SQLAlchemy persistence schema for observations and generated signals."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.sqlite import JSON as SQLiteJSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON

type JsonDocument = dict[str, object]


class Base(DeclarativeBase):
    pass


json_type = JSON().with_variant(JSONB(), "postgresql").with_variant(SQLiteJSON(), "sqlite")


class EventRecord(Base):
    __tablename__ = "events"

    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    chain: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)
    attributes: Mapped[JsonDocument] = mapped_column(json_type, nullable=False)


class SignalRecord(Base):
    __tablename__ = "signals"
    __table_args__ = (Index("ix_signals_dedupe_occurred", "deduplication_key", "occurred_at"),)

    signal_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    deduplication_key: Mapped[str] = mapped_column(String(255), nullable=False)
    event_id: Mapped[str] = mapped_column(
        ForeignKey("events.event_id", ondelete="CASCADE"), nullable=False, index=True
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    observed_facts: Mapped[JsonDocument] = mapped_column(json_type, nullable=False)
    interpretation: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SignalEvaluationRecord(Base):
    __tablename__ = "signal_evaluations"
    __table_args__ = (
        UniqueConstraint("signal_id", "horizon_minutes", name="uq_signal_evaluation_horizon"),
        Index("ix_evaluations_status_target", "status", "target_at"),
        Index("ix_evaluations_pair_target", "pair_key", "target_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[str] = mapped_column(
        ForeignKey("signals.signal_id", ondelete="CASCADE"), nullable=False
    )
    pair_key: Mapped[str] = mapped_column(String(255), nullable=False)
    horizon_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    target_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reference_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reference_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    reference_source: Mapped[str | None] = mapped_column(Text)
    measured_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    measured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    return_percent: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    max_increase_percent: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    max_decrease_percent: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    largest_gap_seconds: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    data_quality: Mapped[str] = mapped_column(String(32), nullable=False)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CursorRecord(Base):
    __tablename__ = "processing_cursors"

    stream_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    cursor: Mapped[str] = mapped_column(String(255), nullable=False)


class RuntimeHealthRecord(Base):
    __tablename__ = "runtime_health"

    health_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class TradeRecord(Base):
    __tablename__ = "trades"
    __table_args__ = (Index("ix_trades_pair_time", "pair_key", "occurred_at"),)

    trade_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    pair_key: Mapped[str] = mapped_column(String(255), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    base_amount: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    quote_amount: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)


class VolumeWindowRecord(Base):
    __tablename__ = "volume_windows"
    __table_args__ = (
        Index("ix_volume_pair_window", "pair_key", "window_start"),
        UniqueConstraint("pair_key", "window_start", name="uq_volume_pair_window"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pair_key: Mapped[str] = mapped_column(String(255), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    base_volume: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False)


class MarketSnapshotRecord(Base):
    __tablename__ = "market_snapshots"
    __table_args__ = (Index("ix_snapshots_pair_time", "pair_key", "observed_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pair_key: Mapped[str] = mapped_column(String(255), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    best_bid: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    best_ask: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    bid_depth: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    ask_depth: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    spread_percent: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    observed_facts: Mapped[JsonDocument] = mapped_column(json_type, nullable=False)
