from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agents.graph import OrderFlow
from app.agents.vendor_flow import VendorFlow
from app.config import get_settings
from app.db import get_db
from app.models import Customer, Order, Vendor
from app.services.delivery_fee_service import compute_delivery_fee
from app.services.order_service import OrderService
from app.services.paystack import PaystackClient
from app.services.vendor_alerts import AlertChannel, VendorAlertService
from app.services.whatsapp import WhatsAppClient

router = APIRouter()

# Per-phone drop-off coordinates shared via WhatsApp, attached to the next order.
PENDING_LOCATIONS: dict[str, tuple[float, float]] = {}

# Vendor operator number for the MVP single-vendor deployment.
DEFAULT_VENDOR_ID = 1


def _naira(total: Decimal) -> str:
    return f"\u20a6{total:,.2f}"


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
                mtype = msg.get("type")
                wa_phone = msg.get("from")
                if mtype == "text":
                    background_tasks.add_task(
                        handle_inbound_message,
                        db,
                        wa_phone,
                        msg.get("text", {}).get("body", ""),
                    )
                elif mtype == "location":
                    background_tasks.add_task(
                        handle_inbound_location, db, wa_phone, msg.get("location", {})
                    )
                elif mtype == "interactive":
                    # Button taps (vendor order actions) -> normalize to a command.
                    button = msg.get("interactive", {}).get("button_reply", {})
                    reply_id = button.get("id", "")
                    cmd, _, arg = reply_id.partition(":")
                    if cmd and arg:
                        background_tasks.add_task(
                            handle_inbound_message, db, wa_phone, f"{cmd} #{arg}"
                        )
    return {"status": "received"}


class MessageSimulate(BaseModel):
    wa_phone: str
    text: str


@router.post("/simulate")
async def simulate_message(
    body: MessageSimulate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    """Debug endpoint to trigger the LangGraph agent directly."""
    background_tasks.add_task(
        handle_inbound_message, db, body.wa_phone, body.text
    )
    return {"status": "simulated", "message": f"Simulating message from {body.wa_phone}"}


def _resolve_vendor(db: Session) -> Vendor | None:
    return db.query(Vendor).filter(Vendor.id == DEFAULT_VENDOR_ID).first()


async def handle_inbound_message(db: Session, wa_phone: str, text: str) -> None:
    vendor = _resolve_vendor(db)
    is_vendor = vendor is not None and (
        wa_phone == vendor.alert_phone
        or (vendor.whatsapp_business_phone and wa_phone == vendor.whatsapp_business_phone)
    )
    if is_vendor:
        await VendorFlow(db, vendor).handle(wa_phone, text)
        return

    state: dict = {"wa_phone": wa_phone, "customer_text": text}
    coords = PENDING_LOCATIONS.pop(wa_phone, None)
    if coords:
        state["dropoff_latitude"], state["dropoff_longitude"] = coords
    await OrderFlow(db, vendor_id=DEFAULT_VENDOR_ID).run(state)


async def handle_inbound_location(db: Session, wa_phone: str, location: dict) -> None:
    lat = location.get("latitude")
    lng = location.get("longitude")
    if lat is None or lng is None:
        return
    PENDING_LOCATIONS[wa_phone] = (float(lat), float(lng))

    wa = WhatsAppClient()
    await wa.send_text(
        wa_phone,
        f"Got your location {chr(0x1F4CD)} \u2014 I'll use it to work out the "
        "dispatch fee. Go ahead and place your order.",
    )

    # If this customer has a live order without a distance yet, re-quote it now.
    customer = (
        db.query(Customer).filter(Customer.whatsapp_phone == wa_phone).first()
    )
    if not customer:
        return
    order = (
        db.query(Order)
        .filter(
            Order.customer_id == customer.id,
            Order.delivery_type == "delivery",
            Order.distance_km.is_(None),
        )
        .order_by(Order.created_at.desc())
        .first()
    )
    if not order:
        return
    fee, km = compute_delivery_fee(
        vendor=order.vendor,
        dropoff_latitude=float(lat),
        dropoff_longitude=float(lng),
    )
    order.delivery_fee = fee
    order.total = order.subtotal + fee
    order.distance_km = km
    order.dropoff_latitude = float(lat)
    order.dropoff_longitude = float(lng)
    db.commit()
    distance_txt = f" ({km:.1f} km)" if km is not None else ""
    await wa.send_text(
        wa_phone,
        f"Updated quote for order #{order.id}:\n"
        f"Delivery fee{distance_txt}: {_naira(fee)}\n"
        f"Total: {_naira(order.total)}",
    )


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
    summary = _order_summary(order)
    if vendor.alert_webhook_url:
        await VendorAlertService().send(
            alert_phone=vendor.alert_phone,
            channel=AlertChannel.WEBHOOK,
            webhook_url=vendor.alert_webhook_url,
            message=f"NEW PAID ORDER: {summary}. Ref: #{order.id}. Please start preparing.",
        )
    else:
        # Template send so the vendor is pinged even outside the 24h window.
        await WhatsAppClient().send_templated_or_text(
            vendor.alert_phone,
            settings.whatsapp_template_new_order,
            f"NEW PAID ORDER: {summary}. Ref: #{order.id}. Please start preparing.",
        )

    await WhatsAppClient().send_text(
        order.customer.whatsapp_phone,
        f"Payment confirmed! Ref #{order.id}. The vendor is now preparing your food. \u2705",
    )
    return {"status": "ok"}
