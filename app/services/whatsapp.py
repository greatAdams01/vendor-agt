from __future__ import annotations

import httpx

from app.config import get_settings


class WhatsAppError(Exception):
    """Raised when the WhatsApp Cloud API returns a non-2xx response."""


class WhatsAppClient:
    """Minimal async client for the WhatsApp Cloud API."""

    def __init__(
        self,
        token: str | None = None,
        phone_number_id: str | None = None,
        api_version: str | None = None,
    ) -> None:
        settings = get_settings()
        self._token = token or settings.whatsapp_token
        self._phone_number_id = phone_number_id or settings.whatsapp_phone_number_id
        self._api_version = api_version or settings.whatsapp_api_version

    async def send_text(self, to: str, body: str) -> dict:
        """Send a simple text message to a WhatsApp number (E.164)."""
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"preview_url": False, "body": body},
        }
        return await self._post(payload)

    async def send_template(
        self,
        to: str,
        template_name: str,
        body_params: list[str] | None = None,
        language: str | None = None,
    ) -> dict:
        """Send an approved template message (for proactive, out-of-window sends)."""
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {
                    "code": language or get_settings().whatsapp_template_language
                },
            },
        }
        if body_params:
            payload["template"]["components"] = [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": p} for p in body_params],
                }
            ]
        return await self._post(payload)

    async def send_templated_or_text(
        self,
        to: str,
        template_name: str,
        body: str,
        language: str | None = None,
    ) -> dict:
        """Send a template message, transparently falling back to free text in dev.

        Templates are required for proactive sends outside the 24-hour service
        window. If a template isn't approved yet, we degrade to a plain text
        message so the system still works end-to-end during development.
        """
        if template_name:
            try:
                return await self.send_template(to, template_name, [body], language)
            except WhatsAppError:
                pass
        return await self.send_text(to, body)

    async def send_interactive(self, to: str, reply_body: str, buttons: list[dict]) -> dict:
        """Send an interactive reply-button message."""
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": reply_body},
                "action": {"buttons": buttons[:3]},
            },
        }
        return await self._post(payload)

    async def _post(self, payload: dict) -> dict:
        url = (
            f"https://graph.facebook.com/{self._api_version}/"
            f"{self._phone_number_id}/messages"
        )
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(url, json=payload, headers=headers)
        if not resp.is_success:
            raise WhatsAppError(f"WhatsApp {resp.status_code}: {resp.text[:500]}")
        return resp.json()