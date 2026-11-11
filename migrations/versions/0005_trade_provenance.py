"""Add trade provenance, evaluation price basis and collector interruption health.

All new columns are nullable (or defaulted) so existing rows stay valid and unchanged.

Revision ID: 0005_trade_provenance
Revises: 0004_dashboard_indexes
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_trade_provenance"
down_revision = "0004_dashboard_indexes"
branch_labels = None
depends_on = None

TRADE_COLUMNS = (
    ("venue", sa.String(length=16)),
    ("base_is_seller", sa.Boolean()),
    ("base_asset", sa.String(length=255)),
    ("counter_asset", sa.String(length=255)),
    ("operation_id", sa.String(length=64)),
    ("transaction_hash", sa.String(length=64)),
    ("base_account", sa.String(length=64)),
    ("counter_account", sa.String(length=64)),
    ("liquidity_pool_id", sa.String(length=64)),
    ("observed_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    with op.batch_alter_table("trades") as batch:
        for name, kind in TRADE_COLUMNS:
            batch.add_column(sa.Column(name, kind, nullable=True))
    with op.batch_alter_table("signal_evaluations") as batch:
        batch.add_column(sa.Column("price_basis", sa.String(length=24), nullable=True))
        batch.add_column(sa.Column("observation_ref", sa.Text(), nullable=True))
        batch.add_column(sa.Column("reference_observed_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("missing_reason", sa.String(length=64), nullable=True))
    with op.batch_alter_table("runtime_health") as batch:
        batch.add_column(
            sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("interruption_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("last_interruption_at", sa.DateTime(timezone=True)))
        batch.add_column(
            sa.Column("skipped_trade_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("last_skipped_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("last_skipped_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("runtime_health") as batch:
        for name in (
            "last_skipped_reason",
            "last_skipped_at",
            "skipped_trade_count",
            "last_interruption_at",
            "interruption_count",
            "consecutive_failures",
        ):
            batch.drop_column(name)
    with op.batch_alter_table("signal_evaluations") as batch:
        for name in ("missing_reason", "reference_observed_at", "observation_ref", "price_basis"):
            batch.drop_column(name)
    with op.batch_alter_table("trades") as batch:
        for name, _ in reversed(TRADE_COLUMNS):
            batch.drop_column(name)
