"""
Bridge HTTP API

FastAPI application providing email sending and health check endpoints.
Runs alongside the Signal bot polling loop as a co-process.

Authentication:
  Bridge trusts requests from the internal container network (Option A from plan).
  All containers are isolated; bridge only listens on its internal IP and is not
  exposed externally. This is the same trust boundary that allows Busibox Portal to
  call AuthZ unauthenticated for the magic-link flow.
"""

import logging
from typing import Awaitable, Callable, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, EmailStr

from .config import Settings
from .whatsapp_client import WhatsAppClient
from .email_client import EmailClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class SendMagicLinkRequest(BaseModel):
    to: str
    magic_link_url: str = ""
    totp_code: str = ""


class SendMagicLinkSimpleRequest(BaseModel):
    to: str
    magic_link_url: str


class SendEmailRequest(BaseModel):
    to: str
    subject: str
    html: str
    text: str = ""


class SendWelcomeRequest(BaseModel):
    to: str
    user_name: Optional[str] = None
    portal_url: str = ""


class SendAccountNotificationRequest(BaseModel):
    to: str
    portal_url: str = ""


class SendTestRequest(BaseModel):
    to: str


class EmailResponse(BaseModel):
    success: bool
    provider: str = "none"
    message: str = ""


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(
    settings: Settings,
    whatsapp_handler: Optional[Callable[[dict], Awaitable[None]]] = None,
) -> FastAPI:
    """Create the FastAPI application with email endpoints."""

    app = FastAPI(
        title="Bridge API",
        description="Multi-channel communication bridge — email, Signal, and more.",
        version="1.0.0",
    )

    email_client = EmailClient(settings)

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "service": "bridge",
            "email_provider": email_client.provider,
            "email_enabled": settings.email_enabled,
            "signal_enabled": settings.signal_enabled,
            "telegram_enabled": settings.telegram_enabled,
            "discord_enabled": settings.discord_enabled,
            "whatsapp_enabled": settings.whatsapp_enabled,
        }

    # ------------------------------------------------------------------
    # WhatsApp webhook endpoints (Cloud API)
    # ------------------------------------------------------------------

    @app.get("/api/v1/channels/whatsapp/webhook")
    async def whatsapp_verify(
        mode: str = Query(alias="hub.mode"),
        verify_token: str = Query(alias="hub.verify_token"),
        challenge: str = Query(alias="hub.challenge"),
    ):
        if not settings.whatsapp_enabled:
            raise HTTPException(status_code=503, detail="WhatsApp channel not enabled")
        if mode != "subscribe":
            raise HTTPException(status_code=400, detail="Invalid mode")
        if not settings.whatsapp_verify_token or verify_token != settings.whatsapp_verify_token:
            raise HTTPException(status_code=403, detail="Invalid verify token")
        return int(challenge) if challenge.isdigit() else challenge

    @app.post("/api/v1/channels/whatsapp/webhook")
    async def whatsapp_webhook(request: Request):
        if not settings.whatsapp_enabled:
            raise HTTPException(status_code=503, detail="WhatsApp channel not enabled")
        payload = await request.json()
        # Basic validation: if parser sees nothing, still ACK to avoid retries.
        parsed = WhatsAppClient.parse_webhook_messages(payload)
        if parsed and whatsapp_handler is not None:
            await whatsapp_handler(payload)
        return {"ok": True, "message_count": len(parsed)}

    # ------------------------------------------------------------------
    # Email endpoints
    # ------------------------------------------------------------------

    @app.post("/api/v1/email/send-magic-link", response_model=EmailResponse)
    async def send_magic_link(req: SendMagicLinkRequest):
        """
        Send a magic-link authentication email with TOTP code.
        Called by Busibox Portal during the login flow.
        """
        if not settings.email_enabled:
            raise HTTPException(status_code=503, detail="Email channel is not enabled on bridge")
        try:
            result = await email_client.send_magic_link(req.to, req.magic_link_url, req.totp_code)
            return EmailResponse(
                success=result.get("success", True),
                provider=result.get("provider", "unknown"),
                message=f"Magic link email sent to {req.to}",
            )
        except Exception as exc:
            logger.error(f"[API] send-magic-link failed: {exc}")
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/v1/email/send-magic-link-simple", response_model=EmailResponse)
    async def send_magic_link_simple(req: SendMagicLinkSimpleRequest):
        """
        Send a simple magic-link email (no TOTP code).
        Used for activation links sent to new users.
        """
        if not settings.email_enabled:
            raise HTTPException(status_code=503, detail="Email channel is not enabled on bridge")
        try:
            result = await email_client.send_magic_link_simple(req.to, req.magic_link_url)
            return EmailResponse(
                success=result.get("success", True),
                provider=result.get("provider", "unknown"),
                message=f"Magic link email sent to {req.to}",
            )
        except Exception as exc:
            logger.error(f"[API] send-magic-link-simple failed: {exc}")
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/v1/email/send", response_model=EmailResponse)
    async def send_email(req: SendEmailRequest):
        """
        Send a generic email with custom subject/body.
        """
        if not settings.email_enabled:
            raise HTTPException(status_code=503, detail="Email channel is not enabled on bridge")
        try:
            result = await email_client.send(req.to, req.subject, req.html, req.text)
            return EmailResponse(
                success=result.get("success", True),
                provider=result.get("provider", "unknown"),
                message=f"Email sent to {req.to}",
            )
        except Exception as exc:
            logger.error(f"[API] send failed: {exc}")
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/v1/email/send-welcome", response_model=EmailResponse)
    async def send_welcome(req: SendWelcomeRequest):
        """Send a welcome email to a new user."""
        if not settings.email_enabled:
            raise HTTPException(status_code=503, detail="Email channel is not enabled on bridge")
        try:
            result = await email_client.send_welcome(req.to, req.user_name, req.portal_url)
            return EmailResponse(
                success=result.get("success", True),
                provider=result.get("provider", "unknown"),
                message=f"Welcome email sent to {req.to}",
            )
        except Exception as exc:
            logger.error(f"[API] send-welcome failed: {exc}")
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/v1/email/send-account-deactivated", response_model=EmailResponse)
    async def send_account_deactivated(req: SendAccountNotificationRequest):
        """Send account deactivation notification."""
        if not settings.email_enabled:
            raise HTTPException(status_code=503, detail="Email channel is not enabled on bridge")
        try:
            result = await email_client.send_account_deactivated(req.to)
            return EmailResponse(
                success=result.get("success", True),
                provider=result.get("provider", "unknown"),
                message=f"Deactivation email sent to {req.to}",
            )
        except Exception as exc:
            logger.error(f"[API] send-account-deactivated failed: {exc}")
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/v1/email/send-account-reactivated", response_model=EmailResponse)
    async def send_account_reactivated(req: SendAccountNotificationRequest):
        """Send account reactivation notification."""
        if not settings.email_enabled:
            raise HTTPException(status_code=503, detail="Email channel is not enabled on bridge")
        try:
            result = await email_client.send_account_reactivated(req.to, req.portal_url)
            return EmailResponse(
                success=result.get("success", True),
                provider=result.get("provider", "unknown"),
                message=f"Reactivation email sent to {req.to}",
            )
        except Exception as exc:
            logger.error(f"[API] send-account-reactivated failed: {exc}")
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/v1/email/test", response_model=EmailResponse)
    async def send_test_email(req: SendTestRequest):
        """Send a test email to verify SMTP / Resend configuration."""
        if not settings.email_enabled:
            raise HTTPException(status_code=503, detail="Email channel is not enabled on bridge")
        try:
            result = await email_client.send_test(req.to)
            return EmailResponse(
                success=result.get("success", True),
                provider=result.get("provider", "unknown"),
                message=f"Test email sent to {req.to} via {result.get('provider')}",
            )
        except Exception as exc:
            logger.error(f"[API] test email failed: {exc}")
            raise HTTPException(status_code=500, detail=str(exc))

    return app
