import uuid
from typing import Any, Dict

from pydantic_ai import Agent
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.core import BusiboxDeps
from app.clients.busibox import BusiboxClient
from app.models.domain import RunRecord
from app.schemas.auth import Principal
from app.services.agent_registry import agent_registry
from app.services.token_service import get_or_exchange_token


async def create_run(
    session: AsyncSession,
    principal: Principal,
    agent_id: uuid.UUID,
    payload: Dict[str, Any],
    scopes: list[str],
    purpose: str,
) -> RunRecord:
    token = await get_or_exchange_token(session, principal, scopes=scopes, purpose=purpose)
    client = BusiboxClient(token.access_token)
    agent: Agent[BusiboxDeps, object] = agent_registry.get(agent_id)
    deps = BusiboxDeps(principal=principal, busibox_client=client)

    run_record = RunRecord(
        agent_id=agent_id,
        status="running",
        input=payload,
        created_by=principal.sub,
        events=[],
    )
    session.add(run_record)
    await session.commit()
    await session.refresh(run_record)

    result = await agent.run(payload.get("prompt"), deps=deps)
    run_record.status = "succeeded"
    run_record.output = result.output if hasattr(result, "output") else {"output": result}
    await session.commit()
    await session.refresh(run_record)
    return run_record
