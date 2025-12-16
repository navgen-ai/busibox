"""Unit tests for model selector service."""
import pytest

from app.services.model_selector import (
    has_image_attachments,
    detect_web_search_intent,
    detect_doc_search_intent,
    needs_complex_reasoning,
    select_model_and_tools,
    get_model_capabilities,
    list_available_models,
    AVAILABLE_MODELS,
)


def test_has_image_attachments_with_images():
    """Test image detection in attachments."""
    attachments = [
        {"name": "photo.jpg", "type": "image/jpeg", "url": "http://example.com/photo.jpg"},
        {"name": "doc.pdf", "type": "application/pdf", "url": "http://example.com/doc.pdf"},
    ]
    
    assert has_image_attachments(attachments) is True


def test_has_image_attachments_without_images():
    """Test no images in attachments."""
    attachments = [
        {"name": "doc.pdf", "type": "application/pdf", "url": "http://example.com/doc.pdf"},
        {"name": "data.csv", "type": "text/csv", "url": "http://example.com/data.csv"},
    ]
    
    assert has_image_attachments(attachments) is False


def test_has_image_attachments_empty():
    """Test empty attachments list."""
    assert has_image_attachments([]) is False
    assert has_image_attachments(None) is False


def test_detect_web_search_intent_current_events():
    """Test detection of current events queries."""
    assert detect_web_search_intent("What's the latest news about AI?") is True
    assert detect_web_search_intent("Show me recent updates on climate change") is True
    assert detect_web_search_intent("What happened today in the stock market?") is True


def test_detect_web_search_intent_search_phrases():
    """Test detection of search intent phrases."""
    assert detect_web_search_intent("Search for information about Python") is True
    assert detect_web_search_intent("Find out who won the election") is True
    assert detect_web_search_intent("Look up the weather forecast") is True


def test_detect_web_search_intent_urls():
    """Test detection of URL mentions."""
    assert detect_web_search_intent("Check https://example.com for details") is True
    assert detect_web_search_intent("Visit www.python.org") is True
    assert detect_web_search_intent("Go to github.com") is True


def test_detect_web_search_intent_no_match():
    """Test queries that don't need web search."""
    assert detect_web_search_intent("Explain machine learning") is False
    assert detect_web_search_intent("Help me write a function") is False


def test_detect_doc_search_intent_document_keywords():
    """Test detection of document-related queries."""
    assert detect_doc_search_intent("What does the Q4 report say?", []) is True
    assert detect_doc_search_intent("Search my documents for revenue", []) is True
    assert detect_doc_search_intent("Find information in the uploaded file", []) is True


def test_detect_doc_search_intent_from_history():
    """Test detection based on conversation history."""
    history = [
        {"role": "user", "content": "I uploaded a document about sales"},
        {"role": "assistant", "content": "I see your document"},
    ]
    
    assert detect_doc_search_intent("What does it say about Q3?", history) is True


def test_detect_doc_search_intent_no_match():
    """Test queries that don't need document search."""
    assert detect_doc_search_intent("What's the weather?", []) is False
    assert detect_doc_search_intent("Tell me a joke", []) is False


def test_needs_complex_reasoning_analysis():
    """Test detection of analysis requests."""
    assert needs_complex_reasoning("Analyze the pros and cons of this approach") is True
    assert needs_complex_reasoning("Compare these two options") is True
    assert needs_complex_reasoning("Evaluate the trade-offs") is True


def test_needs_complex_reasoning_detailed():
    """Test detection of detailed explanation requests."""
    assert needs_complex_reasoning("Explain why this happens in detail") is True
    assert needs_complex_reasoning("Break down the steps") is True
    assert needs_complex_reasoning("How does this work exactly?") is True


def test_needs_complex_reasoning_simple():
    """Test simple queries don't trigger complex reasoning."""
    assert needs_complex_reasoning("What's the weather?") is False
    assert needs_complex_reasoning("Hello") is False


def test_select_model_vision_required():
    """Test model selection when vision is required."""
    attachments = [{"type": "image/jpeg", "name": "photo.jpg", "url": "http://example.com/photo.jpg"}]
    
    result = select_model_and_tools(
        message="What's in this image?",
        attachments=attachments,
        history=[],
        user_model_preference=None,
        enabled_tools=["web_search"]
    )
    
    assert result.model_id == "frontier"
    assert "vision" in result.capabilities_used
    assert result.confidence >= 0.9


def test_select_model_tools_and_reasoning():
    """Test model selection for complex task with tools."""
    result = select_model_and_tools(
        message="Analyze the latest AI research papers and compare their approaches",
        attachments=[],
        history=[],
        user_model_preference=None,
        enabled_tools=["web_search", "doc_search"]
    )
    
    assert result.model_id == "research"
    assert "tools" in result.capabilities_used or "reasoning" in result.capabilities_used
    assert result.confidence >= 0.8


def test_select_model_tools_only():
    """Test model selection when tools are needed but not complex reasoning."""
    result = select_model_and_tools(
        message="What's the latest news?",
        attachments=[],
        history=[],
        user_model_preference=None,
        enabled_tools=["web_search"]
    )
    
    assert result.model_id == "research"
    assert "tools" in result.capabilities_used
    assert result.confidence >= 0.8


def test_select_model_reasoning_only():
    """Test model selection for complex reasoning without tools."""
    result = select_model_and_tools(
        message="Analyze the implications of quantum computing on cryptography",
        attachments=[],
        history=[],
        user_model_preference=None,
        enabled_tools=[]
    )
    
    assert result.model_id == "research"
    assert "reasoning" in result.capabilities_used
    assert result.confidence >= 0.7


def test_select_model_simple_chat():
    """Test model selection for simple conversation."""
    result = select_model_and_tools(
        message="Hello, how are you?",
        attachments=[],
        history=[],
        user_model_preference=None,
        enabled_tools=[]
    )
    
    assert result.model_id == "chat"
    assert result.confidence >= 0.8


def test_select_model_user_preference():
    """Test that user preference overrides auto selection."""
    result = select_model_and_tools(
        message="What's the weather?",
        attachments=[],
        history=[],
        user_model_preference="frontier",
        enabled_tools=["web_search"]
    )
    
    assert result.model_id == "frontier"
    assert result.confidence == 1.0
    assert "user-selected" in result.reasoning.lower()


def test_select_model_auto_preference():
    """Test that 'auto' preference triggers selection logic."""
    result = select_model_and_tools(
        message="Analyze this complex problem",
        attachments=[],
        history=[],
        user_model_preference="auto",
        enabled_tools=[]
    )
    
    # Should not use user preference, should select based on content
    assert result.model_id in ["chat", "research", "frontier"]
    assert result.confidence < 1.0  # Not user-selected


def test_get_model_capabilities_existing():
    """Test getting capabilities for existing model."""
    capabilities = get_model_capabilities("chat")
    
    assert capabilities is not None
    assert capabilities.id == "chat"
    assert capabilities.name == "Chat Model"
    assert capabilities.supports_vision is False
    assert capabilities.supports_tools is False


def test_get_model_capabilities_nonexistent():
    """Test getting capabilities for non-existent model."""
    capabilities = get_model_capabilities("nonexistent")
    assert capabilities is None


def test_list_available_models():
    """Test listing all available models."""
    models = list_available_models()
    
    assert len(models) == 3
    assert any(m.id == "chat" for m in models)
    assert any(m.id == "research" for m in models)
    assert any(m.id == "frontier" for m in models)
    
    # Verify structure
    for model in models:
        assert hasattr(model, "id")
        assert hasattr(model, "name")
        assert hasattr(model, "description")
        assert hasattr(model, "supports_vision")
        assert hasattr(model, "supports_tools")
        assert hasattr(model, "supports_reasoning")
        assert hasattr(model, "max_tokens")
        assert hasattr(model, "cost_tier")
        assert hasattr(model, "speed_tier")


def test_available_models_structure():
    """Test AVAILABLE_MODELS dictionary structure."""
    assert "chat" in AVAILABLE_MODELS
    assert "research" in AVAILABLE_MODELS
    assert "frontier" in AVAILABLE_MODELS
    
    # Verify chat model
    chat = AVAILABLE_MODELS["chat"]
    assert chat.supports_vision is False
    assert chat.supports_tools is False
    assert chat.cost_tier == "low"
    assert chat.speed_tier == "fast"
    
    # Verify research model
    research = AVAILABLE_MODELS["research"]
    assert research.supports_tools is True
    assert research.supports_reasoning is True
    assert research.cost_tier == "medium"
    
    # Verify frontier model
    frontier = AVAILABLE_MODELS["frontier"]
    assert frontier.supports_vision is True
    assert frontier.supports_tools is True
    assert frontier.supports_reasoning is True
    assert frontier.cost_tier == "high"


def test_select_model_confidence_scoring():
    """Test confidence scoring for different scenarios."""
    # High confidence: clear vision requirement
    result1 = select_model_and_tools(
        "What's in this image?",
        [{"type": "image/jpeg", "name": "test.jpg", "url": "http://example.com/test.jpg"}],
        [],
        None,
        []
    )
    assert result1.confidence >= 0.9
    
    # Medium-high confidence: clear tool need
    result2 = select_model_and_tools(
        "Search for the latest AI news",
        [],
        [],
        None,
        ["web_search"]
    )
    assert 0.8 <= result2.confidence < 0.95
    
    # Medium confidence: reasoning needed
    result3 = select_model_and_tools(
        "Analyze this problem",
        [],
        [],
        None,
        []
    )
    assert 0.7 <= result3.confidence < 0.9
    
    # High confidence: simple chat
    result4 = select_model_and_tools(
        "Hello",
        [],
        [],
        None,
        []
    )
    assert result4.confidence >= 0.85

