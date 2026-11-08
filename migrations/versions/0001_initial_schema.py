"""Initial event, market observation, cursor, and signal tables.

Revision ID: 0001_initial_schema
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def _json_type() -> sa.types.TypeEngine[object]:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("event_id", sa.String(length=255), primary_key=True),
        sa.Column("chain", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("attributes", _json_type(), nullable=False),
    )
    op.create_index("ix_events_chain", "events", ["chain"])

    op.create_table(
        "processing_cursors",
        sa.Column("stream_key", sa.String(length=255), primary_key=True),
        sa.Column("cursor", sa.String(length=255), nullable=False),
    )

    op.create_table(
        "signals",
        sa.Column("signal_id", sa.String(length=255), primary_key=True),
        sa.Column("deduplication_key", sa.String(length=255), nullable=False),
        sa.Column(
            "event_id",
            sa.String(length=255),
            sa.ForeignKey("events.event_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.String(length=16), nullable=False),
        sa.Column("observed_facts", _json_type(), nullable=False),
        sa.Column("interpretation", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_signals_event_id", "signals", ["event_id"])
    op.create_index(
        "ix_signals_dedupe_occurred",
        "signals",
        ["deduplication_key", "occurred_at"],
    )

    op.create_table(
        "trades",
        sa.Column("trade_id", sa.String(length=255), primary_key=True),
        sa.Column("pair_key", sa.String(length=255), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("base_amount", sa.Numeric(38, 18), nullable=False),
        sa.Column("quote_amount", sa.Numeric(38, 18), nullable=False),
        sa.Column("price", sa.Numeric(38, 18), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
    )
    op.create_index("ix_trades_pair_time", "trades", ["pair_key", "occurred_at"])

    op.create_table(
        "volume_windows",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("pair_key", sa.String(length=255), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("base_volume", sa.Numeric(38, 18), nullable=False),
        sa.Column("trade_count", sa.Integer(), nullable=False),
        sa.UniqueConstraint("pair_key", "window_start", name="uq_volume_pair_window"),
    )
    op.create_index(
        "ix_volume_pair_window",
        "volume_windows",
        ["pair_key", "window_start"],
    )

    op.create_table(
        "market_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("pair_key", sa.String(length=255), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("best_bid", sa.Numeric(38, 18), nullable=True),
        sa.Column("best_ask", sa.Numeric(38, 18), nullable=True),
        sa.Column("bid_depth", sa.Numeric(38, 18), nullable=False),
        sa.Column("ask_depth", sa.Numeric(38, 18), nullable=False),
        sa.Column("spread_percent", sa.Numeric(18, 8), nullable=True),
        sa.Column("observed_facts", _json_type(), nullable=False),
    )
    op.create_index(
        "ix_snapshots_pair_time",
        "market_snapshots",
        ["pair_key", "observed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_snapshots_pair_time", table_name="market_snapshots")
    op.drop_table("market_snapshots")
    op.drop_index("ix_volume_pair_window", table_name="volume_windows")
    op.drop_table("volume_windows")
    op.drop_index("ix_trades_pair_time", table_name="trades")
    op.drop_table("trades")
    op.drop_index("ix_signals_dedupe_occurred", table_name="signals")
    op.drop_index("ix_signals_event_id", table_name="signals")
    op.drop_table("signals")
    op.drop_table("processing_cursors")
    op.drop_index("ix_events_chain", table_name="events")
    op.drop_table("events")
