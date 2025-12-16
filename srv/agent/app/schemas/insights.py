"""
Pydantic schemas for chat insights API.

Insights are agent memories/context extracted from conversations and stored
in Milvus for vector similarity search.
"""

from typing import List
from pydantic import BaseModel, Field


class ChatInsight(BaseModel):
    """Chat insight entity."""
    
    id: str = Field(..., description="Insight ID")
    user_id: str = Field(..., description="User ID who owns this insight", alias="userId")
    content: str = Field(..., description="The insight text")
    embedding: List[float] = Field(..., description="Vector embedding (1024 dimensions)")
    conversation_id: str = Field(..., description="Source conversation ID", alias="conversationId")
    analyzed_at: int = Field(..., description="Unix timestamp when insight was extracted", alias="analyzedAt")
    
    class Config:
        populate_by_name = True


class InsertInsightsRequest(BaseModel):
    """Request to insert insights."""
    
    insights: List[ChatInsight] = Field(..., description="List of insights to insert", min_items=1)


class InsightSearchRequest(BaseModel):
    """Request to search insights."""
    
    query: str = Field(..., description="Search query", min_length=1, max_length=500)
    user_id: str = Field(..., description="User ID to filter results", alias="userId")
    limit: int = Field(3, description="Maximum number of results", ge=1, le=20)
    score_threshold: float = Field(0.7, description="Maximum L2 distance threshold", ge=0.0, le=2.0, alias="scoreThreshold")
    
    class Config:
        populate_by_name = True


class InsightSearchResult(BaseModel):
    """Search result for chat insights."""
    
    id: str = Field(..., description="Insight ID")
    user_id: str = Field(..., description="User ID", alias="userId")
    content: str = Field(..., description="Insight content")
    conversation_id: str = Field(..., description="Conversation ID", alias="conversationId")
    analyzed_at: str = Field(..., description="ISO timestamp", alias="analyzedAt")
    score: float = Field(..., description="Similarity score (L2 distance)")
    
    class Config:
        populate_by_name = True


class InsightSearchResponse(BaseModel):
    """Response for insight search."""
    
    query: str = Field(..., description="Original query")
    results: List[InsightSearchResult] = Field(..., description="Search results")
    count: int = Field(..., description="Number of results returned")


class InsightStatsResponse(BaseModel):
    """Statistics for user insights."""
    
    user_id: str = Field(..., description="User ID", alias="userId")
    count: int = Field(..., description="Number of insights")
    collection_name: str = Field(..., description="Collection name", alias="collectionName")
    
    class Config:
        populate_by_name = True
