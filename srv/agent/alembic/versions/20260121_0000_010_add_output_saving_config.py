"""Add output_saving_config column to agent_tasks

Revision ID: 010
Revises: 009
Create Date: 2026-01-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '010'
down_revision: Union[str, None] = '009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add output_saving_config column to agent_tasks table."""
    op.add_column(
        'agent_tasks',
        sa.Column(
            'output_saving_config',
            postgresql.JSON(astext_type=sa.Text()),
            nullable=True,
            comment='Output saving settings: {enabled: false, tags: ["tag1"], library_type: "TASKS"}'
        )
    )


def downgrade() -> None:
    """Remove output_saving_config column from agent_tasks table."""
    op.drop_column('agent_tasks', 'output_saving_config')
