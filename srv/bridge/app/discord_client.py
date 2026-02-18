"""
Discord REST polling client.

This uses channel polling over the REST API so it can run without a gateway
websocket dependency. Configure explicit channel IDs to monitor.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import AsyncGenerator, Dict, Optional

import httpx

logger = logging.getLogger(__name__)


@dataclass
class DiscordMessage:
    """Represents an inbound Discord message."""

    message_id: str
    channel_id: str
    author_id: str
    content: str
    is_bot: bool = False


class DiscordClient:
    """Async client for Discord channel polling and message send."""

    def __init__(self, bot_token: str):
        self.base_url = "https://discord.com/api/v10"
        self.bot_token = bot_token
        self._client: Optional[httpx.AsyncClient] = None
        self._last_seen_by_channel: Dict[str, str] = {}

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
            "Authorization": f"Bot {self.bot_token}",
            "Content-Type": "application/json",
        }

    async def poll_messages(
        self,
        channel_id: str,
        interval: float = 2.0,
    ) -> AsyncGenerator[DiscordMessage, None]:
        """Poll a specific Discord channel and yield new user messages."""
        while True:
            params: Dict[str, str] = {"limit": "20"}
            after = self._last_seen_by_channel.get(channel_id)
            if after:
                params["after"] = after

            try:
                response = await self.client.get(
                    f"{self.base_url}/channels/{channel_id}/messages",
                    params=params,
                    headers=self._headers(),
                )
                response.raise_for_status()
                messages = response.json()
                if not isinstance(messages, list):
                    await asyncio.sleep(interval)
                    continue

                # Discord returns newest first.
                for msg in reversed(messages):
                    message_id = str(msg.get("id", ""))
                    author = msg.get("author") or {}
                    author_id = str(author.get("id", ""))
                    content = (msg.get("content") or "").strip()
                    is_bot = bool(author.get("bot", False))
                    if not message_id:
                        continue

                    self._last_seen_by_channel[channel_id] = message_id

                    if is_bot or not content:
                        continue

                    yield DiscordMessage(
                        message_id=message_id,
                        channel_id=channel_id,
                        author_id=author_id,
                        content=content,
                        is_bot=is_bot,
                    )
            except Exception as e:
                logger.error("Discord poll failed for channel %s: %s", channel_id, e)
            await asyncio.sleep(interval)

    async def send_message(self, channel_id: str, content: str) -> None:
        """Send a message to a Discord channel."""
        response = await self.client.post(
            f"{self.base_url}/channels/{channel_id}/messages",
            json={"content": content},
            headers=self._headers(),
        )
        response.raise_for_status()
