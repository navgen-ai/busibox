import pytest

from app.email_inbound_client import EmailInboundClient


@pytest.mark.unit
def test_extract_body_prefers_plain_text_part():
    raw = (
        "From: alice@example.com\r\n"
        "To: bridge@example.com\r\n"
        "Subject: Hello\r\n"
        "Content-Type: multipart/alternative; boundary=abc\r\n\r\n"
        "--abc\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n\r\n"
        "plain body\r\n"
        "--abc\r\n"
        "Content-Type: text/html; charset=utf-8\r\n\r\n"
        "<p>html body</p>\r\n"
        "--abc--\r\n"
    ).encode("utf-8")

    import email

    msg = email.message_from_bytes(raw)
    client = EmailInboundClient(
        host="imap.example.com",
        port=993,
        username="u",
        password="p",
    )
    body = client._extract_body(msg)
    assert body.strip() == "plain body"


@pytest.mark.unit
def test_extract_body_from_singlepart_plain_text():
    raw = (
        "From: bob@example.com\r\n"
        "To: bridge@example.com\r\n"
        "Subject: Hi\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n\r\n"
        "single body\r\n"
    ).encode("utf-8")

    import email

    msg = email.message_from_bytes(raw)
    client = EmailInboundClient(
        host="imap.example.com",
        port=993,
        username="u",
        password="p",
    )
    body = client._extract_body(msg)
    assert body.strip() == "single body"
