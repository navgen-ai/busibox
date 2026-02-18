import pytest

from app.channel_identity import ChannelIdentityResolver


@pytest.mark.unit
def test_resolve_returns_binding_when_present():
    resolver = ChannelIdentityResolver(
        {
            "telegram:1234": "user-1",
            "signal:+15551231234": "user-1",
        }
    )
    assert resolver.resolve("telegram", "1234") == "user-1"
    assert resolver.resolve("signal", "+15551231234") == "user-1"


@pytest.mark.unit
def test_resolve_falls_back_to_channel_scoped_identity():
    resolver = ChannelIdentityResolver({})
    assert resolver.resolve("telegram", "1234") == "telegram:1234"
    assert resolver.resolve("discord", "abc") == "discord:abc"
