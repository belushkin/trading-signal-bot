"""Add indexes used by dashboard list and summary queries.

Revision ID: 0004_dashboard_indexes
Revises: 0003_runtime_health
"""

from alembic import op

revision = "0004_dashboard_indexes"
down_revision = "0003_runtime_health"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_events_type_time", "events", ["event_type", "occurred_at"])
    op.create_index(
        "ix_signals_time_category_severity",
        "signals",
        ["occurred_at", "category", "severity"],
    )


def downgrade() -> None:
    op.drop_index("ix_signals_time_category_severity", table_name="signals")
    op.drop_index("ix_events_type_time", table_name="events")
