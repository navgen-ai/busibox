import uuid
from typing import Dict, List, Optional

from pydantic_ai import Agent
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.core import BusiboxDeps, ingest_tool, rag_tool, search_tool
from app.models.domain import AgentDefinition
from app.schemas.definitions import AgentDefinitionCreate

# Registry of permitted tool adapters
TOOL_REGISTRY = {
    "search": search_tool,
    "ingest": ingest_tool,
    "rag": rag_tool,
}


async def load_active_agents(session: AsyncSession) -> Dict[uuid.UUID, Agent[BusiboxDeps, object]]:
    """
    Hydrate active agent definitions from the database and register allowed tools.
    """
    stmt = select(AgentDefinition).where(AgentDefinition.is_active.is_(True))
    result = await session.execute(stmt)
    agents: Dict[uuid.UUID, Agent[BusiboxDeps, object]] = {}
    for definition in result.scalars().all():
        agent = Agent[BusiboxDeps, object](
            model=definition.model,
            instructions=definition.instructions,
        )
        for tool_name in definition.tools.get("names", []):
            tool_fn = TOOL_REGISTRY.get(tool_name)
            if tool_fn:
                agent.tool(tool_fn)  # type: ignore[arg-type]
        agents[definition.id] = agent
    return agents


async def register_agent(
    session: AsyncSession, payload: AgentDefinitionCreate
) -> tuple[uuid.UUID, Agent[BusiboxDeps, object]]:
    """
    Persist a new agent definition and return a hydrated Agent instance.
    """
    definition = AgentDefinition(
        name=payload.name,
        display_name=payload.display_name,
        description=payload.description,
        model=payload.model,
        instructions=payload.instructions,
        tools=payload.tools,
        workflow=payload.workflow,
        scopes=payload.scopes,
        is_active=payload.is_active,
    )
    session.add(definition)
    await session.commit()
    await session.refresh(definition)
    agent = Agent[BusiboxDeps, object](model=definition.model, instructions=definition.instructions)
    for tool_name in payload.tools.get("names", []):
        tool_fn = TOOL_REGISTRY.get(tool_name)
        if tool_fn:
            agent.tool(tool_fn)  # type: ignore[arg-type]
    return definition.id, agent
