"""
Web Search Providers for Search API

Abstraction layer supporting multiple search APIs:
- Tavily (recommended for AI applications)
- DuckDuckGo (no API key required)
- SerpAPI (Google search results)
- Perplexity (AI-powered search)
- Microsoft Bing (via Bing Search API)
"""

import httpx
import structlog
from abc import ABC, abstractmethod
from typing import List, Dict, Optional, Any
from datetime import datetime
from duckduckgo_search import DDGS

logger = structlog.get_logger()


class SearchResult:
    """Individual search result."""
    
    def __init__(
        self,
        title: str,
        url: str,
        snippet: str,
        score: Optional[float] = None,
        published_date: Optional[str] = None,
        domain: Optional[str] = None,
    ):
        self.title = title
        self.url = url
        self.snippet = snippet
        self.score = score
        self.published_date = published_date
        self.domain = domain
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        result = {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
        }
        if self.score is not None:
            result["score"] = self.score
        if self.published_date:
            result["publishedDate"] = self.published_date
        if self.domain:
            result["domain"] = self.domain
        return result


class SearchResponse:
    """Search response containing results and metadata."""
    
    def __init__(
        self,
        query: str,
        results: List[SearchResult],
        provider: str,
        timestamp: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.query = query
        self.results = results
        self.provider = provider
        self.timestamp = timestamp or datetime.utcnow().isoformat()
        self.metadata = metadata or {}
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "query": self.query,
            "results": [r.to_dict() for r in self.results],
            "provider": self.provider,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


class WebSearchProvider(ABC):
    """Base class for web search providers."""
    
    def __init__(self, name: str, api_key: Optional[str] = None, endpoint: Optional[str] = None):
        self.name = name
        self.api_key = api_key
        self.endpoint = endpoint
    
    @abstractmethod
    async def search(
        self,
        query: str,
        max_results: int = 5,
        search_depth: str = "basic",
        include_answer: bool = False,
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
    ) -> SearchResponse:
        """Perform search and return results."""
        pass
    
    def is_configured(self) -> bool:
        """Check if provider is properly configured."""
        # DuckDuckGo doesn't need an API key
        if self.name == "duckduckgo":
            return True
        return bool(self.api_key)


class TavilyProvider(WebSearchProvider):
    """
    Tavily Search Provider
    Optimized for AI/LLM applications with clean, structured results
    """
    
    def __init__(self, api_key: str):
        super().__init__("tavily", api_key)
    
    async def search(
        self,
        query: str,
        max_results: int = 5,
        search_depth: str = "basic",
        include_answer: bool = False,
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
    ) -> SearchResponse:
        """Search using Tavily API."""
        if not self.is_configured():
            raise ValueError("Tavily API key not configured")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            payload = {
                "api_key": self.api_key,
                "query": query,
                "max_results": max_results,
                "search_depth": search_depth,
                "include_answer": include_answer,
            }
            
            if include_domains:
                payload["include_domains"] = include_domains
            if exclude_domains:
                payload["exclude_domains"] = exclude_domains
            
            response = await client.post(
                "https://api.tavily.com/search",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            
            results = [
                SearchResult(
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    snippet=r.get("content", ""),
                    score=r.get("score"),
                    published_date=r.get("published_date"),
                )
                for r in data.get("results", [])
            ]
            
            metadata = {}
            if "answer" in data:
                metadata["answer"] = data["answer"]
            if "images" in data:
                metadata["images"] = data["images"]
            
            return SearchResponse(
                query=query,
                results=results,
                provider=self.name,
                metadata=metadata,
            )


class DuckDuckGoProvider(WebSearchProvider):
    """
    DuckDuckGo Search Provider
    Free, privacy-focused search (no API key required)
    """
    
    def __init__(self):
        super().__init__("duckduckgo")
    
    async def search(
        self,
        query: str,
        max_results: int = 5,
        search_depth: str = "basic",
        include_answer: bool = False,
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
    ) -> SearchResponse:
        """Search using DuckDuckGo."""
        try:
            # DuckDuckGo search is synchronous, but we'll run it in executor
            # to keep the async interface consistent
            with DDGS() as ddgs:
                raw_results = list(ddgs.text(query, max_results=max_results))
            
            results = [
                SearchResult(
                    title=r.get("title", ""),
                    url=r.get("href", ""),
                    snippet=r.get("body", ""),
                )
                for r in raw_results
            ]
            
            return SearchResponse(
                query=query,
                results=results,
                provider=self.name,
            )
        except Exception as e:
            logger.error("DuckDuckGo search failed", error=str(e))
            raise ValueError(f"DuckDuckGo search failed: {str(e)}")


class SerpAPIProvider(WebSearchProvider):
    """
    SerpAPI Provider
    Access to Google search results with various search types
    """
    
    def __init__(self, api_key: str):
        super().__init__("serpapi", api_key)
    
    async def search(
        self,
        query: str,
        max_results: int = 5,
        search_depth: str = "basic",
        include_answer: bool = False,
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
    ) -> SearchResponse:
        """Search using SerpAPI."""
        if not self.is_configured():
            raise ValueError("SerpAPI API key not configured")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            params = {
                "q": query,
                "api_key": self.api_key,
                "num": max_results,
                "engine": "google",
            }
            
            response = await client.get(
                "https://serpapi.com/search",
                params=params,
            )
            response.raise_for_status()
            data = response.json()
            
            organic_results = data.get("organic_results", [])
            results = [
                SearchResult(
                    title=r.get("title", ""),
                    url=r.get("link", ""),
                    snippet=r.get("snippet", ""),
                    domain=r.get("displayed_link"),
                )
                for r in organic_results
            ]
            
            metadata = {}
            if "answer_box" in data:
                metadata["answerBox"] = data["answer_box"]
            if "knowledge_graph" in data:
                metadata["knowledgeGraph"] = data["knowledge_graph"]
            
            return SearchResponse(
                query=query,
                results=results,
                provider=self.name,
                metadata=metadata,
            )


class PerplexityProvider(WebSearchProvider):
    """
    Perplexity Provider
    AI-powered search with natural language understanding
    """
    
    def __init__(self, api_key: str):
        super().__init__("perplexity", api_key)
    
    async def search(
        self,
        query: str,
        max_results: int = 5,
        search_depth: str = "basic",
        include_answer: bool = False,
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
    ) -> SearchResponse:
        """Search using Perplexity API."""
        if not self.is_configured():
            raise ValueError("Perplexity API key not configured")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            payload = {
                "model": "llama-3.1-sonar-small-128k-online",
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a helpful search assistant. Provide concise, accurate information with sources.",
                    },
                    {
                        "role": "user",
                        "content": query,
                    },
                ],
            }
            
            response = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            citations = data.get("citations", [])
            
            # Parse content and citations into search results
            results = [
                SearchResult(
                    title=f"Source {i + 1}",
                    url=citation,
                    snippet=content[:200],  # Use part of answer as snippet
                    score=1.0 - (i * 0.1),  # Decreasing score
                )
                for i, citation in enumerate(citations)
            ]
            
            metadata = {
                "answer": content,
                "citations": citations,
                "model": data.get("model"),
            }
            
            return SearchResponse(
                query=query,
                results=results,
                provider=self.name,
                metadata=metadata,
            )


class BingProvider(WebSearchProvider):
    """
    Microsoft Bing Provider
    Bing Web Search API v7
    """
    
    def __init__(self, api_key: str, endpoint: str = "https://api.bing.microsoft.com/v7.0/search"):
        super().__init__("bing", api_key, endpoint)
    
    async def search(
        self,
        query: str,
        max_results: int = 5,
        search_depth: str = "basic",
        include_answer: bool = False,
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
    ) -> SearchResponse:
        """Search using Bing Search API."""
        if not self.is_configured():
            raise ValueError("Bing Search API key not configured")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            params = {
                "q": query,
                "count": max_results,
                "responseFilter": "Webpages",
            }
            
            response = await client.get(
                self.endpoint,
                params=params,
                headers={
                    "Ocp-Apim-Subscription-Key": self.api_key,
                },
            )
            response.raise_for_status()
            data = response.json()
            
            web_pages = data.get("webPages", {}).get("value", [])
            results = [
                SearchResult(
                    title=r.get("name", ""),
                    url=r.get("url", ""),
                    snippet=r.get("snippet", ""),
                    published_date=r.get("dateLastCrawled"),
                    domain=r.get("displayUrl"),
                )
                for r in web_pages
            ]
            
            metadata = {}
            if "webPages" in data and "totalEstimatedMatches" in data["webPages"]:
                metadata["totalEstimatedMatches"] = data["webPages"]["totalEstimatedMatches"]
            
            return SearchResponse(
                query=query,
                results=results,
                provider=self.name,
                metadata=metadata,
            )


class WebSearchProviderFactory:
    """
    Factory for creating and managing web search providers.
    Loads configurations from database.
    """
    
    @staticmethod
    def create_provider(
        provider_name: str,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
    ) -> WebSearchProvider:
        """Create a provider instance."""
        provider_name = provider_name.lower()
        
        if provider_name == "tavily":
            if not api_key:
                raise ValueError("Tavily requires an API key")
            return TavilyProvider(api_key)
        
        elif provider_name == "duckduckgo":
            return DuckDuckGoProvider()
        
        elif provider_name == "serpapi":
            if not api_key:
                raise ValueError("SerpAPI requires an API key")
            return SerpAPIProvider(api_key)
        
        elif provider_name == "perplexity":
            if not api_key:
                raise ValueError("Perplexity requires an API key")
            return PerplexityProvider(api_key)
        
        elif provider_name == "bing":
            if not api_key:
                raise ValueError("Bing requires an API key")
            return BingProvider(api_key, endpoint or "https://api.bing.microsoft.com/v7.0/search")
        
        else:
            raise ValueError(f"Unknown provider: {provider_name}")
    
    @staticmethod
    async def load_providers_from_db(pool) -> Dict[str, WebSearchProvider]:
        """Load all enabled providers from database."""
        providers = {}
        
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT provider, api_key, endpoint
                FROM web_search_providers
                WHERE is_enabled = true
                """
            )
            
            for row in rows:
                try:
                    provider = WebSearchProviderFactory.create_provider(
                        row["provider"],
                        row["api_key"],
                        row["endpoint"],
                    )
                    providers[row["provider"]] = provider
                    logger.info(f"Loaded web search provider: {row['provider']}")
                except Exception as e:
                    logger.error(
                        f"Failed to load provider {row['provider']}",
                        error=str(e),
                    )
        
        return providers
    
    @staticmethod
    async def get_default_provider(pool) -> Optional[str]:
        """Get the default provider name from database."""
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT provider
                FROM web_search_providers
                WHERE is_default = true AND is_enabled = true
                LIMIT 1
                """
            )
            return row["provider"] if row else None

