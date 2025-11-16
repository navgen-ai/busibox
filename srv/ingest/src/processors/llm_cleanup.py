"""
LLM-based text cleanup processor.

Fixes text quality issues using LLM:
- Smashed words (actuallyunderstood → actually understood)
- Missing spaces between sentences
- Incorrect line breaks
- Preserves markdown formatting
"""

import re
from typing import List
import httpx
import structlog
from shared.model_registry import get_registry

logger = structlog.get_logger()


class LLMCleanup:
    """
    Clean up text chunks using LLM.
    
    Fixes spacing, line breaks, and formatting issues while preserving content.
    """
    
    CLEANUP_PROMPT = """You are a text cleanup assistant. Fix spacing, line breaks, and formatting issues in the following text.

Rules:
1. Fix smashed words by adding spaces where needed (e.g., "actuallyunderstood" → "actually understood")
2. Fix missing spaces between sentences
3. Fix incorrect line breaks
4. Preserve ALL content - don't summarize or remove anything
5. Preserve markdown formatting (# headings, *italic*, **bold**, etc.)
6. Output clean, properly formatted markdown

Text to clean:
{text}

Cleaned text:"""
    
    def __init__(self, config: dict):
        """
        Initialize LLM cleanup processor.
        
        Args:
            config: Configuration dictionary with:
                - llm_cleanup_enabled: Enable/disable cleanup (default: False)
                - litellm_base_url: LiteLLM base URL
        """
        self.config = config
        self.enabled = config.get("llm_cleanup_enabled", False)
        self.litellm_base_url = config.get("litellm_base_url", "http://litellm-lxc:4000")
        
        # Get model from registry
        try:
            registry = get_registry()
            self.model = registry.get_model("cleanup")
            self.model_config = registry.get_config("cleanup")
            logger.info(
                "LLM cleanup initialized",
                enabled=self.enabled,
                model=self.model,
                base_url=self.litellm_base_url
            )
        except Exception as e:
            logger.error("Failed to get cleanup model from registry", error=str(e))
            self.model = "qwen-2.5-32b"  # Fallback
            self.model_config = {"temperature": 0.1, "max_tokens": 32768}
            logger.warning("Using fallback cleanup model", model=self.model)
    
    async def cleanup_chunk(self, text: str) -> str:
        """
        Clean up a single chunk of text.
        
        Args:
            text: Text to clean
            
        Returns:
            Cleaned text (or original if cleanup disabled/failed)
        """
        if not self.enabled:
            return text
        
        # Skip if text looks clean (no long words)
        if not self._needs_cleanup(text):
            logger.debug("Chunk looks clean, skipping LLM cleanup")
            return text
        
        try:
            prompt = self.CLEANUP_PROMPT.format(text=text)
            
            logger.debug(
                "Cleaning chunk with LLM",
                text_length=len(text),
                model=self.model
            )
            
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.litellm_base_url}/chat/completions",
                    json={
                        "model": self.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": self.model_config.get("temperature", 0.1),
                        "max_tokens": self.model_config.get("max_tokens", 32768),
                    }
                )
                
                if response.status_code != 200:
                    logger.error(
                        "LLM cleanup failed",
                        status=response.status_code,
                        response=response.text[:200]
                    )
                    return text  # Return original on error
                
                result = response.json()
                cleaned_text = result["choices"][0]["message"]["content"]
                
                logger.info(
                    "Chunk cleaned successfully",
                    original_length=len(text),
                    cleaned_length=len(cleaned_text),
                    model=self.model
                )
                
                return cleaned_text
        
        except httpx.TimeoutException:
            logger.error("LLM cleanup timeout", timeout=60.0)
            return text
        except Exception as e:
            logger.error("LLM cleanup error", error=str(e), exc_info=True)
            return text  # Return original on error
    
    def _needs_cleanup(self, text: str) -> bool:
        """
        Check if text needs cleanup (has long words indicating smashed text).
        
        Args:
            text: Text to check
            
        Returns:
            True if text needs cleanup, False otherwise
        """
        # Find words longer than 40 characters (likely smashed)
        long_words = re.findall(r'\b\w{40,}\b', text)
        
        if long_words:
            logger.debug(
                "Text needs cleanup",
                long_word_count=len(long_words),
                examples=long_words[:3]
            )
            return True
        
        return False
    
    async def cleanup_chunks(self, chunks: List) -> List:
        """
        Clean up multiple chunks.
        
        Args:
            chunks: List of Chunk objects
            
        Returns:
            List of cleaned Chunk objects
        """
        if not self.enabled:
            logger.info("LLM cleanup disabled, skipping")
            return chunks
        
        logger.info(
            "Starting LLM cleanup for chunks",
            chunk_count=len(chunks),
            model=self.model
        )
        
        cleaned_chunks = []
        cleaned_count = 0
        
        for i, chunk in enumerate(chunks):
            cleaned_text = await self.cleanup_chunk(chunk.text)
            
            # Update chunk text if it was cleaned
            if cleaned_text != chunk.text:
                chunk.text = cleaned_text
                cleaned_count += 1
            
            cleaned_chunks.append(chunk)
            
            if (i + 1) % 10 == 0:
                logger.info(
                    "Cleanup progress",
                    processed=i + 1,
                    total=len(chunks),
                    cleaned=cleaned_count
                )
        
        logger.info(
            "LLM cleanup complete",
            total_chunks=len(chunks),
            cleaned_chunks=cleaned_count,
            skipped_chunks=len(chunks) - cleaned_count
        )
        
        return cleaned_chunks

