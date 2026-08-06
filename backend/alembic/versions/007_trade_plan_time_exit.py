"""007 add TIME_EXIT to trade_plan_status enum

Revision ID: 007
Revises: 006
"""

from typing import Sequence, Union

from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostgreSQL cannot ALTER TYPE ... ADD VALUE inside a transaction.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE trade_plan_status ADD VALUE IF NOT EXISTS 'TIME_EXIT'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values safely; no-op.
    pass
