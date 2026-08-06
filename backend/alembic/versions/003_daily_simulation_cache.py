"""003 daily simulation cache columns

Revision ID: 003
Revises: 002
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "backtest_runs",
        sa.Column("universe", sa.String(length=32), server_default="NIFTY250", nullable=False),
    )
    op.add_column(
        "backtest_runs",
        sa.Column("simulation_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "backtest_runs",
        sa.Column("report_payload", JSONB(), nullable=True),
    )
    op.execute(
        "UPDATE backtest_runs SET simulation_date = (run_at AT TIME ZONE 'UTC')::date "
        "WHERE simulation_date IS NULL"
    )
    op.alter_column("backtest_runs", "simulation_date", nullable=False)
    op.create_index(
        "ix_backtest_runs_simulation_date_universe",
        "backtest_runs",
        ["simulation_date", "universe"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_backtest_runs_simulation_date_universe", table_name="backtest_runs")
    op.drop_column("backtest_runs", "report_payload")
    op.drop_column("backtest_runs", "simulation_date")
    op.drop_column("backtest_runs", "universe")
