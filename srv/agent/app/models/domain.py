import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, Column, DateTime, Enum, Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

# Use JSON for SQLite compatibility, JSONB for PostgreSQL
try:
    from sqlalchemy.dialects.postgresql import JSONB as JSONType
except ImportError:
    JSONType = JSON  # type: ignore


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    # Return timezone-naive UTC datetime for PostgreSQL TIMESTAMP WITHOUT TIME ZONE
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AgentDefinition(Base):
    __tablename__ = "agent_definitions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(255))
    instructions: Mapped[str] = mapped_column(Text)
    tools: Mapped[dict] = mapped_column(JSON, default=dict)
    workflow: Mapped[Optional[dict]] = mapped_column(JSON, default=None)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[Optional[str]] = mapped_column(String(255))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now
    )
    
    def __repr__(self) -> str:
        return f"<AgentDefinition(id={self.id}, name={self.name}, is_builtin={self.is_builtin}, version={self.version})>"


class ToolDefinition(Base):
    __tablename__ = "tool_definitions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    schema: Mapped[dict] = mapped_column(JSON, default=dict)
    entrypoint: Mapped[str] = mapped_column(String(255), comment="registered adapter name")
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[Optional[str]] = mapped_column(String(255))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now
    )
    
    def __repr__(self) -> str:
        return f"<ToolDefinition(id={self.id}, name={self.name}, is_builtin={self.is_builtin}, version={self.version})>"


class WorkflowDefinition(Base):
    __tablename__ = "workflow_definitions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    steps: Mapped[list] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(255))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now
    )
    
    def __repr__(self) -> str:
        return f"<WorkflowDefinition(id={self.id}, name={self.name}, version={self.version})>"


class EvalDefinition(Base):
    __tablename__ = "eval_definitions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(255))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now
    )
    
    def __repr__(self) -> str:
        return f"<EvalDefinition(id={self.id}, name={self.name}, version={self.version})>"


class RagDatabase(Base):
    __tablename__ = "rag_databases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now
    )


class RagDocument(Base):
    __tablename__ = "rag_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    rag_database_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rag_databases.id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(String(255))
    doc_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict)  # Use column name 'metadata' but attribute 'doc_metadata'
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now
    )

    database: Mapped[RagDatabase] = relationship("RagDatabase", lazy="selectin")


class RunRecord(Base):
    __tablename__ = "run_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workflow_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(50), default="pending")
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[Optional[dict]] = mapped_column(JSON)
    events: Mapped[list] = mapped_column(JSON, default=list)
    definition_snapshot: Mapped[Optional[dict]] = mapped_column(JSONType)
    parent_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("run_records.id", ondelete="SET NULL")
    )
    resume_from_step: Mapped[Optional[str]] = mapped_column(String(255))
    workflow_state: Mapped[Optional[dict]] = mapped_column(JSONType)
    created_by: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now
    )
    
    def __repr__(self) -> str:
        return f"<RunRecord(id={self.id}, agent_id={self.agent_id}, status={self.status}, parent_run_id={self.parent_run_id})>"


class TokenGrant(Base):
    __tablename__ = "token_grants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    subject: Mapped[str] = mapped_column(String(255), index=True)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    token: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Conversation(Base):
    """Chat conversation between user and agents"""
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    # Relationships
    messages: Mapped[list["Message"]] = relationship(
        "Message", back_populates="conversation", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index('idx_conversations_user_id', 'user_id'),
        Index('idx_conversations_created_at', 'created_at'),
    )

    def __repr__(self) -> str:
        return f"<Conversation(id={self.id}, user_id={self.user_id}, title={self.title})>"


class Message(Base):
    """Individual message in a conversation"""
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False
    )
    role: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # 'user', 'assistant', 'system'
    content: Mapped[str] = mapped_column(Text, nullable=False)
    attachments: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey('run_records.id'), nullable=True
    )
    routing_decision: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    tool_calls: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    # Relationships
    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")
    run: Mapped[Optional["RunRecord"]] = relationship("RunRecord")

    __table_args__ = (
        Index('idx_messages_conversation_id', 'conversation_id'),
        Index('idx_messages_created_at', 'created_at'),
        Index('idx_messages_run_id', 'run_id'),
    )

    def __repr__(self) -> str:
        return f"<Message(id={self.id}, conversation_id={self.conversation_id}, role={self.role})>"


class ChatSettings(Base):
    """User chat preferences"""
    __tablename__ = "chat_settings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    enabled_tools: Mapped[list] = mapped_column(ARRAY(String), default=list)
    enabled_agents: Mapped[list] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)
    model: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    temperature: Mapped[float] = mapped_column(Float, default=0.7)
    max_tokens: Mapped[int] = mapped_column(Integer, default=2000)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    def __repr__(self) -> str:
        return f"<ChatSettings(id={self.id}, user_id={self.user_id})>"
