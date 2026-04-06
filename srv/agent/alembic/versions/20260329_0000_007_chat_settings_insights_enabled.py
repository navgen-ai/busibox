"""Add insights_enabled to chat_settings

Revision ID: chat_settings_insights_007
Revises: agent_mcp_servers_006
Create Date: 2026-03-29 00:00:00

Adds insights_enabled boolean column to chat_settings table.
Defaults to true so existing users retain the current behavior.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'chat_settings_insights_007'
down_revision: Union[str, None] = 'agent_mcp_servers_006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add insights_enabled column to chat_settings."""
    op.add_column(
        'chat_settings',
        sa.Column(
            'insights_enabled',
            sa.Boolean(),
            nullable=False,
            server_default='true',
            comment='When false, skip AI insight generation and injection',
        ),
    )


def downgrade() -> None:
    """Remove insights_enabled column from chat_settings."""
    op.drop_column('chat_settings', 'insights_enabled')
