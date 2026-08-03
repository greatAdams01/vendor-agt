from __future__ import annotations

import hashlib
import hmac
import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.agents.graph import OrderFlow
from app.config import get_settings
from app.db import get_db
from app.models import Order
from app.services.order_service import OrderService
from app.services.paystack import PaystackClient
from app.services.vendor_alerts import AlertChannel, VendorAlertService
from app.services.whatsapp import WhatsAppClient

router = APIRouter()


def _order_summary(order: Order) -> str:
    items = ", ".join(f"{i.quantity}x {i.name}" for i in order.items)
    deliver_label = order.delivery_address or ("pickup" if order.delivery_type == "pickup" else "delivery")
    return f"{items} -> {deliver_label}"


# ------------------------------------------------------------------ WhatsApp
@router.get("/whatsapp/webhook")
def whatsapp_verify(request: Request) -> dict:
    """Verification handshake for the WhatsApp Cloud API."""
    settings = get_settings()
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token == settings.whatsapp_verify_token:
        return {"hub.challenge": challenge}
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/whatsapp/webhook")
async def whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    payload = await request.json()
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for msg in change.get("value", {}).get("messages", []):
                if msg.get("type") != "text":
                    continue
                background_tasks.add_task(
                    handle_inbound_message,
                    db,
                    msg.get("from"),
                    msg.get("text", {}).get("body", ""),
                )
    return {"status": "received"}


async def handle_inbound_message(db: Session, wa_phone: str, text: str) -> None:
    # Phase 1 is single-vendor. Resolve vendor_id from WhatsApp number when
    # multi-vendor auth lands; default to vendor 1 for the MVP.
    flow = OrderFlow(db, vendor_id=1)
    await flow.run({"wa_phone": wa_phone, "customer_text": text})


# ------------------------------------------------------------- Paystack webhook
@router.post("/paystack/webhook")
async def paystack_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    signature = request.headers.get("x-paystack-signature", "")
    body = await request.body()
    expected = hmac.new(
        settings.paystack_secret_key.encode(), body, hashlib.sha512
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=400, detail="Invalid signature")

    event = json.loads(body or "{}")
    if event.get("event") != "charge.success":
        return {"status": "ignored"}

    reference = event.get("data", {}).get("reference")
    order = OrderService(db, paystack=PaystackClient()).mark_paid(reference)
    if not order:
        return {"status": "noop", "reason": "unknown_or_duplicate_reference"}

    vendor = order.vendor
    await VendorAlertService().send(
        alert_phone=vendor.alert_phone,
        channel=AlertChannel.WHATSAPP if vendor.alert_webhook_url is None else AlertChannel.WEBHOOK,
        webhook_url=vendor.alert_webhook_url,
        message=(
            f"NEW PAID ORDER: {_order_summary(order)}. Ref: #{order.id}. "
            "Please start preparing."
        ),
    )

    await WhatsAppClient().send_text(
        order.customer.whatsapp_phone,
        f"Payment confirmed! Ref #{order.id}. The vendor is now preparing your food. \u2705",
    )
    return {"status": "ok"}