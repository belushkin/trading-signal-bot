"""Add independent actionable signal settings and observations."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_actionable_signals"
down_revision = "0006_episode_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    document = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table(
        "assistant_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settings", document, nullable=False),
    )
    op.create_table(
        "assistant_heartbeat",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "assistant_opportunities",
        sa.Column("signal_id", sa.String(255), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pair_key", sa.String(255), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("parameters", document, nullable=False),
    )
    op.create_index(
        "ix_assistant_opportunities_created_at", "assistant_opportunities", ["created_at"]
    )
    op.create_table(
        "assistant_opportunity_evaluations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "signal_id",
            sa.String(255),
            sa.ForeignKey("assistant_opportunities.signal_id"),
            nullable=False,
        ),
        sa.Column("horizon_minutes", sa.Integer(), nullable=False),
        sa.Column("target_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("result", document),
        sa.UniqueConstraint("signal_id", "horizon_minutes", name="uq_opportunity_horizon"),
    )
    op.create_index(
        "ix_opportunity_evaluation_due",
        "assistant_opportunity_evaluations",
        ["status", "target_at"],
    )


def downgrade() -> None:
    op.drop_table("assistant_opportunity_evaluations")
    op.drop_table("assistant_opportunities")
    op.drop_table("assistant_heartbeat")
    op.drop_table("assistant_settings")
