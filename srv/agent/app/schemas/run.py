import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RunCreate(BaseModel):
    agent_id: uuid.UUID
    workflow_id: Optional[uuid.UUID] = None
    input: Dict[str, Any] = Field(default_factory=dict)


class RunRead(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    workflow_id: Optional[uuid.UUID]
    status: str
    input: Dict[str, Any]
    output: Optional[Dict[str, Any]]
    events: List[Any]
    created_by: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
