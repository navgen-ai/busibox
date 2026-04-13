"""
Centralized agent visibility logic.

Provides helpers to determine which agents a user can see and access,
based on the visibility column on agent_definitions.
"""
from sqlalchemy import or_
from sqlalchemy.sql import ColumnElement

from app.models.domain import (
    AGENT_VISIBILITY_APPLICATION,
    AGENT_VISIBILITY_BUILTIN,
    AgentDefinition,
)
from app.schemas.auth import Principal


def can_access_agent(principal: Principal, agent: AgentDefinition) -> bool:
    """
    Check whether *principal* is allowed to see / execute *agent*.

    Rules:
      builtin      → everyone
      application   → everyone
      shared        → owner  (future: + users with matching role in JWT)
      personal      → owner only
    """
    if agent.visibility in (AGENT_VISIBILITY_BUILTIN, AGENT_VISIBILITY_APPLICATION):
        return True
    if agent.created_by == principal.sub:
        return True
    return False


def visibility_filter(principal: Principal) -> ColumnElement:
    """
    Return a SQLAlchemy filter clause for listing agents visible to *principal*.

    Combines:
      - application / builtin agents (visible to all)
      - agents owned by the user (personal + shared)
    """
    return or_(
        AgentDefinition.visibility.in_([
            AGENT_VISIBILITY_APPLICATION,
            AGENT_VISIBILITY_BUILTIN,
        ]),
        AgentDefinition.created_by == principal.sub,
    )
