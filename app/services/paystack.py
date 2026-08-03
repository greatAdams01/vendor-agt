from __future__ import annotations

import httpx

from app.config import get_settings


class PaystackError(Exception):
    """Raised when Paystack returns a non-2xx response."""


class PaystackClient:
    """Thin async client for the Paystack payment gateway."""

    def __init__(self, secret_key: str | None = None, base_url: str | None = None) -> None:
        settings = get_settings()
        self._base_url = (base_url or settings.paystack_base_url).rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {secret_key or settings.paystack_secret_key}",
            "Content-Type": "application/json",
        }

    async def initialize_transaction(self, *, amount_kobo: int, email: str, reference: str) -> dict:
        """Create a transaction and return its checkout/authorization URL."""
        payload = {
            "amount": amount_kobo,
            "email": email,
            "reference": reference,
            "currency": "NGN",
            "channels": ["bank", "card", "ussd", "transfer", "mobile_money"],
        }
        data = await self._post("/transaction/initialize", payload)
        return data["authorization_url"]

    async def verify_transaction(self, reference: str) -> dict:
        """Verify a transaction by reference and return its status payload."""
        data = await self._get(f"/transaction/verify/{reference}")
        return data

    async def _post(self, path: str, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{self._base_url}{path}", json=payload, headers=self._headers)
        return self._raise_or_json(resp)

    async def _get(self, path: str) -> dict:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{self._base_url}{path}", headers=self._headers)
        return self._raise_or_json(resp)

    @staticmethod
    def _raise_or_json(resp: httpx.Response) -> dict:
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if not resp.is_success:
            raise PaystackError(f"Paystack {resp.status_code}: {body}")
        return body