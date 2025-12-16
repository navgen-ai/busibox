"""fix dispatcher log timezone

Revision ID: 20251216_0000_004
Revises: 20251212_0000_003
Create Date: 2025-12-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20251216_0000_004'
down_revision: Union[str, None] = '20251212_0000_003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Change dispatcher_decision_log.timestamp from TIMESTAMP to TIMESTAMP WITH TIME ZONE.
    This fixes timezone-aware datetime compatibility issues.
    """
    # PostgreSQL allows this ALTER without data loss - it converts existing timestamps
    # from local time to UTC (or keeps them as-is if already UTC)
    op.execute("""
        ALTER TABLE dispatcher_decision_log 
        ALTER COLUMN timestamp TYPE TIMESTAMP WITH TIME ZONE
    """)


def downgrade() -> None:
    """
    Revert timestamp column to TIMESTAMP WITHOUT TIME ZONE.
    Note: This will lose timezone information.
    """
    op.execute("""
        ALTER TABLE dispatcher_decision_log 
        ALTER COLUMN timestamp TYPE TIMESTAMP WITHOUT TIME ZONE
    """)
