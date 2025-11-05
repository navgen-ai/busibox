"""
Shared fixtures for integration tests.
"""
import os
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

# Load .env from busibox root directory
busibox_root = Path(__file__).parent.parent.parent.parent.parent
env_file = busibox_root / ".env"
if env_file.exists():
    load_dotenv(env_file)
    print(f"Loaded environment from {env_file}")
else:
    print(f"Warning: .env file not found at {env_file}")

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from shared.config import Config


@pytest.fixture(scope="session")
def config():
    """Load configuration from environment variables."""
    return Config()


@pytest.fixture(scope="session")
def test_user_id():
    """Generate a test user ID for integration tests."""
    import uuid
    return str(uuid.uuid4())


@pytest.fixture(autouse=True)
async def cleanup_test_data(config, test_user_id):
    """Clean up test data after each test."""
    yield
    # Cleanup will be handled by individual tests
    pass

