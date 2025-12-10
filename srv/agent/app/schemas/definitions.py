import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AgentDefinitionCreate(BaseModel):
    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    model: str
    instructions: str
    tools: Dict[str, Any] = Field(default_factory=dict)
    workflow: Optional[Dict[str, Any]] = None
    scopes: List[str] = Field(default_factory=list)
    is_active: bool = True


class AgentDefinitionRead(AgentDefinitionCreate):
    id: uuid.UUID
    version: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ToolDefinitionCreate(BaseModel):
    name: str
    description: Optional[str] = None
    schema: Dict[str, Any] = Field(default_factory=dict)
    entrypoint: str
    scopes: List[str] = Field(default_factory=list)
    is_active: bool = True


class ToolDefinitionRead(ToolDefinitionCreate):
    id: uuid.UUID
    version: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class WorkflowDefinitionCreate(BaseModel):
    name: str
    description: Optional[str] = None
    steps: List[Dict[str, Any]] = Field(default_factory=list)
    is_active: bool = True


class WorkflowDefinitionRead(WorkflowDefinitionCreate):
    id: uuid.UUID
    version: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class EvalDefinitionCreate(BaseModel):
    name: str
    description: Optional[str] = None
    config: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


class EvalDefinitionRead(EvalDefinitionCreate):
    id: uuid.UUID
    version: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
