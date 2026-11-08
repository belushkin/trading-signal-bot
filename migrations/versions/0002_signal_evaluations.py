"""Persist forward price observations for eligible market signals.

Revision ID: 0002_signal_evaluations
Revises: 0001_initial_schema
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_signal_evaluations"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "signal_evaluations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "signal_id",
            sa.String(length=255),
            sa.ForeignKey("signals.signal_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pair_key", sa.String(length=255), nullable=False),
        sa.Column("horizon_minutes", sa.Integer(), nullable=False),
        sa.Column("target_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reference_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reference_price", sa.Numeric(38, 18), nullable=True),
        sa.Column("reference_source", sa.Text(), nullable=True),
        sa.Column("measured_price", sa.Numeric(38, 18), nullable=True),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("return_percent", sa.Numeric(18, 8), nullable=True),
        sa.Column("max_increase_percent", sa.Numeric(18, 8), nullable=True),
        sa.Column("max_decrease_percent", sa.Numeric(18, 8), nullable=True),
        sa.Column("observation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("largest_gap_seconds", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("data_quality", sa.String(length=32), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "signal_id",
            "horizon_minutes",
            name="uq_signal_evaluation_horizon",
        ),
    )
    op.create_index(
        "ix_evaluations_status_target",
        "signal_evaluations",
        ["status", "target_at"],
    )
    op.create_index(
        "ix_evaluations_pair_target",
        "signal_evaluations",
        ["pair_key", "target_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_evaluations_pair_target", table_name="signal_evaluations")
    op.drop_index("ix_evaluations_status_target", table_name="signal_evaluations")
    op.drop_table("signal_evaluations")
