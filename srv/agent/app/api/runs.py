import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_principal
from app.db.session import get_session
from app.schemas.auth import Principal
from app.schemas.run import RunCreate, RunRead
from app.services.run_service import create_run
from app.services.scheduler import run_scheduler
from app.db.session import SessionLocal

router = APIRouter(prefix="/runs", tags=["runs"])


@router.post("", response_model=RunRead, status_code=status.HTTP_202_ACCEPTED)
async def run_agent(
    payload: RunCreate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> RunRead:
    run_record = await create_run(
        session=session,
        principal=principal,
        agent_id=payload.agent_id,
        payload=payload.input,
        scopes=["search.read", "ingest.write", "rag.query"],
        purpose="agent-run",
    )
    return RunRead.model_validate(run_record)


@router.post("/schedule", status_code=status.HTTP_202_ACCEPTED)
async def schedule_run(
    payload: RunCreate,
    cron: str,
    principal: Principal = Depends(get_principal),
):
    """
    Schedule a cron-based agent run. Uses shared scheduler.
    """
    run_scheduler.schedule_agent_run(
        session_factory=SessionLocal,
        principal=principal,
        agent_id=payload.agent_id,
        payload=payload.input,
        scopes=["search.read", "ingest.write", "rag.query"],
        purpose="agent-run",
        cron=cron,
    )
    return {"status": "scheduled", "cron": cron}
