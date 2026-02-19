"""
Chat Agent.

A versatile chat agent with access to multiple tools for comprehensive assistance.
Uses LLM-driven tool selection to proactively help users with various tasks.

This agent extends BaseStreamingAgent with multi-tool access and LLM-driven
tool selection strategy.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from app.agents.base_agent import (
    AgentConfig,
    AgentContext,
    BaseStreamingAgent,
    ExecutionMode,
    PipelineStep,
    ToolStrategy,
)
from app.schemas.streaming import content, error
from app.services.attachment_resolver import attachment_resolver
from pydantic import BaseModel, ValidationError

from busibox_common.llm import get_client

logger = logging.getLogger(__name__)


# Chat agent system prompt - focused on behavior, tools are auto-documented by PydanticAI
CHAT_SYSTEM_PROMPT = """You are a versatile chat assistant that helps users by using available tools when appropriate.

**Key Behaviors:**

1. **Use Conversation Context**: The conversation history is provided with each message. Use it to:
   - Understand follow-up questions (e.g., "tell me more about it" refers to the previous topic)
   - Remember what was discussed earlier
   - Maintain continuity across turns

2. **Use Tools Proactively**: Don't wait for explicit tool requests:
   - Questions about current events, news, prices → search the web
   - Questions about weather → get weather
   - Questions about "my documents" or specific files → search documents
   - Requests for recurring tasks → create task

3. **Handle Ambiguous References**: When the user says "it", "that", "this topic", etc., look at the conversation history to understand what they're referring to.

4. **Cite Sources**: When using tools, include relevant sources (URLs for web, filenames for documents).

5. **Be Conversational**: Respond naturally and reference previous context when relevant.

6. **Handle Failures Gracefully**: If a tool fails or returns no results, explain and offer alternatives.

7. **Mobile-Friendly Responses**: Keep responses concise and easy to read in messaging apps:
   - Prefer short paragraphs and concise bullet lists
   - Avoid long walls of text
   - Start with the most important answer first
"""


class FastAckDecision(BaseModel):
    """Structured response from the fast-ack classifier."""

    needs_tools: bool
    response: str


class ChatAgent(BaseStreamingAgent):
    """
    A versatile streaming chat agent that:
    1. Analyzes user queries to determine appropriate tools
    2. Uses LLM-driven tool selection for flexible assistance
    3. Synthesizes results from multiple sources
    
    All steps stream their progress to the user in real-time.
    """
    
    def __init__(self):
        config = AgentConfig(
            name="chat-agent",
            display_name="Chat Agent",
            instructions=CHAT_SYSTEM_PROMPT,
            tools=[
                "web_search",
                "get_weather",
                "document_search",
                "create_task",
                "send_notification",
                "generate_image",
                "transcribe_audio",
                "text_to_speech",
            ],
            execution_mode=ExecutionMode.RUN_ONCE,
            tool_strategy=ToolStrategy.LLM_DRIVEN,  # Let LLM decide which tools to use
        )
        super().__init__(config)
    
    def pipeline_steps(self, query: str, context: AgentContext) -> List[PipelineStep]:
        """
        For LLM_DRIVEN strategy, this returns an empty list.
        The LLM will decide which tools to call.
        """
        return []
    
    def _build_synthesis_context(self, query: str, context: AgentContext) -> str:
        """
        Build context for synthesis including conversation history and tool results.
        
        Uses the base class implementation which now includes:
        1. Compressed history summary (if compression was performed)
        2. Recent conversation messages
        3. Tool results
        4. Current query
        """
        # Use base class implementation for full context with history
        base_context = super()._build_synthesis_context(query, context)
        
        # If no tools were called, add a note to respond conversationally
        if not context.tool_results:
            base_context += "\n\nNo tools were called for this query. Provide a helpful, conversational response based on the conversation context and your knowledge."
        
        return base_context
    
    def _build_fallback_response(self, query: str, context: AgentContext) -> str:
        """
        Build fallback response if synthesis fails.
        """
        if not context.tool_results:
            return "I'm here to help! What would you like to know?"
        
        parts = [f"Here's what I found:\n"]
        for tool_name, result in context.tool_results.items():
            parts.append(f"\n**{tool_name}**: {str(result)[:500]}")
        
        return "\n".join(parts)

    def _build_fast_ack_context(self, query: str, context: AgentContext) -> str:
        """Build lightweight context for a fast classification + ack pass."""
        lines: List[str] = []

        if context.compressed_history_summary:
            lines.append("Conversation summary:")
            lines.append(context.compressed_history_summary[:800])
            lines.append("")

        if context.recent_messages:
            lines.append("Recent messages:")
            for msg in context.recent_messages[-6:]:
                role = str(msg.get("role", "unknown")).strip()
                message = str(msg.get("content", "")).strip()
                if not message:
                    continue
                lines.append(f"{role}: {message[:300]}")
            lines.append("")

        if context.attachment_metadata:
            lines.append("Attachments:")
            for attachment in context.attachment_metadata:
                filename = attachment.get("filename", "attachment")
                mime_type = attachment.get("mime_type", "unknown")
                lines.append(f"- {filename} ({mime_type})")
            lines.append("")

        lines.append(f"Current user message: {query}")
        return "\n".join(lines)

    async def _generate_fast_ack(self, query: str, context: AgentContext) -> FastAckDecision:
        """
        Generate a fast first response and decide whether we need a deeper tool pass.
        """
        default = FastAckDecision(
            needs_tools=True,
            response="Got it. Let me check that for you...",
        )
        prompt = (
            "You are deciding how to handle a user message.\n"
            "Return ONLY JSON with keys: needs_tools (boolean) and response (string).\n"
            "Rules:\n"
            "- needs_tools=true when external tools or fresh system data are useful "
            "(calendar, docs, web, weather, tasking, notifications, app data).\n"
            "- needs_tools=false for greetings/chitchat/simple acknowledgements where "
            "a direct response is enough.\n"
            "- response must be concise (max 1 sentence, max 120 chars).\n"
            "- If needs_tools=true, response should acknowledge and indicate you are checking.\n"
            "- If needs_tools=false, response should be a complete direct reply.\n\n"
            f"{self._build_fast_ack_context(query, context)}"
        )
        try:
            client = get_client()
            result = await client.chat_completion(
                model="fast",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a strict JSON generator. Return only valid JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
            )
            raw = (
                result.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
            if raw.startswith("```json"):
                raw = raw[7:]
            if raw.startswith("```"):
                raw = raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            parsed = FastAckDecision.model_validate(json.loads(raw.strip()))
            if not parsed.response.strip():
                return default
            return parsed
        except (json.JSONDecodeError, ValidationError, Exception) as exc:
            logger.warning("Fast ack generation fallback triggered: %s", exc)
            return default

    async def run_with_streaming(
        self,
        query: str,
        stream,
        cancel,
        context: Optional[dict] = None,
    ) -> str:
        """
        Two-phase chat flow:
        1) Fast first response (ack or direct conversational reply)
        2) Optional deeper tool-enabled response
        """
        agent_context = await self._setup_context(context, stream, query)
        if agent_context is None:
            return "Authentication or session error. Please sign in and try again."
        if cancel.is_set():
            return ""

        decision = await self._generate_fast_ack(query, agent_context)
        fast_response = decision.response.strip()
        if fast_response:
            await stream(content(
                source=self.name,
                message=fast_response,
                data={"phase": "fast_ack", "partial": False},
            ))

        if cancel.is_set():
            return ""

        # For simple conversational messages, the fast response is final.
        if not decision.needs_tools:
            return fast_response

        # Resolve uploaded attachments between fast ack and deeper tool pass.
        if agent_context.attachment_metadata:
            context_token_estimate = 0
            if agent_context.compressed_history_summary:
                context_token_estimate += len(agent_context.compressed_history_summary) // 4
            if agent_context.recent_messages:
                context_token_estimate += sum(
                    len(str(m.get("content", ""))) // 4 for m in agent_context.recent_messages
                )
            if agent_context.relevant_insights:
                context_token_estimate += sum(
                    len(str(i.get("content", ""))) // 4 for i in agent_context.relevant_insights
                )

            agent_context.resolved_attachments = await attachment_resolver.resolve(
                query=query,
                attachment_metadata=agent_context.attachment_metadata,
                principal=agent_context.principal,
                user_id=agent_context.user_id,
                stream=stream,
                context_token_estimate=context_token_estimate,
            )

        try:
            if self.config.tool_strategy == ToolStrategy.LLM_DRIVEN:
                await self._execute_llm_driven(query, stream, cancel, agent_context)
            else:
                await self._execute_pipeline(query, stream, cancel, agent_context)
        except Exception as exc:
            logger.error("Chat agent execution error: %s", exc, exc_info=True)
            await stream(error(
                source=self.name,
                message=f"Error during execution: {str(exc)}",
            ))
            return f"{fast_response}\n\nI encountered an error while checking that." if fast_response else "I encountered an error while checking that."

        if cancel.is_set():
            return ""

        # Return the deeper response. The fast ack is already streamed.
        deep_response = await self._synthesize(query, stream, cancel, agent_context)
        if fast_response:
            return f"{fast_response}\n\n{deep_response}".strip()
        return deep_response


# Singleton instance
chat_agent = ChatAgent()
