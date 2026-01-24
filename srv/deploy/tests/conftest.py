"""Test fixtures for deployment service"""
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def mock_ssh_command():
    """Mock SSH command execution"""
    async def mock_execute(host: str, command: str):
        return "", "", 0
    return mock_execute


@pytest.fixture
def sample_manifest():
    """Sample app manifest"""
    return {
        "name": "Test App",
        "id": "test-app",
        "version": "1.0.0",
        "description": "Test application",
        "icon": "Calculator",
        "defaultPath": "/testapp",
        "defaultPort": 3010,
        "healthEndpoint": "/api/health",
        "buildCommand": "npm run build",
        "startCommand": "npm start",
        "appMode": "prisma",
        "database": {
            "required": True,
            "preferredName": "testapp",
            "schemaManagement": "prisma"
        },
        "requiredEnvVars": ["LITELLM_API_KEY"],
        "optionalEnvVars": []
    }


@pytest.fixture
def sample_config():
    """Sample deployment config"""
    return {
        "githubRepoOwner": "test-owner",
        "githubRepoName": "test-repo",
        "githubBranch": "main",
        "environment": "staging",
        "secrets": {}
    }
