import pytest

from app.whatsapp_client import WhatsAppClient


@pytest.mark.unit
def test_parse_webhook_messages_extracts_text_messages():
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
                                },
                                {
                                    "id": "wamid.2",
                                    "from": "15550000002",
                                    "type": "image",
                                },
                            ]
                        }
                    }
                ]
            }
        ]
    }
    out = WhatsAppClient.parse_webhook_messages(payload)
    assert len(out) == 1
    assert out[0].message_id == "wamid.1"
    assert out[0].from_phone == "15550000001"
    assert out[0].text == "hello"


@pytest.mark.unit
def test_parse_webhook_messages_handles_empty_payload():
    assert WhatsAppClient.parse_webhook_messages({}) == []
