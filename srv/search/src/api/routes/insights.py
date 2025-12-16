"""
Chat Insights API routes.
"""

import structlog
from fastapi import APIRouter, HTTPException, Request
from typing import Dict

from shared.schemas import (
    InsertInsightsRequest,
    InsightSearchRequest,
    InsightSearchResponse,
    InsightSearchResult,
    InsightStatsResponse,
)
from services.insights_service import InsightsService, ChatInsight as ServiceChatInsight

logger = structlog.get_logger()

router = APIRouter()

# Global insights service instance
insights_service: InsightsService = None


def get_insights_service(request: Request) -> InsightsService:
    """Get insights service from app state."""
    return request.app.state.insights_service


@router.post("/init")
async def initialize_collection(request: Request):
    """
    Initialize the chat_insights collection in Milvus.
    
    Creates the collection with schema if it doesn't exist.
    This endpoint is idempotent - safe to call multiple times.
    """
    service = get_insights_service(request)
    
    try:
        service.initialize_collection()
        return {
            "message": "Collection initialized successfully",
            "collection": "chat_insights",
        }
    except Exception as e:
        logger.error("Failed to initialize collection", error=str(e))
        raise HTTPException(
            status_code=500,
            detail=f"Failed to initialize collection: {str(e)}",
        )


@router.post("")
async def insert_insights(
    insert_request: InsertInsightsRequest,
    request: Request,
):
    """
    Insert insights into Milvus.
    
    Requires authentication via Bearer token.
    """
    service = get_insights_service(request)
    user_id = request.state.user_id
    
    try:
        # Convert Pydantic models to service models
        service_insights = [
            ServiceChatInsight(
                id=insight.id,
                user_id=insight.user_id,
                content=insight.content,
                embedding=insight.embedding,
                conversation_id=insight.conversation_id,
                analyzed_at=insight.analyzed_at,
            )
            for insight in insert_request.insights
        ]
        
        service.insert_insights(service_insights)
        
        logger.info(
            "Insights inserted",
            user_id=user_id,
            count=len(service_insights),
        )
        
        return {
            "message": f"Successfully inserted {len(service_insights)} insights",
            "count": len(service_insights),
        }
    
    except Exception as e:
        logger.error("Failed to insert insights", user_id=user_id, error=str(e))
        raise HTTPException(
            status_code=500,
            detail=f"Failed to insert insights: {str(e)}",
        )


@router.post("/search", response_model=InsightSearchResponse)
async def search_insights(
    search_request: InsightSearchRequest,
    request: Request,
):
    """
    Search for relevant insights based on query.
    
    Filters results by user_id to ensure users only see their own insights.
    Requires authentication via Bearer token.
    """
    service = get_insights_service(request)
    user_id = request.state.user_id
    authorization = getattr(request.state, 'authorization', None)
    
    # Verify user_id matches authenticated user
    if search_request.user_id != user_id:
        raise HTTPException(
            status_code=403,
            detail="Cannot search insights for other users",
        )
    
    try:
        results = await service.search_insights(
            query=search_request.query,
            user_id=search_request.user_id,
            authorization=authorization,
            limit=search_request.limit,
            score_threshold=search_request.score_threshold,
        )
        
        # Convert service results to API results
        api_results = [
            InsightSearchResult(
                id=r.id,
                userId=r.user_id,
                content=r.content,
                conversationId=r.conversation_id,
                analyzedAt=r.analyzed_at.isoformat(),
                score=r.score,
            )
            for r in results
        ]
        
        logger.info(
            "Insights search completed",
            user_id=user_id,
            query=search_request.query,
            results_count=len(api_results),
        )
        
        return InsightSearchResponse(
            query=search_request.query,
            results=api_results,
            count=len(api_results),
        )
    
    except Exception as e:
        logger.error(
            "Insights search failed",
            user_id=user_id,
            query=search_request.query,
            error=str(e),
        )
        raise HTTPException(
            status_code=500,
            detail=f"Insights search failed: {str(e)}",
        )


@router.delete("/conversation/{conversation_id}")
async def delete_conversation_insights(
    conversation_id: str,
    request: Request,
):
    """
    Delete insights for a conversation.
    
    Requires authentication. Users can only delete their own insights.
    """
    service = get_insights_service(request)
    user_id = request.state.user_id
    
    try:
        service.delete_conversation_insights(conversation_id, user_id)
        
        logger.info(
            "Conversation insights deleted",
            conversation_id=conversation_id,
            user_id=user_id,
        )
        
        return {
            "message": f"Deleted insights for conversation {conversation_id}",
            "conversationId": conversation_id,
        }
    
    except Exception as e:
        logger.error(
            "Failed to delete conversation insights",
            conversation_id=conversation_id,
            user_id=user_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete conversation insights: {str(e)}",
        )


@router.delete("/user/{user_id}")
async def delete_user_insights(
    user_id: str,
    request: Request,
):
    """
    Delete all insights for a user (for account deletion/cleanup).
    
    Requires authentication. Users can only delete their own insights.
    """
    service = get_insights_service(request)
    authenticated_user_id = request.state.user_id
    
    # Verify user can only delete their own insights
    if user_id != authenticated_user_id:
        raise HTTPException(
            status_code=403,
            detail="Cannot delete insights for other users",
        )
    
    try:
        service.delete_user_insights(user_id)
        
        logger.info("User insights deleted", user_id=user_id)
        
        return {
            "message": f"Deleted all insights for user {user_id}",
            "userId": user_id,
        }
    
    except Exception as e:
        logger.error(
            "Failed to delete user insights",
            user_id=user_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete user insights: {str(e)}",
        )


@router.get("/stats/{user_id}", response_model=InsightStatsResponse)
async def get_user_stats(
    user_id: str,
    request: Request,
):
    """
    Get statistics for user insights.
    
    Requires authentication. Users can only view their own stats.
    """
    service = get_insights_service(request)
    authenticated_user_id = request.state.user_id
    
    # Verify user can only view their own stats
    if user_id != authenticated_user_id:
        raise HTTPException(
            status_code=403,
            detail="Cannot view stats for other users",
        )
    
    try:
        count = service.get_user_insight_count(user_id)
        stats = service.get_collection_stats()
        
        return InsightStatsResponse(
            userId=user_id,
            count=count,
            collectionName=stats.get("collectionName", "chat_insights"),
        )
    
    except Exception as e:
        logger.error(
            "Failed to get user stats",
            user_id=user_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get user stats: {str(e)}",
        )


@router.post("/flush")
async def flush_collection(request: Request):
    """
    Flush collection to ensure data persistence.
    
    Call this after batch inserts for data durability.
    Requires authentication.
    """
    service = get_insights_service(request)
    
    try:
        service.flush_collection()
        
        logger.info("Collection flushed")
        
        return {
            "message": "Collection flushed successfully",
            "collection": "chat_insights",
        }
    
    except Exception as e:
        logger.error("Failed to flush collection", error=str(e))
        raise HTTPException(
            status_code=500,
            detail=f"Failed to flush collection: {str(e)}",
        )

