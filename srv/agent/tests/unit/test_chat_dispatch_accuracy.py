"""Reference-query checks for dispatch/action classification."""

import asyncio
import json

import pytest

from app.agents.chat_agent import (
    AgentContext,
    ChatAgent,
    ExecutionPlan,
    FastAckDecision,
    PlanStep,
)


@pytest.mark.parametrize(
    "query,expected_action,expected_tools",
    [
        ("hello there", "direct", False),
        ("what's the weather in boston?", "search", True),
        ("search my documents for roadmap", "search", True),
        ("latest news on nvidia earnings", "research", True),
        ("calendar for today", "multi_step", True),
        ("help", "clarify", False),
    ],
)
def test_dispatch_heuristics_reference_queries(query, expected_action, expected_tools):
    """
    Ensure heuristic fallback stays aligned with intended action taxonomy.
    """
    agent = ChatAgent()
    decision = agent._heuristic_fast_ack(query)
    assert decision.action_type == expected_action
    assert decision.needs_tools == expected_tools


# ============================================================================
# Fast-ack prompt: tool awareness and attachment handling
# ============================================================================


class _CapturingClient:
    """Fake LLM client that captures the prompt and returns a valid FastAckDecision."""

    def __init__(self):
        self.captured_prompt: str = ""

    async def chat_completion(self, **kwargs):
        messages = kwargs.get("messages", [])
        self.captured_prompt = messages[-1]["content"] if messages else ""
        return {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "action_type": "search",
                        "needs_tools": True,
                        "response": "Let me check that for you.",
                        "follow_up_question": None,
                        "confidence": 0.9,
                        "complexity": "moderate",
                    })
                }
            }]
        }


@pytest.mark.asyncio
async def test_fast_ack_prompt_lists_available_tools(monkeypatch):
    """The fast-ack prompt should tell the LLM which tools it has access to."""
    agent = ChatAgent()
    context = AgentContext()
    fake = _CapturingClient()
    monkeypatch.setattr("app.agents.chat_agent.get_client", lambda: fake)
    monkeypatch.setattr("app.agents.chat_agent.ToolRegistry.has", lambda name: True)

    await agent._generate_fast_ack("search my docs for invoices", context)

    assert "Available tools:" in fake.captured_prompt
    assert "document_search" in fake.captured_prompt
    assert "web_search" in fake.captured_prompt


@pytest.mark.asyncio
async def test_fast_ack_prompt_forbids_lack_of_tools_language(monkeypatch):
    """The prompt should explicitly tell the model to never claim lack of tools."""
    agent = ChatAgent()
    context = AgentContext()
    fake = _CapturingClient()
    monkeypatch.setattr("app.agents.chat_agent.get_client", lambda: fake)
    monkeypatch.setattr("app.agents.chat_agent.ToolRegistry.has", lambda name: True)

    await agent._generate_fast_ack("summarize this document", context)

    assert "NEVER say you lack tools" in fake.captured_prompt
    assert "You DO have access to tools" in fake.captured_prompt


@pytest.mark.asyncio
async def test_fast_ack_prompt_includes_attachment_guidance(monkeypatch):
    """When attachments are present, prompt should tell the model about them."""
    agent = ChatAgent()
    context = AgentContext(
        attachment_metadata=[
            {"filename": "report.pdf", "mime_type": "application/pdf"},
        ]
    )
    fake = _CapturingClient()
    monkeypatch.setattr("app.agents.chat_agent.get_client", lambda: fake)
    monkeypatch.setattr("app.agents.chat_agent.ToolRegistry.has", lambda name: True)

    await agent._generate_fast_ack("what is this document about?", context)

    assert "uploaded attachments" in fake.captured_prompt
    assert "Do NOT say you can't access the file" in fake.captured_prompt


@pytest.mark.asyncio
async def test_fast_ack_prompt_omits_attachment_guidance_when_none(monkeypatch):
    """Without attachments, the attachment-specific guidance should not appear."""
    agent = ChatAgent()
    context = AgentContext()
    fake = _CapturingClient()
    monkeypatch.setattr("app.agents.chat_agent.get_client", lambda: fake)
    monkeypatch.setattr("app.agents.chat_agent.ToolRegistry.has", lambda name: True)

    await agent._generate_fast_ack("hello", context)

    assert "uploaded attachments" not in fake.captured_prompt


# ============================================================================
# Planner: attachment-aware doc search skipping
# ============================================================================


@pytest.mark.asyncio
async def test_plan_fallback_skips_doc_search_for_attachment_query(monkeypatch):
    """When the user asks about an attachment, the fallback plan should skip document_search."""
    agent = ChatAgent()
    context = AgentContext(
        attachment_metadata=[
            {"filename": "invoice.pdf", "mime_type": "application/pdf"},
        ]
    )

    class FailingClient:
        async def chat_completion(self, **kwargs):
            raise RuntimeError("planner unavailable")

    monkeypatch.setattr("app.agents.chat_agent.get_client", lambda: FailingClient())
    monkeypatch.setattr(
        "app.agents.chat_agent.ToolRegistry.has",
        lambda name: name in {"document_search", "web_search"},
    )
    monkeypatch.setattr(
        "app.agents.chat_agent.ToolRegistry.get",
        lambda name: (lambda query, limit=5: None) if name == "document_search" else (lambda **kw: None),
    )

    plan = await agent._generate_plan(
        query="summarize this document for me",
        context=context,
        dispatch=FastAckDecision(action_type="search", needs_tools=True, response="Reviewing."),
    )

    tool_names = [s.tool for s in plan.steps]
    assert "document_search" not in tool_names


@pytest.mark.asyncio
async def test_plan_fallback_keeps_doc_search_for_cross_reference_query(monkeypatch):
    """Even with attachments, doc search should run when user asks to compare with other docs."""
    agent = ChatAgent()
    context = AgentContext(
        attachment_metadata=[
            {"filename": "contract.pdf", "mime_type": "application/pdf"},
        ]
    )

    class FailingClient:
        async def chat_completion(self, **kwargs):
            raise RuntimeError("planner unavailable")

    monkeypatch.setattr("app.agents.chat_agent.get_client", lambda: FailingClient())
    monkeypatch.setattr(
        "app.agents.chat_agent.ToolRegistry.has",
        lambda name: name in {"document_search", "web_search"},
    )
    monkeypatch.setattr(
        "app.agents.chat_agent.ToolRegistry.get",
        lambda name: (lambda query, limit=5: None) if name == "document_search" else (lambda **kw: None),
    )

    plan = await agent._generate_plan(
        query="compare with my other documents and find similar contracts",
        context=context,
        dispatch=FastAckDecision(action_type="search", needs_tools=True, response="Checking."),
    )

    tool_names = [s.tool for s in plan.steps]
    assert "document_search" in tool_names


@pytest.mark.asyncio
async def test_plan_llm_prompt_includes_attachment_skip_guidance(monkeypatch):
    """When attachments are present, the planner prompt should tell LLM to skip doc search."""
    agent = ChatAgent()
    context = AgentContext(
        attachment_metadata=[
            {"filename": "report.pdf", "mime_type": "application/pdf"},
        ]
    )

    captured_prompts = []

    class CapturingPlanClient:
        async def chat_completion(self, **kwargs):
            messages = kwargs.get("messages", [])
            captured_prompts.append(messages[-1]["content"] if messages else "")
            return {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "summary": "Review attachment",
                            "steps": [],
                            "parallel_groups": [],
                            "feedback_points": [],
                            "estimated_duration": "quick",
                        })
                    }
                }]
            }

    monkeypatch.setattr("app.agents.chat_agent.get_client", lambda: CapturingPlanClient())
    monkeypatch.setattr(
        "app.agents.chat_agent.ToolRegistry.has",
        lambda name: name in {"document_search", "web_search"},
    )
    monkeypatch.setattr(
        "app.agents.chat_agent.ToolRegistry.get",
        lambda name: (lambda query, limit=5: None) if name == "document_search" else (lambda **kw: None),
    )

    await agent._generate_plan(
        query="what does this attachment say?",
        context=context,
        dispatch=FastAckDecision(action_type="search", needs_tools=True, response="Reviewing."),
    )

    assert captured_prompts
    prompt_text = captured_prompts[0]
    assert "report.pdf" in prompt_text
    assert "do NOT need `document_search`" in prompt_text


@pytest.mark.asyncio
async def test_plan_llm_prompt_requires_doc_search_without_attachments(monkeypatch):
    """Without attachments, the planner prompt should require document_search."""
    agent = ChatAgent()
    context = AgentContext()

    captured_prompts = []

    class CapturingPlanClient:
        async def chat_completion(self, **kwargs):
            messages = kwargs.get("messages", [])
            captured_prompts.append(messages[-1]["content"] if messages else "")
            return {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "summary": "Search docs",
                            "steps": [{"id": "s1", "tool": "document_search", "objective": "search", "args": {"query": "test"}}],
                            "parallel_groups": [],
                            "feedback_points": [],
                            "estimated_duration": "quick",
                        })
                    }
                }]
            }

    monkeypatch.setattr("app.agents.chat_agent.get_client", lambda: CapturingPlanClient())
    monkeypatch.setattr(
        "app.agents.chat_agent.ToolRegistry.has",
        lambda name: name in {"document_search", "web_search"},
    )
    monkeypatch.setattr(
        "app.agents.chat_agent.ToolRegistry.get",
        lambda name: (lambda query, limit=5: None) if name == "document_search" else (lambda **kw: None),
    )

    await agent._generate_plan(
        query="find project plans in my docs",
        context=context,
        dispatch=FastAckDecision(action_type="search", needs_tools=True, response="Checking."),
    )

    assert captured_prompts
    prompt_text = captured_prompts[0]
    assert "ALWAYS include `document_search`" in prompt_text

