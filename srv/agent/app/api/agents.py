import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_principal
from app.core.logging import get_logger
from app.db.session import get_session
from app.models.domain import AgentDefinition, EvalDefinition, ToolDefinition, WorkflowDefinition
from app.schemas.auth import Principal
from app.schemas.definitions import (
    AgentDefinitionCreate,
    AgentDefinitionRead,
    AgentDefinitionUpdate,
    EvalDefinitionCreate,
    EvalDefinitionRead,
    ToolDefinitionCreate,
    ToolDefinitionRead,
    WorkflowDefinitionCreate,
    WorkflowDefinitionRead,
)
from app.models.domain import AGENT_VISIBILITY_APPLICATION, AGENT_VISIBILITY_BUILTIN, AGENT_VISIBILITY_PERSONAL
from app.services.agent_registry import agent_registry
from app.services.agent_visibility import can_access_agent, visibility_filter
from app.services.builtin_agents import BUILTIN_AGENT_METADATA

logger = get_logger(__name__)

router = APIRouter(prefix="/agents", tags=["agents"])
BUILTIN_AGENT_RESERVED_NAMES = {meta["name"] for meta in BUILTIN_AGENT_METADATA.values()}


class WeatherRequest(BaseModel):
    """Request to get weather via weather agent."""
    query: str


class WeatherResponse(BaseModel):
    """Response from weather agent."""
    response: str


@router.get("", response_model=List[AgentDefinitionRead])
async def list_agents(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> List[AgentDefinitionRead]:
    """
    List agents visible to the current user.
    
    Returns:
    - All code-defined built-in agents (from app/agents/)
    - All application agents (visibility='application')
    - Personal agents created by the authenticated user
    
    Other users' personal agents are not visible.
    """
    from app.services.builtin_agents import get_builtin_agent_definitions
    
    builtin_agents = get_builtin_agent_definitions()
    
    stmt = select(AgentDefinition).where(
        AgentDefinition.is_active.is_(True),
        visibility_filter(principal),
    )
    result = await session.execute(stmt)
    db_agents = [AgentDefinitionRead.model_validate(a) for a in result.scalars().all()]
    
    agents_dict = {agent.name: agent for agent in db_agents}
    for builtin in builtin_agents:
        agents_dict[builtin.name] = builtin
    
    agents = list(agents_dict.values())
    
    logger.info(
        "agents_listed",
        user_id=principal.sub,
        total_count=len(agents),
        builtin_count=sum(1 for a in agents if a.is_builtin),
        personal_count=sum(1 for a in agents if not a.is_builtin),
        code_builtin_count=len(builtin_agents),
    )
    
    return agents


@router.get("/models")
async def list_available_models(
    # principal: Principal = Depends(get_principal),  # Temporarily disabled for testing
) -> dict:
    """
    List available models from LiteLLM.
    Models are configured in model_registry.yml with purposes like 'chat', 'research', 'agent'.
    """
    import httpx
    from app.config.settings import get_settings
    
    settings = get_settings()
    
    try:
        headers = {}
        if settings.litellm_api_key:
            headers["Authorization"] = f"Bearer {settings.litellm_api_key}"
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{settings.litellm_base_url}/models",
                headers=headers
            )
            response.raise_for_status()
            return response.json()
    except Exception as e:
        return {
            "error": str(e),
            "note": "Models are configured in model_registry.yml",
            "common_purposes": ["chat", "research", "agent", "tool_calling", "frontier"]
        }


@router.get("/workflows", response_model=List[WorkflowDefinitionRead])
async def list_workflows(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> List[WorkflowDefinitionRead]:
    """
    List all workflows including built-in and database workflows.
    
    Returns:
    - All built-in workflows (defined in code)
    - All custom workflows from database
    
    Built-in workflows are marked with is_builtin=True.
    """
    from app.services.builtin_workflows import get_builtin_workflow_definitions
    
    # Get built-in workflows
    builtin_workflows = get_builtin_workflow_definitions()
    builtin_names = {w.name for w in builtin_workflows}
    
    # Get database workflows (excluding any with same name as built-in)
    stmt = select(WorkflowDefinition).where(WorkflowDefinition.is_active.is_(True))
    result = await session.execute(stmt)
    db_workflows = [
        WorkflowDefinitionRead.model_validate(w) 
        for w in result.scalars().all()
        if w.name not in builtin_names  # Avoid duplicates
    ]
    
    # Mark database workflows as not built-in
    for w in db_workflows:
        w.is_builtin = False
    
    # Combine: built-in first, then database workflows
    return builtin_workflows + db_workflows


@router.post("/definitions", response_model=AgentDefinitionRead, status_code=status.HTTP_201_CREATED)
async def create_agent_definition(
    payload: AgentDefinitionCreate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> AgentDefinitionRead:
    """
    Create or update (upsert) an agent definition.

    Visibility categories:
      - 'application': visible to all users, owned by an app (requires app_id)
      - 'shared': visible to owner + users with matching role (future)
      - 'personal': visible only to the creator
      - 'builtin': reserved for code-defined agents

    Backward compat: ``is_builtin=True`` without an explicit ``visibility``
    is mapped to ``visibility='application'``.
    """
    from sqlalchemy import select as sa_select

    if payload.name in BUILTIN_AGENT_RESERVED_NAMES:
        raise HTTPException(
            status_code=409,
            detail=f"'{payload.name}' is a reserved built-in agent name and cannot be created or modified via API",
        )

    effective_visibility = payload.resolved_visibility()
    effective_app_id = payload.app_id

    if effective_visibility == AGENT_VISIBILITY_BUILTIN:
        raise HTTPException(
            status_code=400,
            detail="visibility='builtin' is reserved for code-defined agents",
        )

    # Keep is_builtin in sync with visibility for backward compat
    effective_is_builtin = effective_visibility in (AGENT_VISIBILITY_APPLICATION, AGENT_VISIBILITY_BUILTIN)

    # Check for existing agent with same name (upsert semantics)
    existing_query = sa_select(AgentDefinition).where(
        AgentDefinition.name == payload.name,
        AgentDefinition.is_active == True,  # noqa: E712
    )
    result = await session.execute(existing_query)
    existing = result.scalar_one_or_none()

    if existing:
        is_owner = existing.created_by == principal.sub
        has_app_access = (
            effective_visibility == AGENT_VISIBILITY_APPLICATION
            and existing.visibility == AGENT_VISIBILITY_APPLICATION
            and effective_app_id
            and existing.app_id == effective_app_id
        )

        if not is_owner and not has_app_access:
            raise HTTPException(
                status_code=409,
                detail=f"An agent named '{payload.name}' already exists (owned by another user)",
            )

        update_fields = payload.model_dump(exclude_unset=True)
        # Remove fields that are computed or should not be blindly set
        update_fields.pop("is_builtin", None)
        update_fields.pop("context_compression", None)

        # Set authoritative visibility + derived is_builtin
        update_fields["visibility"] = effective_visibility
        update_fields["is_builtin"] = effective_is_builtin
        if effective_app_id:
            update_fields["app_id"] = effective_app_id

        for key, value in update_fields.items():
            if hasattr(existing, key):
                setattr(existing, key, value)
        await session.commit()
        await session.refresh(existing)

        await agent_registry.refresh(session)

        logger.info(
            "agent_updated_via_create",
            agent_id=str(existing.id),
            agent_name=existing.name,
            user_id=principal.sub,
            visibility=existing.visibility,
            app_id=existing.app_id,
        )
        return AgentDefinitionRead.model_validate(existing)

    try:
        agent_id = await agent_registry.add(
            session,
            payload,
            created_by=principal.sub,
            is_builtin=effective_is_builtin,
            visibility=effective_visibility,
            app_id=effective_app_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    definition = await session.get(AgentDefinition, agent_id)
    if not definition:
        raise HTTPException(status_code=500, detail="failed to fetch saved definition")
    
    logger.info(
        "agent_created",
        agent_id=str(agent_id),
        agent_name=definition.name,
        user_id=principal.sub,
        visibility=definition.visibility,
        app_id=definition.app_id,
        model=definition.model,
    )
    
    return AgentDefinitionRead.model_validate(definition)


@router.put("/definitions/{agent_id}", response_model=AgentDefinitionRead)
async def update_agent_definition(
    agent_id: uuid.UUID,
    payload: AgentDefinitionUpdate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> AgentDefinitionRead:
    """
    Update an agent definition.
    
    Code-defined built-in agents cannot be updated.
    Application and personal agents: only the owner (or same app_id) can update.
    """
    from app.agents.dynamic_loader import validate_tool_references

    definition = await session.get(AgentDefinition, agent_id)
    if not definition or not definition.is_active:
        raise HTTPException(status_code=404, detail="Agent not found")

    update_data = payload.model_dump(exclude_unset=True)

    if definition.visibility == AGENT_VISIBILITY_BUILTIN:
        raise HTTPException(
            status_code=403,
            detail="Built-in agents are code-defined and cannot be updated via API",
        )

    if not can_access_agent(principal, definition):
        raise HTTPException(status_code=404, detail="Agent not found")

    if "name" in update_data and update_data["name"] in BUILTIN_AGENT_RESERVED_NAMES:
        raise HTTPException(
            status_code=409,
            detail=f"'{update_data['name']}' is a reserved built-in agent name and cannot be used",
        )
    if "tools" in update_data and update_data["tools"] is not None:
        validate_tool_references(update_data["tools"].get("names", []))

    # Keep is_builtin in sync if visibility changes
    if "visibility" in update_data and update_data["visibility"]:
        vis = update_data["visibility"].value if hasattr(update_data["visibility"], "value") else update_data["visibility"]
        update_data["is_builtin"] = vis in (AGENT_VISIBILITY_APPLICATION, AGENT_VISIBILITY_BUILTIN)
        update_data["visibility"] = vis

    for key, value in update_data.items():
        if hasattr(definition, key):
            setattr(definition, key, value)

    definition.version += 1
    await session.commit()
    await session.refresh(definition)

    await agent_registry.refresh(session)

    logger.info(
        "agent_updated",
        agent_id=str(agent_id),
        agent_name=definition.name,
        user_id=principal.sub,
        visibility=definition.visibility,
    )
    return AgentDefinitionRead.model_validate(definition)


@router.delete("/definitions/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent_definition(
    agent_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Soft-delete an agent (is_active=False). Code-defined and application agents cannot be deleted."""
    definition = await session.get(AgentDefinition, agent_id)
    if not definition:
        raise HTTPException(status_code=404, detail="Agent not found")
    if definition.visibility in (AGENT_VISIBILITY_BUILTIN, AGENT_VISIBILITY_APPLICATION):
        raise HTTPException(status_code=403, detail="Cannot delete built-in or application agents")
    if definition.created_by != principal.sub:
        raise HTTPException(status_code=404, detail="Agent not found")
    definition.is_active = False
    await session.commit()
    await agent_registry.refresh(session)
    logger.info(
        "agent_deleted",
        agent_id=str(agent_id),
        agent_name=definition.name,
        user_id=principal.sub,
    )


@router.get("/tools", response_model=List[ToolDefinitionRead])
async def list_tools(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> List[ToolDefinitionRead]:
    """
    List tools with built-in tools included.
    
    Returns:
    - All built-in tools (dynamically loaded from app/tools/)
    - All custom tools from database
    
    Built-in tools take precedence over database entries with the same name.
    """
    from app.services.builtin_tools import get_builtin_tool_definitions
    
    # Get built-in tools from code
    builtin_tools = get_builtin_tool_definitions()
    
    # Get custom tools from database
    stmt = select(ToolDefinition).where(ToolDefinition.is_active.is_(True))
    result = await session.execute(stmt)
    db_tools = [ToolDefinitionRead.model_validate(t) for t in result.scalars().all()]
    
    # Combine: built-in tools take precedence over database entries with same name
    tools_dict = {tool.name: tool for tool in db_tools}
    for builtin in builtin_tools:
        tools_dict[builtin.name] = builtin
    
    tools = list(tools_dict.values())
    
    logger.info(
        "tools_listed",
        user_id=principal.sub,
        total_count=len(tools),
        builtin_count=sum(1 for t in tools if t.is_builtin),
        custom_count=sum(1 for t in tools if not t.is_builtin),
    )
    
    return tools


@router.post("/tools", response_model=ToolDefinitionRead, status_code=status.HTTP_201_CREATED)
async def create_tool(
    payload: ToolDefinitionCreate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> ToolDefinitionRead:
    definition = ToolDefinition(**payload.model_dump(by_alias=True))
    session.add(definition)
    await session.commit()
    await session.refresh(definition)
    return ToolDefinitionRead.model_validate(definition)


@router.post("/workflows", response_model=WorkflowDefinitionRead, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    payload: WorkflowDefinitionCreate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> WorkflowDefinitionRead:
    """
    Create a new workflow definition with step validation.
    
    Workflows are created with created_by set to authenticated user.
    
    Raises:
        HTTPException: 400 if workflow validation fails
    """
    try:
        # Validate workflow steps
        from app.workflows.engine import validate_workflow_steps
        validate_workflow_steps(payload.steps)
        
        record = WorkflowDefinition(
            **payload.model_dump(),
            created_by=principal.sub
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)
        return WorkflowDefinitionRead.model_validate(record)
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid workflow definition: {str(e)}"
        )


@router.get("/evals", response_model=List[EvalDefinitionRead])
async def list_evals(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> List[EvalDefinitionRead]:
    stmt = select(EvalDefinition).where(EvalDefinition.is_active.is_(True))
    result = await session.execute(stmt)
    return [EvalDefinitionRead.model_validate(e) for e in result.scalars().all()]


@router.post("/evals", response_model=EvalDefinitionRead, status_code=status.HTTP_201_CREATED)
async def create_eval(
    payload: EvalDefinitionCreate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> EvalDefinitionRead:
    """
    Create a new evaluator definition.
    
    Evaluators are created with created_by set to authenticated user.
    """
    record = EvalDefinition(
        **payload.model_dump(),
        created_by=principal.sub
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return EvalDefinitionRead.model_validate(record)


@router.post("/weather/query", response_model=WeatherResponse)
async def query_weather_agent(
    payload: WeatherRequest,
    # principal: Principal = Depends(get_principal),  # Temporarily disabled for testing
) -> WeatherResponse:
    """
    Query the weather agent directly (for testing).
    This endpoint demonstrates LiteLLM integration and external API tool calling.
    """
    from app.agents.weather_agent import weather_agent
    
    result = await weather_agent.run(payload.query)
    # Pydantic AI result has .output attribute, not .data
    response_text = str(result.output) if hasattr(result, 'output') else str(result)
    return WeatherResponse(response=response_text)


# NOTE: This route MUST be at the end to avoid matching before static routes like /tools, /workflows, etc.
@router.get("/{agent_id}", response_model=AgentDefinitionRead)
async def get_agent(
    agent_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> AgentDefinitionRead:
    """
    Get individual agent by ID with ownership check.
    
    Returns:
    - Agent if it's built-in code agent (visible to all)
    - Agent if it's built-in database agent (visible to all)
    - Agent if it's personal and owned by authenticated user
    - 404 if agent doesn't exist or user doesn't have access
    """
    from app.services.builtin_agents import get_builtin_agent_definitions
    
    # Check built-in code agents first
    builtin_agents = get_builtin_agent_definitions()
    for builtin_def in builtin_agents:
        if builtin_def.id == agent_id:
            logger.info(
                "builtin_agent_accessed",
                agent_id=str(agent_id),
                agent_name=builtin_def.name,
                user_id=principal.sub,
            )
            return builtin_def
    
    # Check database for personal or database-based built-in agents
    agent = await session.get(AgentDefinition, agent_id)
    
    # Return 404 if agent doesn't exist or is inactive
    if not agent or not agent.is_active:
        logger.warning(
            "agent_access_denied",
            agent_id=str(agent_id),
            user_id=principal.sub,
            reason="not_found_or_inactive"
        )
        raise HTTPException(status_code=404, detail="Agent not found")
    
    if not can_access_agent(principal, agent):
        logger.warning(
            "agent_access_denied",
            agent_id=str(agent_id),
            user_id=principal.sub,
            owner=agent.created_by,
            reason="unauthorized_personal_agent",
        )
        raise HTTPException(status_code=404, detail="Agent not found")
    
    logger.info(
        "agent_accessed",
        agent_id=str(agent_id),
        agent_name=agent.name,
        user_id=principal.sub,
        is_builtin=agent.is_builtin
    )
    
    return AgentDefinitionRead.model_validate(agent)
