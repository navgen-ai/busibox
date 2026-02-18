"""
Channel identity resolver.

Maps external channel identities (phone numbers, chat IDs, etc.) to stable
sender IDs so conversations can persist across channels when desired.
"""

from typing import Dict


class ChannelIdentityResolver:
    """Resolve an external channel sender into a stable sender key."""

    def __init__(self, bindings: Dict[str, str]):
        self._bindings = {k.strip().lower(): v for k, v in bindings.items() if k and v}

    def resolve(self, channel: str, external_id: str) -> str:
        """Resolve to bound identity or channel-scoped fallback identity."""
        key = f"{channel}:{external_id}".strip().lower()
        mapped = self._bindings.get(key)
        if mapped:
            return mapped
        return key
