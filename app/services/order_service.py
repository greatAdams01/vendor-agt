from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import Customer, MenuItem, Order, OrderItem, Vendor
from app.models.enums import EscalationReason, OrderStatus, PaymentStatus
from app.services.delivery_fee_service import compute_delivery_fee
from app.services.paystack import PaystackClient


class OrderService:
    """Creates, prices, and advances orders. Kept framework-agnostic so both the
    LangGraph agents and the payment webhook share one code path."""

    def __init__(self, db: Session, paystack: PaystackClient | None = None) -> None:
        self.db = db
        self.paystack = paystack or PaystackClient()

    def get_or_create_customer(self, wa_phone: str, name: str | None = None) -> Customer:
        customer = (
            self.db.query(Customer).filter(Customer.whatsapp_phone == wa_phone).first()
        )
        if customer:
            if name and not customer.name:
                customer.name = name
            return customer
        customer = Customer(whatsapp_phone=wa_phone, name=name)
        self.db.add(customer)
        self.db.flush()
        return customer

    def build_preview(
        self,
        vendor_id: int,
        line_items: list[dict],
        delivery_type: str = "delivery",
        dropoff_latitude: float | None = None,
        dropoff_longitude: float | None = None,
    ) -> dict:
        """Price a current menu cart. Returns a preview dict WITHOUT persisting."""
        vendor = self.db.query(Vendor).filter(Vendor.id == vendor_id).first()
        subtotal = Decimal("0")
        items: list[dict] = []
        for line in line_items:
            menu_item = (
                self.db.query(MenuItem)
                .filter(MenuItem.vendor_id == vendor_id, MenuItem.name.ilike(f"%{line['name']}%"))
                .first()
            )
            name = menu_item.name if menu_item else line["name"]
            unit_price = menu_item.price if menu_item else Decimal("0")
            qty = max(int(line.get("quantity", 1)), 1)
            items.append(
                {
                    "name": name,
                    "quantity": qty,
                    "unit_price": unit_price,
                    "notes": line.get("notes"),
                    "id": menu_item.id if menu_item else None,
                }
            )
            subtotal += unit_price * qty
        if delivery_type == "delivery":
            delivery_fee, distance_km = compute_delivery_fee(
                vendor=vendor,
                dropoff_latitude=dropoff_latitude,
                dropoff_longitude=dropoff_longitude,
            )
        else:
            delivery_fee, distance_km = Decimal("0"), None
        return {
            "items": items,
            "subtotal": subtotal,
            "delivery_fee": delivery_fee,
            "total": subtotal + delivery_fee,
            "delivery_type": delivery_type,
            "distance_km": distance_km,
        }

    def persist_order(
        self,
        *,
        vendor_id: int,
        wa_phone: str,
        customer_name: str | None,
        line_items: list[dict],
        preview: dict | None = None,
        delivery_address: str | None = None,
        notes: str | None = None,
        escalation: EscalationReason | None = None,
        dropoff_latitude: float | None = None,
        dropoff_longitude: float | None = None,
    ) -> Order:
        preview = preview or self.build_preview(vendor_id, line_items)
        customer = self.get_or_create_customer(wa_phone, customer_name)
        order = Order(
            vendor_id=vendor_id,
            customer_id=customer.id,
            status=(
                OrderStatus.AWAITING_VENDOR_REVIEW.value
                if escalation
                else OrderStatus.AWAITING_PAYMENT.value
            ),
            payment_status=PaymentStatus.UNPAID.value,
            delivery_type=preview["delivery_type"],
            delivery_address=delivery_address,
            subtotal=preview["subtotal"],
            delivery_fee=preview["delivery_fee"],
            total=preview["total"],
            currency="NGN",
            customer_notes=notes,
            escalation_reason=escalation.value if escalation else None,
            dropoff_latitude=dropoff_latitude,
            dropoff_longitude=dropoff_longitude,
            distance_km=preview.get("distance_km"),
        )
        self.db.add(order)
        self.db.flush()
        for item in preview["items"]:
            self.db.add(
                OrderItem(
                    order_id=order.id,
                    menu_item_id=item.get("id"),
                    name=item["name"],
                    quantity=item["quantity"],
                    unit_price=item["unit_price"],
                    notes=item.get("notes"),
                )
            )
        self.db.commit()
        self.db.refresh(order)
        return order

    def get_order(self, order_id: int) -> Order | None:
        return self.db.query(Order).filter(Order.id == order_id).first()

    def update_delivery_fee(self, order_id: int, fee: Decimal) -> Order | None:
        """Vendor-issued override of the dispatch-rider fee on an existing order.

        Recomputes the total and returns the updated order, or None if unknown.
        """
        order = self.db.query(Order).filter(Order.id == order_id).first()
        if not order:
            return None
        order.delivery_fee = fee
        order.total = order.subtotal + fee
        self.db.commit()
        self.db.refresh(order)
        return order

    async def initiate_payment(self, order: Order, customer_email: str) -> dict[str, str]:
        """Create a Paystack transaction, store its reference, return checkout URL."""
        reference = f"CHOPA-{uuid.uuid4().hex[:12].upper()}"
        authorization_url = await self.paystack.initialize_transaction(
            amount_kobo=int(order.total * 100),
            email=customer_email,
            reference=reference,
        )
        order.reference = reference
        order.status = OrderStatus.AWAITING_PAYMENT.value
        self.db.commit()
        return {"reference": reference, "authorization_url": authorization_url}

    def mark_paid(self, reference: str) -> Order | None:
        """Advance a paid order and return it (so the caller can alert the vendor).

        Returns None if the reference is unknown or already handled.
        """
        order = (
            self.db.query(Order).filter(Order.reference == reference).first()
        )
        if not order or order.payment_status == PaymentStatus.PAID.value:
            return None
        order.payment_status = PaymentStatus.PAID.value
        order.status = OrderStatus.PAID.value
        self.db.commit()
        self.db.refresh(order)
        return order