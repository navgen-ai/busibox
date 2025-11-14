"""
Embedding generation via liteLLM.

Generates dense semantic embeddings for text chunks using text-embedding-3-small.
"""

import asyncio
from typing import List, Optional

import httpx
import litellm
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

logger = structlog.get_logger()


class Embedder:
    """Generate embeddings for text chunks."""
    
    def __init__(self, config: dict):
        """
        Initialize embedder with configuration.
        
        Args:
            config: Configuration dictionary with litellm_base_url, embedding_model
        """
        self.config = config
        self.litellm_base_url = config.get("litellm_base_url", "http://10.96.200.30:4000")
        self.embedding_model = config.get("embedding_model", "text-embedding-3-small")
        self.api_key = config.get("litellm_api_key", "")
        
        # Batch configuration
        self.batch_size = config.get("embedding_batch_size", 50)
        self.max_retries = config.get("embedding_max_retries", 3)
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=60),
    )
    async def embed_chunks(
        self,
        chunks: List[str],
    ) -> List[List[float]]:
        """
        Generate embeddings for text chunks.
        
        Args:
            chunks: List of text chunks
        
        Returns:
            List of embedding vectors (1536 dimensions each)
        
        Raises:
            Exception: If embedding generation fails after retries
        """
        if not chunks:
            return []
        
        logger.info(
            "Generating embeddings",
            chunk_count=len(chunks),
            model=self.embedding_model,
        )
        
        try:
            # Configure litellm to use proxy server (OpenAI-compatible API)
            litellm.api_base = self.litellm_base_url
            if self.api_key:
                litellm.api_key = self.api_key
            
            # Batch processing
            all_embeddings = []
            
            for i in range(0, len(chunks), self.batch_size):
                batch = chunks[i:i + self.batch_size]
                
                logger.debug(
                    "Processing embedding batch",
                    batch_start=i,
                    batch_size=len(batch),
                )
                
                # Generate embeddings via litellm proxy
                # When using liteLLM as a proxy, we use openai/ prefix to tell the SDK
                # to use OpenAI-compatible API format (liteLLM handles the actual routing)
                response = await litellm.aembedding(
                    model=f"openai/{self.embedding_model}",
                    input=batch,
                    api_base=self.litellm_base_url,
                    api_key=self.api_key or "dummy-key",
                )
                
                # Extract embeddings
                batch_embeddings = [item["embedding"] for item in response.data]
                all_embeddings.extend(batch_embeddings)
                
                logger.debug(
                    "Batch embeddings generated",
                    batch_start=i,
                    embeddings_count=len(batch_embeddings),
                    embedding_dim=len(batch_embeddings[0]) if batch_embeddings else 0,
                )
            
            logger.info(
                "Embeddings generated successfully",
                total_chunks=len(chunks),
                total_embeddings=len(all_embeddings),
                embedding_dim=len(all_embeddings[0]) if all_embeddings else 0,
            )
            
            return all_embeddings
        
        except Exception as e:
            logger.error(
                "Embedding generation failed",
                error=str(e),
                chunk_count=len(chunks),
                exc_info=True,
            )
            raise
    
    async def embed_single(self, text: str) -> List[float]:
        """
        Generate embedding for single text.
        
        Args:
            text: Text to embed
        
        Returns:
            Embedding vector (1536 dimensions)
        """
        embeddings = await self.embed_chunks([text])
        return embeddings[0] if embeddings else []
    
    async def check_health(self) -> bool:
        """Check if embedding service is available."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.litellm_base_url}/health")
                return response.status_code == 200
        except Exception as e:
            logger.warning("Embedding service health check failed", error=str(e))
            return False
