import pytest

from app.config import Settings


@pytest.mark.unit
def test_channel_user_bindings_json_parsed():
    settings = Settings(
        channel_user_bindings='{"signal:+1555":"user-a","telegram:123":"user-a"}'
    )
    parsed = settings.get_channel_user_bindings()
    assert parsed["signal:+1555"] == "user-a"
    assert parsed["telegram:123"] == "user-a"


@pytest.mark.unit
def test_channel_user_bindings_invalid_json_returns_empty():
    settings = Settings(channel_user_bindings="{not-valid-json")
    assert settings.get_channel_user_bindings() == {}


@pytest.mark.unit
def test_comma_separated_parsers():
    settings = Settings(
        allowed_phone_numbers="+1,+2",
        telegram_allowed_chat_ids="123,456",
        discord_channel_ids="chan-a,chan-b",
        whatsapp_allowed_phone_numbers="+10,+20",
        email_allowed_senders="alice@example.com,bob@example.com",
    )
    assert settings.get_allowed_phone_numbers() == ["+1", "+2"]
    assert settings.get_allowed_telegram_chat_ids() == ["123", "456"]
    assert settings.get_discord_channel_ids() == ["chan-a", "chan-b"]
    assert settings.get_allowed_whatsapp_phone_numbers() == ["+10", "+20"]
    assert settings.get_email_allowed_senders() == ["alice@example.com", "bob@example.com"]
