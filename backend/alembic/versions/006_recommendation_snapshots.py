"""006 recommendation daily snapshot cache

Revision ID: 006
Revises: 005
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "recommendation_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("analysis_date", sa.Date(), nullable=False),
        sa.Column("prediction_date", sa.Date(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("budget_inr", sa.Numeric(18, 2), nullable=False),
        sa.Column("max_target_profit_pct", sa.Numeric(8, 2), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.UniqueConstraint("analysis_date", name="uq_recommendation_snapshot_analysis_date"),
    )
    op.create_index(
        "ix_recommendation_snapshots_prediction_date",
        "recommendation_snapshots",
        ["prediction_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_recommendation_snapshots_prediction_date", table_name="recommendation_snapshots")
    op.drop_table("recommendation_snapshots")
