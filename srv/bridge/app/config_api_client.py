"""
Config API Client for Bridge

Reads email configuration from config-api on demand.

Flow:
  1. Caller passes a config-api scoped JWT (minted by authz with
     scope=config.email.read) in the Authorization header to bridge
  2. Bridge uses this token directly to read email config from config-api
  3. Config is returned to the EmailClient for that send operation

Falls back to env-var settings if no JWT is provided or config-api is unreachable.
"""

import logging
import time
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_CACHE_TTL = 60
_cached_config: Optional[dict] = None
_cached_at: float = 0


@dataclass
class EmailSettings:
    """Email provider settings fetched from config-api."""
    smtp_enabled: bool = False
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_secure: bool = False
    resend_enabled: bool = False
    email_from: Optional[str] = None
    resend_api_key: Optional[str] = None
    email_enabled: bool = False

    @property
    def provider(self) -> str:
        if self.smtp_enabled and self.smtp_host and self.smtp_port and self.smtp_user:
            return "smtp"
        if self.resend_enabled and self.resend_api_key:
            return "resend"
        return "none"


async def _read_email_config_from_api(
    config_api_url: str,
    config_token: str,
) -> Optional[dict]:
    """Read email/smtp config entries from config-api."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{config_api_url}/admin/config",
                params={"category": "smtp"},
                headers={"Authorization": f"Bearer {config_token}"},
            )
            resp.raise_for_status()
            data = resp.json()

            configs = data.get("configs", [])
            result = {}
            for cfg in configs:
                key = cfg.get("key", "")
                encrypted = cfg.get("encrypted", False)
                if encrypted:
                    raw_resp = await client.get(
                        f"{config_api_url}/admin/config/{key}/raw",
                        headers={"Authorization": f"Bearer {config_token}"},
                    )
                    if raw_resp.is_success:
                        raw_data = raw_resp.json()
                        result[key] = raw_data.get("value", "")
                else:
                    result[key] = cfg.get("value", "")
            return result
    except Exception as exc:
        logger.warning(f"[CONFIG-API] Failed to read email config: {exc}")
        return None


def _parse_email_settings(raw: dict) -> EmailSettings:
    """Parse raw config key/value pairs into EmailSettings."""
    port_str = raw.get("SMTP_PORT", "")
    port = None
    if port_str:
        try:
            port = int(port_str)
        except ValueError:
            pass

    smtp_host = raw.get("SMTP_HOST") or None
    smtp_user = raw.get("SMTP_USER") or None
    resend_key = raw.get("RESEND_API_KEY") or None
    smtp_enabled_raw = raw.get("SMTP_ENABLED")
    resend_enabled_raw = raw.get("RESEND_ENABLED")
    has_smtp = bool(smtp_host and port and smtp_user)
    has_resend = bool(resend_key)
    smtp_enabled = (str(smtp_enabled_raw).lower() == "true") if smtp_enabled_raw is not None else has_smtp
    resend_enabled = (str(resend_enabled_raw).lower() == "true") if resend_enabled_raw is not None else has_resend

    return EmailSettings(
        smtp_enabled=smtp_enabled,
        smtp_host=smtp_host,
        smtp_port=port,
        smtp_user=smtp_user,
        smtp_password=raw.get("SMTP_PASSWORD") or None,
        smtp_secure=raw.get("SMTP_SECURE", "").lower() == "true",
        resend_enabled=resend_enabled,
        email_from=raw.get("EMAIL_FROM") or None,
        resend_api_key=resend_key,
        email_enabled=(smtp_enabled and has_smtp) or (resend_enabled and has_resend),
    )


async def get_email_settings(
    config_token: Optional[str],
    config_api_url: str,
) -> Optional[EmailSettings]:
    """
    Fetch email settings from config-api using the provided config-api token.

    The token should already have aud=config-api and scope=config.email.read
    (minted by authz). No token exchange is performed here.

    Returns None if no token provided or config-api is unreachable,
    signalling the caller to fall back to env-var settings.
    """
    if not config_token:
        return None

    global _cached_config, _cached_at
    now = time.time()
    if _cached_config is not None and (now - _cached_at) < _CACHE_TTL:
        return _parse_email_settings(_cached_config)

    raw = await _read_email_config_from_api(config_api_url, config_token)
    if raw is None:
        return None

    _cached_config = raw
    _cached_at = now
    return _parse_email_settings(raw)


def clear_config_cache() -> None:
    """Clear cached config to force re-read on next request."""
    global _cached_config, _cached_at
    _cached_config = None
    _cached_at = 0
