"""Persist collector heartbeat and latest error.

Revision ID: 0003_runtime_health
Revises: 0002_signal_evaluations
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_runtime_health"
down_revision = "0002_signal_evaluations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runtime_health",
        sa.Column("health_key", sa.String(length=64), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("runtime_health")
