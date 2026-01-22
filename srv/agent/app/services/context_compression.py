"""
Context Compression Service.

Implements conversation history compression for long threads.
Uses a hybrid approach:
1. Keep recent messages in full (last N turns)
2. Compress older messages into a summary
3. Support hierarchical compression for very long threads

Best practices from research:
- Sliding window: Keep most recent 5-10 turns verbatim
- Periodic summarization: Compress older turns when threshold exceeded
- Tag critical content: Preserve key entities, decisions, constraints
- Validate summaries: Ensure key information is retained
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel

from app.config.settings import get_settings
from app.schemas.definitions import ContextCompressionConfig

logger = logging.getLogger(__name__)

# Default compression configuration
DEFAULT_COMPRESSION_CONFIG = ContextCompressionConfig()

# System prompt for the compression model
COMPRESSION_SYSTEM_PROMPT = """You are a conversation summarizer. Your task is to compress a conversation history into a concise summary that preserves all important information.

CRITICAL: Your summary must retain:
1. Key facts and information discussed
2. User preferences and constraints mentioned
3. Decisions made and their reasoning
4. Important context that would affect future responses
5. Any specific instructions or requirements

Format your summary as a structured list:
- **Context**: Brief description of what the conversation is about
- **Key Facts**: Important information established
- **User Preferences**: Any preferences or constraints mentioned
- **Decisions Made**: Key decisions or conclusions reached
- **Current State**: Where the conversation left off

Be concise but comprehensive. Do not lose important details."""


@dataclass
class CompressionResult:
    """Result of conversation history compression."""
    # The compressed summary (if compression was performed)
    summary: Optional[str] = None
    
    # Recent messages kept in full
    recent_messages: List[Dict[str, str]] = field(default_factory=list)
    
    # Whether compression was actually performed
    was_compressed: bool = False
    
    # Statistics
    original_char_count: int = 0
    compressed_char_count: int = 0
    messages_compressed: int = 0
    messages_kept: int = 0


class ContextCompressionService:
    """
    Service for compressing conversation history to fit context windows.
    
    Uses a hybrid approach:
    1. Always keep the last N message pairs in full
    2. When history exceeds threshold, compress older messages into a summary
    3. Optionally compress in parallel for faster response
    """
    
    def __init__(self, config: Optional[ContextCompressionConfig] = None):
        self.config = config or DEFAULT_COMPRESSION_CONFIG
        self._compression_agent: Optional[Agent] = None
    
    def _get_compression_agent(self) -> Agent:
        """Get or create the compression agent (lazy initialization)."""
        if self._compression_agent is None:
            from busibox_common.llm import ensure_openai_env
            settings = get_settings()
            ensure_openai_env(
                base_url=str(settings.litellm_base_url),
                api_key=settings.litellm_api_key,
            )
            
            model_name = self.config.compression_model or "fast"
            model = OpenAIModel(model_name=model_name, provider="openai")
            
            self._compression_agent = Agent(
                model=model,
                system_prompt=COMPRESSION_SYSTEM_PROMPT,
                model_settings={"max_tokens": 1000},  # Keep summaries concise
            )
        
        return self._compression_agent
    
    def _count_chars(self, messages: List[Dict[str, str]]) -> int:
        """Count total characters in messages."""
        return sum(len(msg.get("content", "")) for msg in messages)
    
    def _split_messages(
        self, 
        messages: List[Dict[str, str]], 
        keep_recent: int
    ) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
        """
        Split messages into older (to compress) and recent (to keep).
        
        Args:
            messages: All conversation messages
            keep_recent: Number of recent message pairs to keep
            
        Returns:
            Tuple of (messages_to_compress, messages_to_keep)
        """
        if len(messages) <= keep_recent * 2:
            # Not enough messages to compress
            return [], messages
        
        # Keep the last N*2 messages (N pairs of user/assistant)
        split_point = len(messages) - (keep_recent * 2)
        
        return messages[:split_point], messages[split_point:]
    
    async def compress_history(
        self,
        messages: List[Dict[str, str]],
        config: Optional[ContextCompressionConfig] = None,
    ) -> CompressionResult:
        """
        Compress conversation history if it exceeds the threshold.
        
        Args:
            messages: List of message dicts with 'role' and 'content' keys
            config: Optional override for compression config
            
        Returns:
            CompressionResult with summary and recent messages
        """
        cfg = config or self.config
        
        if not cfg.enabled:
            return CompressionResult(
                recent_messages=messages,
                was_compressed=False,
                original_char_count=self._count_chars(messages),
                compressed_char_count=self._count_chars(messages),
                messages_kept=len(messages),
            )
        
        original_chars = self._count_chars(messages)
        
        # Check if compression is needed
        if original_chars <= cfg.compression_threshold_chars:
            return CompressionResult(
                recent_messages=messages,
                was_compressed=False,
                original_char_count=original_chars,
                compressed_char_count=original_chars,
                messages_kept=len(messages),
            )
        
        # Split messages
        to_compress, to_keep = self._split_messages(
            messages, 
            cfg.recent_messages_to_keep
        )
        
        if not to_compress:
            # Nothing to compress
            return CompressionResult(
                recent_messages=messages,
                was_compressed=False,
                original_char_count=original_chars,
                compressed_char_count=original_chars,
                messages_kept=len(messages),
            )
        
        logger.info(
            f"Compressing {len(to_compress)} messages, keeping {len(to_keep)} recent",
            extra={
                "original_chars": original_chars,
                "messages_to_compress": len(to_compress),
                "messages_to_keep": len(to_keep),
            }
        )
        
        try:
            # Build the conversation text to summarize
            conversation_text = self._format_for_compression(to_compress)
            
            # Get compression agent and run
            agent = self._get_compression_agent()
            result = await agent.run(
                f"Please summarize this conversation history:\n\n{conversation_text}"
            )
            
            summary = str(result.output) if hasattr(result, 'output') else str(result)
            
            # Truncate if needed
            if len(summary) > cfg.max_summary_chars:
                summary = summary[:cfg.max_summary_chars] + "..."
            
            compressed_chars = len(summary) + self._count_chars(to_keep)
            
            logger.info(
                f"Compression complete: {original_chars} -> {compressed_chars} chars "
                f"({(1 - compressed_chars/original_chars)*100:.1f}% reduction)",
                extra={
                    "original_chars": original_chars,
                    "compressed_chars": compressed_chars,
                    "summary_length": len(summary),
                }
            )
            
            return CompressionResult(
                summary=summary,
                recent_messages=to_keep,
                was_compressed=True,
                original_char_count=original_chars,
                compressed_char_count=compressed_chars,
                messages_compressed=len(to_compress),
                messages_kept=len(to_keep),
            )
            
        except Exception as e:
            logger.error(f"Compression failed, falling back to truncation: {e}", exc_info=True)
            
            # Fallback: Just keep recent messages without summary
            return CompressionResult(
                recent_messages=to_keep,
                was_compressed=False,
                original_char_count=original_chars,
                compressed_char_count=self._count_chars(to_keep),
                messages_compressed=0,
                messages_kept=len(to_keep),
            )
    
    def _format_for_compression(self, messages: List[Dict[str, str]]) -> str:
        """Format messages for the compression prompt."""
        parts = []
        for msg in messages:
            role = msg.get("role", "unknown").upper()
            content = msg.get("content", "")
            parts.append(f"{role}: {content}")
        return "\n\n".join(parts)
    
    async def compress_in_background(
        self,
        messages: List[Dict[str, str]],
        config: Optional[ContextCompressionConfig] = None,
    ) -> asyncio.Task[CompressionResult]:
        """
        Start compression in background, returning a task.
        
        Useful for parallel execution where you want to start compression
        while doing other work.
        
        Args:
            messages: Messages to compress
            config: Optional config override
            
        Returns:
            asyncio.Task that will resolve to CompressionResult
        """
        return asyncio.create_task(
            self.compress_history(messages, config)
        )


def build_context_with_history(
    query: str,
    compression_result: CompressionResult,
    system_context: Optional[str] = None,
) -> str:
    """
    Build a complete context string including compressed history.
    
    Args:
        query: Current user query
        compression_result: Result from compression
        system_context: Optional additional system context
        
    Returns:
        Formatted context string for the LLM
    """
    parts = []
    
    # Add compressed summary if present
    if compression_result.summary:
        parts.append("## Previous Conversation Summary")
        parts.append(compression_result.summary)
        parts.append("")
    
    # Add recent messages
    if compression_result.recent_messages:
        parts.append("## Recent Conversation")
        for msg in compression_result.recent_messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if role == "user":
                parts.append(f"**User**: {content}")
            elif role == "assistant":
                parts.append(f"**Assistant**: {content}")
            else:
                parts.append(f"**{role}**: {content}")
        parts.append("")
    
    # Add system context if provided
    if system_context:
        parts.append("## Additional Context")
        parts.append(system_context)
        parts.append("")
    
    # Add current query
    parts.append("## Current Query")
    parts.append(query)
    
    return "\n".join(parts)


# Singleton instance for default compression
_default_service: Optional[ContextCompressionService] = None


def get_compression_service(
    config: Optional[ContextCompressionConfig] = None
) -> ContextCompressionService:
    """Get the context compression service singleton."""
    global _default_service
    
    if config is not None:
        # Return a new service with custom config
        return ContextCompressionService(config)
    
    if _default_service is None:
        _default_service = ContextCompressionService()
    
    return _default_service
