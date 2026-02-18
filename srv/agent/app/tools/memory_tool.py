"""
Agent memory tools built on top of InsightsService.
"""

import logging
import time
import uuid
from typing import List, Optional

from pydantic import BaseModel, Field
from pydantic_ai import Tool, RunContext

from app.agents.core import BusiboxDeps
from app.api.insights import get_insights_service
from app.services.insights_service import ChatInsight

logger = logging.getLogger(__name__)


class MemoryItem(BaseModel):
    id: str
    content: str
    category: str = "context"
    score: float
    analyzed_at: str


class MemorySearchOutput(BaseModel):
    found: bool = Field(description="Whether memories were found")
    result_count: int = Field(description="Number of memory results")
    context: str = Field(description="Formatted memory context")
    results: List[MemoryItem] = Field(default_factory=list)
    error: Optional[str] = None


class MemorySaveOutput(BaseModel):
    success: bool
    memory_id: Optional[str] = None
    message: str
    error: Optional[str] = None


async def memory_search(
    ctx: RunContext[BusiboxDeps],
    query: str,
    limit: int = 5,
    score_threshold: float = 0.8,
    half_life_days: float = 30.0,
) -> MemorySearchOutput:
    """
    Search a user's saved and learned memories.

    Uses temporal decay so recent memories are prioritized.
    """
    try:
        deps = ctx.deps if hasattr(ctx, "deps") else None
        principal = deps.principal if deps else None
        if not principal:
            return MemorySearchOutput(
                found=False,
                result_count=0,
                context="Authentication required to search memory.",
                error="unauthenticated",
            )

        service = get_insights_service()
        hits = await service.search_insights(
            query=query,
            user_id=principal.sub,
            limit=max(1, min(limit, 20)),
            score_threshold=score_threshold,
            apply_temporal_decay=True,
            half_life_days=half_life_days,
        )
        if not hits:
            return MemorySearchOutput(
                found=False,
                result_count=0,
                context=f"No memory found for query '{query}'.",
                results=[],
            )

        items: List[MemoryItem] = []
        context_lines = [f"Memory search for '{query}':"]
        for hit in hits:
            items.append(
                MemoryItem(
                    id=hit.id,
                    content=hit.content,
                    category=hit.category,
                    score=hit.score,
                    analyzed_at=hit.analyzed_at.isoformat(),
                )
            )
            context_lines.append(f"- [{hit.category}] {hit.content}")

        return MemorySearchOutput(
            found=True,
            result_count=len(items),
            context="\n".join(context_lines),
            results=items,
        )
    except Exception as e:
        logger.error("memory_search failed: %s", e, exc_info=True)
        return MemorySearchOutput(
            found=False,
            result_count=0,
            context="Memory search failed.",
            error=str(e),
        )


async def memory_save(
    ctx: RunContext[BusiboxDeps],
    content: str,
    category: str = "context",
) -> MemorySaveOutput:
    """
    Persist an explicit memory for the current user.
    """
    try:
        deps = ctx.deps if hasattr(ctx, "deps") else None
        principal = deps.principal if deps else None
        if not principal:
            return MemorySaveOutput(
                success=False,
                message="Authentication required to save memory.",
                error="unauthenticated",
            )

        category = (category or "context").strip().lower()
        if category not in ChatInsight.VALID_CATEGORIES:
            category = "context"

        user_id = principal.sub
        service = get_insights_service()
        embedding = await service.generate_embedding(content, user_id=user_id)
        memory_id = str(uuid.uuid4())
        service.insert_insights(
            [
                ChatInsight(
                    id=memory_id,
                    user_id=user_id,
                    content=content.strip(),
                    embedding=embedding,
                    conversation_id=f"memory:manual:{user_id}",
                    analyzed_at=int(time.time()),
                    category=category,
                )
            ]
        )
        return MemorySaveOutput(
            success=True,
            memory_id=memory_id,
            message="Memory saved.",
        )
    except Exception as e:
        logger.error("memory_save failed: %s", e, exc_info=True)
        return MemorySaveOutput(
            success=False,
            message="Failed to save memory.",
            error=str(e),
        )


memory_search_tool = Tool(
    memory_search,
    takes_ctx=True,
    name="memory_search",
    description="Search the user's saved and learned memories with recency-aware ranking.",
)

memory_save_tool = Tool(
    memory_save,
    takes_ctx=True,
    name="memory_save",
    description="Save an explicit long-term memory for the current user.",
)
