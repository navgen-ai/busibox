import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.auth.dependencies import get_principal
from app.db.session import get_session
from app.models.domain import RunRecord
from app.schemas.auth import Principal

router = APIRouter(prefix="/streams", tags=["streams"])


@router.get("/runs/{run_id}")
async def stream_run(
    run_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    """
    Stream run events via SSE. Polls DB for updates.
    """

    async def event_generator():
        last_status = None
        while True:
            run = await session.get(RunRecord, run_id)
            if not run:
                yield {"event": "error", "data": "run not found"}
                break
            if run.status != last_status:
                yield {"event": "status", "data": run.status}
                last_status = run.status
            if run.output:
                yield {"event": "output", "data": run.output}
                break
            await asyncio.sleep(1)

    return EventSourceResponse(event_generator())
