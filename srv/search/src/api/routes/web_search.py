"""
Web Search API routes.
"""

import structlog
from fastapi import APIRouter, HTTPException, Request, Depends
from typing import List

from shared.schemas import (
    WebSearchRequest,
    WebSearchResponse,
    WebSearchProviderInfo,
    WebSearchProviderConfig,
)
from services.web_search_providers import WebSearchProviderFactory

logger = structlog.get_logger()

router = APIRouter()


def get_pg_pool(request: Request):
    """Get PostgreSQL pool manager from app state."""
    return request.app.state.pg_pool


@router.post("", response_model=WebSearchResponse)
async def web_search(
    search_request: WebSearchRequest,
    request: Request,
):
    """
    Perform web search using configured providers.
    
    Supports multiple providers:
    - tavily: AI-optimized search with clean results
    - duckduckgo: Privacy-focused, no API key required
    - serpapi: Google search results
    - perplexity: AI-powered search with citations
    - bing: Microsoft Bing search
    
    If no provider is specified, uses the default provider from database.
    """
    pg_pool = get_pg_pool(request)
    
    try:
        # Load providers from database
        async with pg_pool.acquire() as conn:
            # Determine which provider to use
            if search_request.provider:
                provider_name = search_request.provider.lower()
                
                # Check if provider exists and is enabled
                row = await conn.fetchrow(
                    """
                    SELECT provider, api_key, endpoint, is_enabled
                    FROM web_search_providers
                    WHERE provider = $1
                    """,
                    provider_name,
                )
                
                if not row:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Provider '{provider_name}' not found",
                    )
                
                if not row["is_enabled"]:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Provider '{provider_name}' is disabled",
                    )
                
                provider = WebSearchProviderFactory.create_provider(
                    row["provider"],
                    row["api_key"],
                    row["endpoint"],
                )
            else:
                # Use default provider
                row = await conn.fetchrow(
                    """
                    SELECT provider, api_key, endpoint
                    FROM web_search_providers
                    WHERE is_default = true AND is_enabled = true
                    LIMIT 1
                    """
                )
                
                if not row:
                    raise HTTPException(
                        status_code=400,
                        detail="No default provider configured. Please specify a provider.",
                    )
                
                provider = WebSearchProviderFactory.create_provider(
                    row["provider"],
                    row["api_key"],
                    row["endpoint"],
                )
        
        # Perform search
        response = await provider.search(
            query=search_request.query,
            max_results=search_request.max_results,
            search_depth=search_request.search_depth,
            include_answer=search_request.include_answer,
            include_domains=search_request.include_domains,
            exclude_domains=search_request.exclude_domains,
        )
        
        logger.info(
            "Web search completed",
            provider=provider.name,
            query=search_request.query,
            results_count=len(response.results),
        )
        
        return WebSearchResponse(**response.to_dict())
    
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Web search failed", error=str(e), query=search_request.query)
        raise HTTPException(
            status_code=500,
            detail=f"Web search failed: {str(e)}",
        )


@router.get("/providers", response_model=List[WebSearchProviderInfo])
async def list_providers(request: Request):
    """
    List all configured web search providers.
    
    Returns information about each provider including:
    - Whether it's enabled
    - Whether it's the default
    - Whether it has required configuration (API keys)
    """
    pg_pool = get_pg_pool(request)
    
    try:
        async with pg_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT provider, is_enabled, is_default, api_key
                FROM web_search_providers
                ORDER BY is_default DESC, provider ASC
                """
            )
            
            providers = []
            for row in rows:
                # Check if provider is configured (has API key or doesn't need one)
                is_configured = row["provider"] == "duckduckgo" or bool(row["api_key"])
                
                providers.append(
                    WebSearchProviderInfo(
                        provider=row["provider"],
                        is_enabled=row["is_enabled"],
                        is_default=row["is_default"],
                        is_configured=is_configured,
                    )
                )
            
            return providers
    
    except Exception as e:
        logger.error("Failed to list providers", error=str(e))
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list providers: {str(e)}",
        )


@router.get("/providers/{provider}/status", response_model=WebSearchProviderInfo)
async def get_provider_status(provider: str, request: Request):
    """
    Get status of a specific provider.
    """
    pg_pool = get_pg_pool(request)
    
    try:
        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT provider, is_enabled, is_default, api_key
                FROM web_search_providers
                WHERE provider = $1
                """,
                provider.lower(),
            )
            
            if not row:
                raise HTTPException(
                    status_code=404,
                    detail=f"Provider '{provider}' not found",
                )
            
            is_configured = row["provider"] == "duckduckgo" or bool(row["api_key"])
            
            return WebSearchProviderInfo(
                provider=row["provider"],
                is_enabled=row["is_enabled"],
                is_default=row["is_default"],
                is_configured=is_configured,
            )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get provider status", provider=provider, error=str(e))
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get provider status: {str(e)}",
        )


# ============================================================================
# Admin Endpoints
# ============================================================================

@router.post("/admin/providers", response_model=WebSearchProviderInfo)
async def upsert_provider(
    config: WebSearchProviderConfig,
    request: Request,
):
    """
    Add or update a web search provider configuration.
    
    Requires admin authentication (checked by middleware).
    """
    pg_pool = get_pg_pool(request)
    
    try:
        # Validate provider name
        valid_providers = ["tavily", "duckduckgo", "serpapi", "perplexity", "bing"]
        if config.provider.lower() not in valid_providers:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid provider. Must be one of: {', '.join(valid_providers)}",
            )
        
        # Validate API key requirement
        if config.provider.lower() != "duckduckgo" and not config.api_key:
            raise HTTPException(
                status_code=400,
                detail=f"Provider '{config.provider}' requires an API key",
            )
        
        async with pg_pool.acquire() as conn:
            # If setting as default, unset current default
            if config.is_default:
                await conn.execute(
                    """
                    UPDATE web_search_providers
                    SET is_default = false
                    WHERE is_default = true
                    """
                )
            
            # Upsert provider
            row = await conn.fetchrow(
                """
                INSERT INTO web_search_providers (provider, api_key, endpoint, is_enabled, is_default)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (provider) DO UPDATE
                SET api_key = EXCLUDED.api_key,
                    endpoint = EXCLUDED.endpoint,
                    is_enabled = EXCLUDED.is_enabled,
                    is_default = EXCLUDED.is_default,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING provider, is_enabled, is_default, api_key
                """,
                config.provider.lower(),
                config.api_key,
                config.endpoint,
                config.is_enabled,
                config.is_default,
            )
            
            is_configured = row["provider"] == "duckduckgo" or bool(row["api_key"])
            
            logger.info(
                "Provider configuration updated",
                provider=config.provider,
                is_enabled=config.is_enabled,
                is_default=config.is_default,
            )
            
            return WebSearchProviderInfo(
                provider=row["provider"],
                is_enabled=row["is_enabled"],
                is_default=row["is_default"],
                is_configured=is_configured,
            )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to update provider", provider=config.provider, error=str(e))
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update provider: {str(e)}",
        )


@router.delete("/admin/providers/{provider}")
async def delete_provider(provider: str, request: Request):
    """
    Delete a web search provider configuration.
    
    Requires admin authentication (checked by middleware).
    """
    pg_pool = get_pg_pool(request)
    
    try:
        async with pg_pool.acquire() as conn:
            # Check if provider exists
            row = await conn.fetchrow(
                """
                SELECT provider, is_default
                FROM web_search_providers
                WHERE provider = $1
                """,
                provider.lower(),
            )
            
            if not row:
                raise HTTPException(
                    status_code=404,
                    detail=f"Provider '{provider}' not found",
                )
            
            # Don't allow deleting the default provider
            if row["is_default"]:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot delete the default provider. Set another provider as default first.",
                )
            
            # Delete provider
            await conn.execute(
                """
                DELETE FROM web_search_providers
                WHERE provider = $1
                """,
                provider.lower(),
            )
            
            logger.info("Provider deleted", provider=provider)
            
            return {"message": f"Provider '{provider}' deleted successfully"}
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to delete provider", provider=provider, error=str(e))
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete provider: {str(e)}",
        )


@router.put("/admin/providers/{provider}/default")
async def set_default_provider(provider: str, request: Request):
    """
    Set a provider as the default.
    
    Requires admin authentication (checked by middleware).
    """
    pg_pool = get_pg_pool(request)
    
    try:
        async with pg_pool.acquire() as conn:
            # Check if provider exists and is enabled
            row = await conn.fetchrow(
                """
                SELECT provider, is_enabled
                FROM web_search_providers
                WHERE provider = $1
                """,
                provider.lower(),
            )
            
            if not row:
                raise HTTPException(
                    status_code=404,
                    detail=f"Provider '{provider}' not found",
                )
            
            if not row["is_enabled"]:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot set disabled provider '{provider}' as default",
                )
            
            # Unset current default
            await conn.execute(
                """
                UPDATE web_search_providers
                SET is_default = false
                WHERE is_default = true
                """
            )
            
            # Set new default
            await conn.execute(
                """
                UPDATE web_search_providers
                SET is_default = true
                WHERE provider = $1
                """,
                provider.lower(),
            )
            
            logger.info("Default provider updated", provider=provider)
            
            return {"message": f"Provider '{provider}' set as default"}
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to set default provider", provider=provider, error=str(e))
        raise HTTPException(
            status_code=500,
            detail=f"Failed to set default provider: {str(e)}",
        )

