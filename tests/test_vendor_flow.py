import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents import vendor_flow as vf
from app.agents.vendor_flow import VendorFlow
from app.models import Order, OrderItem, Vendor
from app.models.enums import OrderStatus


class FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def limit(self, n):
        return self

    def all(self):
        return self._result

    def first(self):
        return self._result[0] if self._result else None


class FakeDB:
    def __init__(self, orders):
        self.orders = orders
        self.committed = 0

    def query(self, model):
        if model is Order:
            return FakeQuery(self.orders)
        return FakeQuery([])

    def add(self, obj):
        pass

    def commit(self):
        self.committed += 1


def _order(order_id: int, status: str = "paid") -> Order:
    vendor = Vendor(alert_phone="+2348000000000")
    order = Order(
        id=order_id,
        vendor_id=1,
        vendor=vendor,
        status=status,
        delivery_type="delivery",
        delivery_fee=Decimal("500"),
        subtotal=Decimal("2500"),
        total=Decimal("3000"),
    )
    order.customer = MagicMock(name="Ada", whatsapp_phone="+2348012345678")
    order.items = [
        OrderItem(name="Jollof Rice", quantity=1, unit_price=Decimal("2500"))
    ]
    return order


def _flow(db, wa) -> VendorFlow:
    vendor = Vendor(
        alert_phone="+2348000000000",
        name="Madam Grace Kitchen",
        is_available=True,
        delivery_base_fee=Decimal("1000"),
        delivery_rate_per_km=Decimal("300"),
        vendor_latitude=6.5244,
        vendor_longitude=3.3792,
    )
    vf.WhatsAppClient = MagicMock(return_value=wa)
    return VendorFlow(db, vendor)


def test_unknown_command_gets_help_hint():
    wa = AsyncMock()
    db = FakeDB([_order(1)])
    flow = _flow(db, wa)

    asyncio.run(flow.handle("+2348000000000", "xyzzy"))
    wa.send_text.assert_awaited_once()
    text = wa.send_text.await_args.args[1]
    assert "I didn't get that" in text


def test_orders_lists_orders():
    wa = AsyncMock()
    db = FakeDB([_order(1)])
    flow = _flow(db, wa)

    asyncio.run(flow.handle("+2348000000000", "orders"))
    text = wa.send_text.await_args.args[1]
    assert "#1" in text
    assert "Jollof Rice" in text


def test_open_sends_interactive_buttons():
    wa = AsyncMock()
    db = FakeDB([_order(1)])
    flow = _flow(db, wa)

    asyncio.run(flow.handle("+2348000000000", "open 1"))
    wa.send_interactive.assert_awaited_once()
    buttons = wa.send_interactive.await_args.args[2]
    assert [b["reply"]["id"] for b in buttons] == ["ready:1", "dispatch:1", "delivered:1"]


def test_dispatch_marks_dispatched_and_notifies_customer():
    wa = AsyncMock()
    order = _order(1)
    db = FakeDB([order])
    flow = _flow(db, wa)

    asyncio.run(flow.handle("+2348000000000", "dispatch #1"))
    assert order.status == OrderStatus.DISPATCHED.value
    assert db.committed == 1
    wa.send_templated_or_text.assert_awaited_once()
    assert wa.send_templated_or_text.await_args.args[0] == "+2348012345678"


def test_fee_command_overrides_delivery_fee():
    wa = AsyncMock()
    order = _order(1)
    db = FakeDB([order])
    flow = _flow(db, wa)

    asyncio.run(flow.handle("+2348000000000", "fee #1 1200"))
    assert order.delivery_fee == Decimal("1200")
    assert order.total == Decimal("3700")
    wa.send_templated_or_text.assert_awaited_once()


def test_close_command_marks_vendor_unavailable():
    wa = AsyncMock()
    db = FakeDB([_order(1)])
    flow = _flow(db, wa)
    assert flow.vendor.is_available is True

    asyncio.run(flow.handle("+2348000000000", "close"))
    assert flow.vendor.is_available is False
    assert db.committed == 1
    text = wa.send_text.await_args.args[1]
    assert "closed" in text.lower()


def test_open_command_marks_vendor_available():
    wa = AsyncMock()
    db = FakeDB([_order(1)])
    flow = _flow(db, wa)
    flow.vendor.is_available = False

    asyncio.run(flow.handle("+2348000000000", "open shop"))
    assert flow.vendor.is_available is True
    text = wa.send_text.await_args.args[1]
    assert "open" in text.lower()
