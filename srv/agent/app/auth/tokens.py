import json
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import httpx
from jose import jwk, jwt
from jose.utils import base64url_decode
from pydantic import ValidationError

from app.config.settings import get_settings
from app.schemas.auth import Principal, TokenExchangeResponse

settings = get_settings()


class JWKSCache:
    def __init__(self) -> None:
        self._jwks: Optional[Dict] = None
        self._fetched_at: Optional[float] = None
        self._ttl_seconds = 300

    async def get(self) -> Dict:
        now = time.time()
        if self._jwks and self._fetched_at and now - self._fetched_at < self._ttl_seconds:
            return self._jwks
        if not settings.auth_jwks_url:
            raise ValueError("auth_jwks_url not configured")
        async with httpx.AsyncClient() as client:
            resp = await client.get(str(settings.auth_jwks_url), timeout=10)
            resp.raise_for_status()
            self._jwks = resp.json()
            self._fetched_at = now
            return self._jwks


jwks_cache = JWKSCache()


async def _verify_signature(token: str, jwks: Dict) -> Dict:
    headers = jwt.get_unverified_header(token)
    kid = headers.get("kid")
    if not kid:
        raise jwt.JWTError("kid missing from token header")

    key_data = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
    if not key_data:
        raise jwt.JWTError("matching jwk not found")

    public_key = jwk.construct(key_data)
    message, encoded_sig = token.rsplit(".", 1)
    decoded_sig = base64url_decode(encoded_sig.encode())
    if not public_key.verify(message.encode(), decoded_sig):
        raise jwt.JWTError("signature verification failed")

    return jwt.get_unverified_claims(token)


async def validate_bearer(token: str) -> Principal:
    jwks = await jwks_cache.get()
    claims = await _verify_signature(token, jwks)

    if settings.auth_issuer and claims.get("iss") != settings.auth_issuer:
        raise jwt.JWTError("issuer mismatch")
    if settings.auth_audience and settings.auth_audience not in claims.get("aud", []):
        raise jwt.JWTError("audience mismatch")

    try:
        sub = claims["sub"]
    except KeyError as exc:
        raise jwt.JWTError("sub missing") from exc

    scopes: List[str] = claims.get("scope", "").split() if claims.get("scope") else []
    roles: List[str] = claims.get("roles", [])
    principal = Principal(sub=sub, scopes=scopes, roles=roles, token=token)
    return principal


async def exchange_token(
    principal: Principal, scopes: List[str], purpose: str
) -> TokenExchangeResponse:
    """
    Exchange a user token for a longer-lived downstream token using OAuth2 client credentials.
    Scopes are purpose-scoped (search/ingest/rag) to minimize blast radius.
    """
    payload = {
        "grant_type": "client_credentials",
        "client_id": settings.auth_client_id,
        "client_secret": settings.auth_client_secret,
        "scope": " ".join(scopes),
        "requested_subject": principal.sub,
        "requested_purpose": purpose,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(str(settings.auth_token_url), data=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()

    expires_in = data.get("expires_in", 3600)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    return TokenExchangeResponse(
        access_token=data["access_token"],
        token_type=data.get("token_type", "bearer"),
        expires_at=expires_at,
        scopes=scopes,
    )
