"""
Shared fixtures for load tests.

These tests run against a live agent-api instance. Set environment variables:
  AGENT_API_URL  - base URL (default: http://localhost:8000)
  AUTH_TOKEN      - valid JWT for authenticated endpoints
"""

import os

import pytest

AGENT_API_URL = os.getenv("AGENT_API_URL", "http://localhost:8000")
AUTH_TOKEN = os.getenv("AUTH_TOKEN", "")

LOAD_TEST_QUERIES = [
    "What is the current status of our projects?",
    "Summarize recent activity across all teams.",
    "List the top 5 priorities for this quarter.",
    "How many open tasks do we have?",
    "Show me a breakdown of work by category.",
    "What blockers were reported this week?",
    "Give me an overview of team capacity.",
    "What deadlines are coming up in the next 30 days?",
]

TOOL_TEST_QUERIES = [
    "Search for documents about project planning",
    "Query the data for recent records",
    "List all data documents available",
    "Find information about team members",
]


@pytest.fixture
def agent_api_url():
    return AGENT_API_URL


@pytest.fixture
def auth_headers():
    if not AUTH_TOKEN:
        pytest.skip("AUTH_TOKEN not set -- cannot run load tests against live API")
    return {
        "Authorization": f"Bearer {AUTH_TOKEN}",
        "Content-Type": "application/json",
    }
