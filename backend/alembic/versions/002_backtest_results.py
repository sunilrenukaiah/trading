"""002 backtest results

Revision ID: 002
Revises: 001
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("eval_days", sa.Integer(), nullable=False),
        sa.Column("lookback_days", sa.Integer(), nullable=False),
        sa.Column("stock_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "backtest_pattern_scores",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("pattern_id", sa.String(length=64), nullable=False),
        sa.Column("pattern_name", sa.String(length=128), nullable=False),
        sa.Column("total_correct", sa.Integer(), nullable=False),
        sa.Column("total_signals", sa.Integer(), nullable=False),
        sa.Column("avg_daily_score", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("overall_hit_rate", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["backtest_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "backtest_stock_scores",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("pattern_id", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("correct", sa.Integer(), nullable=False),
        sa.Column("signals", sa.Integer(), nullable=False),
        sa.Column("hit_rate", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["backtest_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("backtest_stock_scores")
    op.drop_table("backtest_pattern_scores")
    op.drop_table("backtest_runs")
