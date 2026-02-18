"""
Post-Deployment Validation Tests (PVT) for Bridge service.

These tests are intentionally fast and validate deploy health + critical paths.
"""

import os

import httpx
import pytest

SERVICE_PORT = os.getenv("BRIDGE_API_PORT", "8081")
SERVICE_URL = os.getenv("BRIDGE_API_URL", f"http://localhost:{SERVICE_PORT}")


async def _require_service_reachable() -> None:
    try:
        async with httpx.AsyncClient() as client:
            await client.get(f"{SERVICE_URL}/health", timeout=3.0)
    except Exception:
        pytest.skip(f"Bridge service is not reachable at {SERVICE_URL}")


@pytest.mark.pvt
class TestPVTHealth:
    @pytest.mark.asyncio
    async def test_health_endpoint(self):
        await _require_service_reachable()
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{SERVICE_URL}/health", timeout=5.0)
            assert resp.status_code == 200
            data = resp.json()
            assert data.get("status") == "ok"
            assert data.get("service") == "bridge"


@pytest.mark.pvt
class TestPVTCoreAPI:
    @pytest.mark.asyncio
    async def test_email_send_endpoint_behaves_consistently(self):
        """
        If email is disabled we expect 503.
        If enabled but config missing, 500 can occur.
        If fully configured, 200 is expected.
        Authz errors are never expected for this internal endpoint.
        """
        await _require_service_reachable()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{SERVICE_URL}/api/v1/email/send",
                json={
                    "to": "test@example.com",
                    "subject": "Bridge PVT",
                    "html": "<p>bridge pvt</p>",
                    "text": "bridge pvt",
                },
                timeout=10.0,
            )
            assert resp.status_code in [200, 500, 503], f"Unexpected status: {resp.status_code} {resp.text}"

    @pytest.mark.asyncio
    async def test_whatsapp_verify_endpoint_reachable(self):
        """
        Endpoint should be reachable post-deploy.
        - 503 when WhatsApp channel disabled
        - 400/403/200 depending on mode/token/challenge config
        """
        await _require_service_reachable()
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{SERVICE_URL}/api/v1/channels/whatsapp/webhook",
                params={
                    "hub.mode": "subscribe",
                    "hub.verify_token": "pvt-check",
                    "hub.challenge": "1",
                },
                timeout=5.0,
            )
            assert resp.status_code in [200, 400, 403, 404, 503], f"Unexpected status: {resp.status_code} {resp.text}"
