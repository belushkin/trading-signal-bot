"""Store whether entry and target prices were seen as queryable columns."""

import json

import sqlalchemy as sa
from alembic import op

revision = "0008_evaluation_target_columns"
down_revision = "0007_actionable_signals"
branch_labels = None
depends_on = None

TABLE = "assistant_opportunity_evaluations"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("entry_price_observed", sa.Boolean(), nullable=True))
    op.add_column(TABLE, sa.Column("target_price_observed", sa.Boolean(), nullable=True))
    bind = op.get_bind()
    table = sa.table(
        TABLE,
        sa.column("id", sa.Integer()),
        sa.column("result", sa.JSON()),
        sa.column("entry_price_observed", sa.Boolean()),
        sa.column("target_price_observed", sa.Boolean()),
    )
    for row_id, result in bind.execute(sa.select(table.c.id, table.c.result)).all():
        if result is None:
            continue
        document = json.loads(result) if isinstance(result, str) else result
        bind.execute(
            table.update()
            .where(table.c.id == row_id)
            .values(
                entry_price_observed=document.get("entry_price_observed"),
                target_price_observed=document.get("target_price_observed_after_entry"),
            )
        )


def downgrade() -> None:
    op.drop_column(TABLE, "target_price_observed")
    op.drop_column(TABLE, "entry_price_observed")
