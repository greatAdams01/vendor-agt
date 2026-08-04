from __future__ import annotations

from typing import TypedDict

from app.models.enums import EscalationReason


class ParsedLineItem(TypedDict):
    name: str
    quantity: int
    notes: str | None


class OrderState(TypedDict, total=False):
    """Shared state flowing through the LangGraph agent nodes."""

    wa_phone: str
    customer_text: str
    customer_name: str | None

    # routing
    _intent: str  # MENU | ORDER | PAY

    # Order Processing agent output
    line_items: list[ParsedLineItem]
    escalation: EscalationReason | None
    escalation_note: str | None
    delivery_type: str
    delivery_address: str | None
    dropoff_latitude: float | None
    dropoff_longitude: float | None

    # totals (kobo, integer)
    subtotal: int
    delivery_fee: int
    total: int

    # Payment agent
    order_id: int | None
    payment_link: str | None

    # Final WhatsApp reply to the customer
    reply: str