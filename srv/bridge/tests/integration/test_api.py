import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import Settings


@pytest.mark.integration
def test_health_endpoint_reports_channel_flags():
    settings = Settings(
        signal_enabled=True,
        telegram_enabled=True,
        discord_enabled=False,
        whatsapp_enabled=False,
        email_enabled=False,
    )
    app = create_app(settings)
    client = TestClient(app)

    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["signal_enabled"] is True
    assert data["telegram_enabled"] is True
    assert data["discord_enabled"] is False


@pytest.mark.integration
def test_whatsapp_webhook_verify_success_when_enabled():
    settings = Settings(
        whatsapp_enabled=True,
        whatsapp_verify_token="verify-me",
    )
    app = create_app(settings)
    client = TestClient(app)

    resp = client.get(
        "/api/v1/channels/whatsapp/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "verify-me",
            "hub.challenge": "12345",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == 12345


@pytest.mark.integration
def test_whatsapp_webhook_verify_rejects_invalid_token():
    settings = Settings(
        whatsapp_enabled=True,
        whatsapp_verify_token="verify-me",
    )
    app = create_app(settings)
    client = TestClient(app)

    resp = client.get(
        "/api/v1/channels/whatsapp/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "bad-token",
            "hub.challenge": "abc",
        },
    )
    assert resp.status_code == 403


@pytest.mark.integration
def test_whatsapp_webhook_post_accepts_payload():
    calls = {"count": 0}

    async def handler(payload: dict):
        calls["count"] += 1

    settings = Settings(
        whatsapp_enabled=True,
        whatsapp_verify_token="verify-me",
    )
    app = create_app(settings, whatsapp_handler=handler)
    client = TestClient(app)

    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.1",
                                    "from": "15550000001",
                                    "type": "text",
                                    "text": {"body": "hello"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    resp = client.post("/api/v1/channels/whatsapp/webhook", json=payload)
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["message_count"] == 1
    assert calls["count"] == 1
