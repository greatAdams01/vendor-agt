from __future__ import annotations

import enum

import httpx

from app.config import get_settings


class AlertChannel(str, enum.Enum):
    SMS = "sms"
    WHATSAPP = "whatsapp"
    WEBHOOK = "webhook"


class VendorAlertService:
    """Delivers time-critical alerts to the vendor (payment received, custom request).

    Phase 1 ships WhatsApp + webhook delivery. SMS can be added behind the same
    interface by plugging a provider (e.g. Termii, Twilio) into `_send_sms`.
    """

    async def send(
        self,
        *,
        alert_phone: str,
        message: str,
        channel: AlertChannel = AlertChannel.WHATSAPP,
        webhook_url: str | None = None,
    ) -> None:
        if channel == AlertChannel.WEBHOOK and webhook_url:
            await self._send_webhook(webhook_url, message)
            return
        if channel == AlertChannel.WHATSAPP:
            from app.services.whatsapp import WhatsAppClient

            client = WhatsAppClient()
            await client.send_text(alert_phone, message)
            return
        await self._send_sms(alert_phone, message)

    async def _send_webhook(self, webhook_url: str, message: str) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                webhook_url,
                json={"text": message, "source": "chopagent", "type": "vendor_alert"},
            )

    async def _send_sms(self, phone: str, message: str) -> None:
        settings = get_settings()
        # TODO: integrate a provider. Raise if misconfigured so failures are loud.
        if not settings.paystack_secret_key:  # placeholder guard
            raise NotImplementedError(
                "SMS delivery is not configured yet. Use WHATSAPP or WEBHOOK channel."
            )
        await self._send_webhook(phone, message)