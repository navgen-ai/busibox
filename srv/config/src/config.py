"""
Config API Service Configuration.

Environment variables and settings for the config-api service.
"""

import os
from dataclasses import dataclass


@dataclass
class Config:
    """Service configuration from environment."""

    port: int = int(os.getenv("CONFIG_PORT", "8012"))

    # PostgreSQL — own database for separation of concerns
    postgres_host: str = os.getenv("POSTGRES_HOST", "postgres")
    postgres_port: int = int(os.getenv("POSTGRES_PORT", "5432"))
    postgres_db: str = os.getenv("POSTGRES_DB", "config")
    postgres_user: str = os.getenv("POSTGRES_USER", "busibox_user")
    postgres_password: str = os.getenv("POSTGRES_PASSWORD", "")

    # Authz service (for JWT validation via JWKS)
    authz_url: str = os.getenv("AUTHZ_BASE_URL", os.getenv("AUTHZ_URL", "http://authz-api:8010"))

    # Encryption key for encrypted config values (AES-256-GCM)
    # If unset, encrypted values are stored as plaintext (dev only).
    encryption_key: str = os.getenv("CONFIG_ENCRYPTION_KEY", "")

    def to_pool_config(self) -> dict:
        """Return config dict compatible with AsyncPGPoolManager.from_config()."""
        return {
            "postgres_host": self.postgres_host,
            "postgres_port": self.postgres_port,
            "postgres_db": self.postgres_db,
            "postgres_user": self.postgres_user,
            "postgres_password": self.postgres_password,
        }


config = Config()
