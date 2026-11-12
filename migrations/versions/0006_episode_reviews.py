"""Add operator review state for grouped market episodes.

Creates one new table. No existing table or row is modified.

Revision ID: 0006_episode_reviews
Revises: 0005_trade_provenance
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_episode_reviews"
down_revision = "0005_trade_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "episode_reviews",
        sa.Column("episode_key", sa.String(length=255), primary_key=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signal_count", sa.Integer(), nullable=False),
        sa.Column("max_severity", sa.String(length=16), nullable=False),
        sa.Column("last_signal_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("episode_reviews")
