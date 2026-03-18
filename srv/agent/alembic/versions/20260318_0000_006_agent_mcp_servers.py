"""Add mcp_servers to agent_definitions

Revision ID: agent_mcp_servers_006
Revises: eval_tables_005
Create Date: 2026-03-18 00:00:00

Adds mcp_servers JSON column to agent_definitions table.
Stores external MCP server configurations that the agent can
connect to for additional tool capabilities.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'agent_mcp_servers_006'
down_revision: Union[str, None] = 'eval_tables_005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add mcp_servers column to agent_definitions."""
    op.add_column(
        'agent_definitions',
        sa.Column(
            'mcp_servers',
            sa.JSON(),
            nullable=True,
            comment='External MCP server configs [{name, transport, url/command, ...}]',
        ),
    )


def downgrade() -> None:
    """Remove mcp_servers column from agent_definitions."""
    op.drop_column('agent_definitions', 'mcp_servers')
