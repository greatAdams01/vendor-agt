from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Order, Vendor
from app.models.enums import OrderStatus
from app.services.whatsapp import WhatsAppClient

router = APIRouter()


def _serialize(order: Order) -> dict:
    return {
        "id": order.id,
        "status": order.status,
        "payment_status": order.payment_status,
        "delivery_type": order.delivery_type,
        "delivery_address": order.delivery_address,
        "total": float(order.total),
        "currency": order.currency,
        "customer_phone": order.customer.whatsapp_phone,
        "customer_notes": order.customer_notes,
        "escalation_reason": order.escalation_reason,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "items": [
            {
                "name": i.name,
                "quantity": i.quantity,
                "unit_price": float(i.unit_price),
                "notes": i.notes,
            }
            for i in order.items
        ],
    }


@router.get("/")
def list_orders(vendor_id: int, status: str | None = None, db: Session = Depends(get_db)):
    _ensure_vendor(db, vendor_id)
    q = db.query(Order).filter(Order.vendor_id == vendor_id)
    if status:
        q = q.filter(Order.status == status)
    q = q.order_by(Order.created_at.desc())
    return {"orders": [_serialize(o) for o in q.all()]}


class StatusUpdate(BaseModel):
    status: OrderStatus


@router.patch("/{order_id}")
async def update_order_status(
    vendor_id: int, order_id: int, body: StatusUpdate, db: Session = Depends(get_db)
) -> dict:
    _ensure_vendor(db, vendor_id)
    order = db.query(Order).filter(Order.id == order_id, Order.vendor_id == vendor_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    order.status = body.status.value
    db.commit()

    if body.status == OrderStatus.DISPATCHED:
        # Per the PRD flow: vendor marks Dispatched -> customer gets a WhatsApp update.
        await WhatsAppClient().send_text(
            order.customer.whatsapp_phone,
            f"Your order (ref #{order.id}) is on the way! \ud83d\ude9a",
        )
    return {"ok": True, "status": order.status}


def _ensure_vendor(db: Session, vendor_id: int) -> None:
    if db.query(Vendor).filter(Vendor.id == vendor_id).first() is None:
        raise HTTPException(status_code=404, detail="Vendor not found")