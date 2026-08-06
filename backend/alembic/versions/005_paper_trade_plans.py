"""005 paper trade plans for bracket orders

Revision ID: 005
Revises: 004
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STATUS_VALUES = ("PENDING_ENTRY", "OPEN", "TARGET_HIT", "STOP_HIT", "CANCELLED")


def upgrade() -> None:
    status_enum = postgresql.ENUM(*_STATUS_VALUES, name="trade_plan_status")
    status_enum.create(op.get_bind(), checkfirst=True)

    status_col = postgresql.ENUM(
        *_STATUS_VALUES,
        name="trade_plan_status",
        create_type=False,
    )

    op.create_table(
        "paper_trade_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("paper_accounts.id"), nullable=False),
        sa.Column("instrument_id", sa.Integer(), sa.ForeignKey("instruments.id"), nullable=False),
        sa.Column("recommendation_date", sa.Date(), nullable=False),
        sa.Column("shares", sa.Integer(), nullable=False),
        sa.Column("entry_limit_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("target_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("stop_loss_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("status", status_col, nullable=False, server_default="PENDING_ENTRY"),
        sa.Column("entry_order_id", sa.Integer(), sa.ForeignKey("paper_orders.id"), nullable=True),
        sa.Column("exit_order_id", sa.Integer(), sa.ForeignKey("paper_orders.id"), nullable=True),
        sa.Column("entry_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("exit_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("realized_pnl", sa.Numeric(18, 2), nullable=True),
        sa.Column("pattern_name", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "account_id",
            "instrument_id",
            "recommendation_date",
            name="uq_trade_plan_day_symbol",
        ),
    )
    op.create_index(
        "ix_trade_plans_status_rec_date",
        "paper_trade_plans",
        ["status", "recommendation_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_trade_plans_status_rec_date", table_name="paper_trade_plans")
    op.drop_table("paper_trade_plans")
    postgresql.ENUM(*_STATUS_VALUES, name="trade_plan_status").drop(op.get_bind(), checkfirst=True)
