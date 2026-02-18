"""
WhatsApp Cloud API helper.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


@dataclass
class WhatsAppMessage:
    """Represents an inbound WhatsApp text message."""

    from_phone: str
    message_id: str
    text: str


class WhatsAppClient:
    """Client for WhatsApp Cloud API send + webhook parsing."""

    def __init__(
        self,
        access_token: str,
        phone_number_id: str,
        api_version: str = "v22.0",
    ):
        self.access_token = access_token
        self.phone_number_id = phone_number_id
        self.api_version = api_version
        self.base_url = f"https://graph.facebook.com/{api_version}"
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("Client not initialized. Use async with context.")
        return self._client

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    async def send_message(self, to_phone: str, text: str) -> None:
        """Send a text message via WhatsApp Cloud API."""
        payload = {
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "text",
            "text": {"body": text},
        }
        response = await self.client.post(
            f"{self.base_url}/{self.phone_number_id}/messages",
            json=payload,
            headers=self._headers(),
        )
        response.raise_for_status()

    @staticmethod
    def parse_webhook_messages(payload: Dict[str, Any]) -> List[WhatsAppMessage]:
        """Extract inbound text messages from Meta webhook payload."""
        out: List[WhatsAppMessage] = []
        try:
            for entry in payload.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value") or {}
                    for message in value.get("messages", []):
                        if message.get("type") != "text":
                            continue
                        text_body = ((message.get("text") or {}).get("body") or "").strip()
                        from_phone = str(message.get("from", "")).strip()
                        message_id = str(message.get("id", "")).strip()
                        if not text_body or not from_phone:
                            continue
                        out.append(
                            WhatsAppMessage(
                                from_phone=from_phone,
                                message_id=message_id,
                                text=text_body,
                            )
                        )
        except Exception as e:
            logger.error("Failed to parse WhatsApp webhook payload: %s", e)
        return out
