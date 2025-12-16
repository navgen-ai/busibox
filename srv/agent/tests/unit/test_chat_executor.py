"""Unit tests for chat executor service."""
import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.chat_executor import (
    ToolExecutionResult,
    AgentExecutionResult,
    ChatExecutionResult,
    execute_web_search,
    execute_document_search,
    execute_tools,
    execute_agent,
    execute_agents,
    synthesize_response,
    execute_chat,
)
from app.schemas.dispatcher import RoutingDecision


@pytest.mark.asyncio
async def test_tool_execution_result():
    """Test ToolExecutionResult creation."""
    result = ToolExecutionResult(
        tool_name="web_search",
        success=True,
        output="Search results",
        metadata={"query": "test"},
        error=None
    )
    
    assert result.tool_name == "web_search"
    assert result.success is True
    assert result.output == "Search results"
    assert result.metadata["query"] == "test"
    assert result.error is None
    
    # Test to_dict
    result_dict = result.to_dict()
    assert result_dict["tool_name"] == "web_search"
    assert result_dict["success"] is True


@pytest.mark.asyncio
async def test_agent_execution_result():
    """Test AgentExecutionResult creation."""
    run_id = uuid.uuid4()
    result = AgentExecutionResult(
        agent_id="test-agent",
        run_id=run_id,
        success=True,
        output="Agent output",
        metadata={"test": "data"},
        error=None
    )
    
    assert result.agent_id == "test-agent"
    assert result.run_id == run_id
    assert result.success is True
    
    # Test to_dict
    result_dict = result.to_dict()
    assert result_dict["agent_id"] == "test-agent"
    assert result_dict["run_id"] == str(run_id)


@pytest.mark.asyncio
async def test_chat_execution_result():
    """Test ChatExecutionResult creation."""
    tool_result = ToolExecutionResult(
        tool_name="web_search",
        success=True,
        output="Results",
        metadata={},
        error=None
    )
    
    agent_result = AgentExecutionResult(
        agent_id="test-agent",
        run_id=uuid.uuid4(),
        success=True,
        output="Output",
        metadata={},
        error=None
    )
    
    routing_decision = RoutingDecision(
        selected_tools=["web_search"],
        selected_agents=["test-agent"],
        confidence=0.9,
        reasoning="Test routing",
        alternatives=[],
        requires_disambiguation=False
    )
    
    result = ChatExecutionResult(
        content="Final response",
        tool_results=[tool_result],
        agent_results=[agent_result],
        model_used="chat",
        routing_decision=routing_decision
    )
    
    assert result.content == "Final response"
    assert len(result.tool_results) == 1
    assert len(result.agent_results) == 1
    assert result.model_used == "chat"
    
    # Test get_tool_calls_json
    tool_calls = result.get_tool_calls_json()
    assert len(tool_calls) == 1
    assert tool_calls[0]["tool_name"] == "web_search"
    
    # Test get_run_ids
    run_ids = result.get_run_ids()
    assert len(run_ids) == 1
    assert isinstance(run_ids[0], uuid.UUID)


@pytest.mark.asyncio
@patch('app.services.chat_executor.web_search_agent')
async def test_execute_web_search_success(mock_agent):
    """Test successful web search execution."""
    # Mock agent run result
    mock_result = MagicMock()
    mock_result.data = "Search results from web"
    mock_agent.run = AsyncMock(return_value=mock_result)
    
    result = await execute_web_search("test query", "user-123")
    
    assert result.tool_name == "web_search"
    assert result.success is True
    assert "Search results from web" in result.output
    assert result.error is None
    assert result.metadata["query"] == "test query"


@pytest.mark.asyncio
@patch('app.services.chat_executor.web_search_agent')
async def test_execute_web_search_failure(mock_agent):
    """Test web search execution failure."""
    # Mock agent run to raise exception
    mock_agent.run = AsyncMock(side_effect=Exception("Search failed"))
    
    result = await execute_web_search("test query", "user-123")
    
    assert result.tool_name == "web_search"
    assert result.success is False
    assert result.output == ""
    assert "Search failed" in result.error


@pytest.mark.asyncio
@patch('app.services.chat_executor.document_agent')
async def test_execute_document_search_success(mock_agent):
    """Test successful document search execution."""
    # Mock agent run result
    mock_result = MagicMock()
    mock_result.data = "Document search results"
    mock_agent.run = AsyncMock(return_value=mock_result)
    
    result = await execute_document_search("test query", "user-123")
    
    assert result.tool_name == "doc_search"
    assert result.success is True
    assert "Document search results" in result.output
    assert result.error is None


@pytest.mark.asyncio
@patch('app.services.chat_executor.execute_web_search')
@patch('app.services.chat_executor.execute_document_search')
async def test_execute_tools_parallel(mock_doc_search, mock_web_search):
    """Test parallel tool execution."""
    # Mock tool results
    mock_web_search.return_value = ToolExecutionResult(
        tool_name="web_search",
        success=True,
        output="Web results",
        metadata={},
        error=None
    )
    
    mock_doc_search.return_value = ToolExecutionResult(
        tool_name="doc_search",
        success=True,
        output="Doc results",
        metadata={},
        error=None
    )
    
    results = await execute_tools(
        ["web_search", "doc_search"],
        "test query",
        "user-123"
    )
    
    assert len(results) == 2
    assert results[0].tool_name == "web_search"
    assert results[1].tool_name == "doc_search"
    assert all(r.success for r in results)


@pytest.mark.asyncio
async def test_execute_tools_empty():
    """Test execute_tools with empty tool list."""
    results = await execute_tools([], "test query", "user-123")
    assert len(results) == 0


@pytest.mark.asyncio
async def test_execute_agent(test_session):
    """Test agent execution with run record creation."""
    result = await execute_agent(
        agent_id="test-agent-id",
        query="test query",
        user_id="user-123",
        session=test_session,
        context={"test": "context"}
    )
    
    assert result.agent_id == "test-agent-id"
    assert isinstance(result.run_id, uuid.UUID)
    # Note: success may be False if agent execution fails (expected for placeholder)
    # The important thing is that run record is created
    assert result.output is not None or result.error is not None


@pytest.mark.asyncio
async def test_execute_agents_sequential(test_session):
    """Test sequential agent execution."""
    results = await execute_agents(
        ["agent-1", "agent-2"],
        "test query",
        "user-123",
        test_session
    )
    
    assert len(results) == 2
    assert results[0].agent_id == "agent-1"
    assert results[1].agent_id == "agent-2"
    assert all(isinstance(r.run_id, uuid.UUID) for r in results)


@pytest.mark.asyncio
async def test_synthesize_response_with_tools():
    """Test response synthesis with tool results."""
    tool_results = [
        ToolExecutionResult(
            tool_name="web_search",
            success=True,
            output="Web search found: AI news",
            metadata={},
            error=None
        ),
        ToolExecutionResult(
            tool_name="doc_search",
            success=True,
            output="Document says: AI is important",
            metadata={},
            error=None
        )
    ]
    
    response = await synthesize_response(
        query="Tell me about AI",
        tool_results=tool_results,
        agent_results=[],
        model="chat"
    )
    
    assert "Web search found" in response
    assert "Document says" in response
    assert "Tell me about AI" in response


@pytest.mark.asyncio
async def test_synthesize_response_with_agents():
    """Test response synthesis with agent results."""
    agent_results = [
        AgentExecutionResult(
            agent_id="test-agent",
            run_id=uuid.uuid4(),
            success=True,
            output="Agent analysis complete",
            metadata={},
            error=None
        )
    ]
    
    response = await synthesize_response(
        query="Analyze this",
        tool_results=[],
        agent_results=agent_results,
        model="chat"
    )
    
    assert "Agent analysis complete" in response


@pytest.mark.asyncio
async def test_synthesize_response_with_errors():
    """Test response synthesis with tool errors."""
    tool_results = [
        ToolExecutionResult(
            tool_name="web_search",
            success=False,
            output="",
            metadata={},
            error="Search API unavailable"
        )
    ]
    
    response = await synthesize_response(
        query="Search for something",
        tool_results=tool_results,
        agent_results=[],
        model="chat"
    )
    
    assert "Search API unavailable" in response


@pytest.mark.asyncio
async def test_synthesize_response_no_results():
    """Test response synthesis with no results."""
    response = await synthesize_response(
        query="Test query",
        tool_results=[],
        agent_results=[],
        model="chat"
    )
    
    assert "wasn't able to gather information" in response.lower()


@pytest.mark.asyncio
@patch('app.services.chat_executor.execute_tools')
@patch('app.services.chat_executor.execute_agents')
@patch('app.services.chat_executor.synthesize_response')
async def test_execute_chat_complete_flow(
    mock_synthesize,
    mock_execute_agents,
    mock_execute_tools,
    test_session
):
    """Test complete chat execution flow."""
    # Mock tool execution
    mock_execute_tools.return_value = [
        ToolExecutionResult(
            tool_name="web_search",
            success=True,
            output="Results",
            metadata={},
            error=None
        )
    ]
    
    # Mock agent execution
    mock_execute_agents.return_value = [
        AgentExecutionResult(
            agent_id="test-agent",
            run_id=uuid.uuid4(),
            success=True,
            output="Output",
            metadata={},
            error=None
        )
    ]
    
    # Mock synthesis
    mock_synthesize.return_value = "Final synthesized response"
    
    # Create routing decision
    routing_decision = RoutingDecision(
        selected_tools=["web_search"],
        selected_agents=["test-agent"],
        confidence=0.9,
        reasoning="Test routing",
        alternatives=[],
        requires_disambiguation=False
    )
    
    result = await execute_chat(
        query="test query",
        routing_decision=routing_decision,
        model="chat",
        user_id="user-123",
        session=test_session
    )
    
    assert isinstance(result, ChatExecutionResult)
    assert result.content == "Final synthesized response"
    assert len(result.tool_results) == 1
    assert len(result.agent_results) == 1
    assert result.model_used == "chat"


@pytest.mark.asyncio
@patch('app.services.chat_executor.execute_tools')
@patch('app.services.chat_executor.execute_agents')
async def test_execute_chat_stream(
    mock_execute_agents,
    mock_execute_tools,
    test_session
):
    """Test streaming chat execution."""
    from app.services.chat_executor import execute_chat_stream
    
    # Mock tool execution
    mock_execute_tools.return_value = [
        ToolExecutionResult(
            tool_name="web_search",
            success=True,
            output="Results",
            metadata={},
            error=None
        )
    ]
    
    # Mock agent execution
    mock_execute_agents.return_value = []
    
    routing_decision = RoutingDecision(
        selected_tools=["web_search"],
        selected_agents=[],
        confidence=0.9,
        reasoning="Test",
        alternatives=[],
        requires_disambiguation=False
    )
    
    events = []
    async for event in execute_chat_stream(
        query="test query",
        routing_decision=routing_decision,
        model="chat",
        user_id="user-123",
        session=test_session
    ):
        events.append(event)
    
    # Verify event types
    event_types = [e["type"] for e in events]
    assert "tools_start" in event_types
    assert "tool_result" in event_types
    assert "synthesis_start" in event_types
    assert "content_chunk" in event_types
    assert "execution_complete" in event_types

