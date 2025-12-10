from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from app.clients.busibox import BusiboxClient
from app.schemas.auth import Principal


@dataclass
class BusiboxDeps:
    principal: Principal
    busibox_client: BusiboxClient


class ChatOutput(BaseModel):
    message: str


class SearchOutput(BaseModel):
    hits: List[Dict[str, Any]] = Field(description="Search hits returned by search service")


class RagOutput(BaseModel):
    answer: str
    citations: List[Dict[str, Any]] = Field(default_factory=list)


chat_agent = Agent[BusiboxDeps, ChatOutput](
    model=None,
    instructions="You are a Busibox assistant. Keep answers concise and actionable.",
)


@chat_agent.tool
async def search_tool(ctx: RunContext[BusiboxDeps], query: str, top_k: int = 5) -> Dict[str, Any]:
    """Search Busibox documents."""
    return await ctx.deps.busibox_client.search(query=query, top_k=top_k)


@chat_agent.tool
async def ingest_tool(
    ctx: RunContext[BusiboxDeps], path: str, metadata: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Ingest a document path into Busibox."""
    return await ctx.deps.busibox_client.ingest_document(path=path, metadata=metadata)


@chat_agent.tool
async def rag_tool(ctx: RunContext[BusiboxDeps], database: str, query: str, top_k: int = 5) -> Dict[str, Any]:
    """Query RAG database for relevant content."""
    return await ctx.deps.busibox_client.rag_query(database=database, query=query, top_k=top_k)


@chat_agent.instructions
async def add_role_context(ctx: RunContext[BusiboxDeps]) -> str:
    roles = ", ".join(ctx.deps.principal.roles) if ctx.deps.principal.roles else "user"
    return f"User roles: {roles}"


rag_agent = Agent[BusiboxDeps, RagOutput](
    model=None,
    instructions="You are a Busibox RAG assistant. Use citations and be concise.",
    tools=[search_tool, rag_tool],
)
