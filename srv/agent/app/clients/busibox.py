from typing import Any, Dict, List, Optional

import httpx

from app.config.settings import get_settings

settings = get_settings()


class BusiboxClient:
    """
    Thin HTTP client for Busibox APIs (search, ingest, RAG).
    Attaches downstream bearer tokens obtained via token exchange.
    """

    def __init__(self, access_token: str) -> None:
        self._token = access_token
        self._headers = {"Authorization": f"Bearer {self._token}"}

    async def search(self, query: str, top_k: int = 10) -> Dict[str, Any]:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{settings.search_api_url}/search",
                json={"query": query, "top_k": top_k},
                headers=self._headers,
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()

    async def ingest_document(self, path: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{settings.ingest_api_url}/documents",
                json={"path": path, "metadata": metadata or {}},
                headers=self._headers,
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json()

    async def rag_query(self, database: str, query: str, top_k: int = 5) -> Dict[str, Any]:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{settings.rag_api_url}/databases/{database}/query",
                json={"query": query, "top_k": top_k},
                headers=self._headers,
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()
