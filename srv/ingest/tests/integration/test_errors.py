"""
Integration test for error scenarios.
"""
import uuid
from io import BytesIO

import pytest
import structlog
from fastapi.testclient import TestClient

from api.main import app
from api.services.postgres import PostgresService
from shared.config import Config

logger = structlog.get_logger()


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_invalid_file_type(config: Config, test_user_id: str, client: TestClient):
    """Test that invalid file types are rejected."""
    # Try to upload an unsupported file type
    file_content = BytesIO(b"Some binary content")
    file_content.name = "test.exe"
    
    response = client.post(
        "/upload",
        headers={"X-User-Id": test_user_id},
        files={"file": ("test.exe", file_content, "application/x-msdownload")},
    )
    
    assert response.status_code == 400
    data = response.json()
    assert "error" in data
    assert "Unsupported file type" in data["error"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_missing_user_id(client: TestClient):
    """Test that requests without user ID are rejected."""
    file_content = BytesIO(b"Test content")
    file_content.name = "test.txt"
    
    response = client.post(
        "/upload",
        files={"file": ("test.txt", file_content, "text/plain")},
    )
    
    assert response.status_code == 401
    data = response.json()
    assert "error" in data
    assert "X-User-Id" in data["error"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_invalid_user_id_format(client: TestClient):
    """Test that invalid user ID format is rejected."""
    file_content = BytesIO(b"Test content")
    file_content.name = "test.txt"
    
    response = client.post(
        "/upload",
        headers={"X-User-Id": "not-a-uuid"},
        files={"file": ("test.txt", file_content, "text/plain")},
    )
    
    assert response.status_code == 400
    data = response.json()
    assert "error" in data
    assert "UUID" in data["error"]


@pytest.mark.integration
def test_file_not_found(test_user_id: str, client: TestClient):
    """Test that requesting metadata for non-existent file returns 404."""
    fake_file_id = str(uuid.uuid4())
    
    response = client.get(
        f"/files/{fake_file_id}",
        headers={"X-User-Id": test_user_id},
    )
    
    assert response.status_code == 404
    data = response.json()
    assert "error" in data
    assert "not found" in data["error"].lower()

