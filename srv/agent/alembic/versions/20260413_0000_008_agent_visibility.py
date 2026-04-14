"""Add visibility and app_id to agent_definitions

Revision ID: agent_visibility_008
Revises: chat_settings_insights_007
Create Date: 2026-04-13 00:00:00

Replaces the binary is_builtin flag with a proper visibility enum:
  - 'builtin'     : code-defined agents (Python files)
  - 'application' : app-managed agents synced via POST /agents/definitions
  - 'shared'      : shared with specific roles (future)
  - 'personal'    : only visible to the creator

Also adds app_id to track which application owns an agent.
Existing is_builtin=true rows are migrated to visibility='application'.
The is_builtin column is kept for backward compatibility.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'agent_visibility_008'
down_revision: Union[str, None] = 'chat_settings_insights_007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Add visibility column
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'agent_definitions' AND column_name = 'visibility'"
    ))
    if result.fetchone() is None:
        op.add_column(
            'agent_definitions',
            sa.Column('visibility', sa.String(20), nullable=False, server_default='personal'),
        )

    # Add app_id column
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'agent_definitions' AND column_name = 'app_id'"
    ))
    if result.fetchone() is None:
        op.add_column(
            'agent_definitions',
            sa.Column('app_id', sa.String(120), nullable=True),
        )

    # Migrate: is_builtin=true → visibility='application'
    op.execute(sa.text(
        "UPDATE agent_definitions SET visibility = 'application' WHERE is_builtin = true"
    ))

    # Sync is_builtin to match visibility for consistency
    op.execute(sa.text(
        "UPDATE agent_definitions SET is_builtin = true "
        "WHERE visibility IN ('builtin', 'application')"
    ))

    # New index for visibility-based queries
    result = conn.execute(sa.text(
        "SELECT 1 FROM pg_indexes "
        "WHERE indexname = 'idx_agent_defs_visibility_appid'"
    ))
    if result.fetchone() is None:
        op.create_index(
            'idx_agent_defs_visibility_appid',
            'agent_definitions',
            ['visibility', 'app_id'],
            postgresql_where=sa.text('is_active = true'),
        )


def downgrade() -> None:
    op.drop_index('idx_agent_defs_visibility_appid', table_name='agent_definitions')
    op.drop_column('agent_definitions', 'app_id')
    op.drop_column('agent_definitions', 'visibility')
