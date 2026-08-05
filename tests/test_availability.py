import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.graph import OrderFlow
from app.models import MenuItem, Vendor


class FakeQuery:
    def __init__(self, result=None):
        self._result = result or []

    def filter(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def all(self):
        return self._result

    def first(self):
        return self._result[0] if self._result else None

    def get(self, pk):
        return self._first


class FakeDB:
    def __init__(self, vendor):
        self.vendor = vendor

    def query(self, model):
        q = FakeQuery()
        q._first = self.vendor if model is Vendor else None
        return q


def _closed_vendor() -> Vendor:
    return Vendor(id=1, name="Closed Kitchen", is_available=False, alert_phone="+2348000000000")


def _open_vendor() -> Vendor:
    return Vendor(id=1, name="Open Kitchen", is_available=True, alert_phone="+2348000000000")


def test_closed_vendor_blocks_order_intent():
    flow = OrderFlow.__new__(OrderFlow)
    flow.vendor = _closed_vendor()
    flow.menu = []

    state = asyncio.run(flow.intent_node({"customer_text": "2x jollof"}))
    assert state["_intent"] == "CLOSED"
    assert "closed" in state["reply"].lower()


def test_open_vendor_routes_order_to_parse():
    flow = OrderFlow.__new__(OrderFlow)
    flow.vendor = _open_vendor()

    state = asyncio.run(flow.intent_node({"customer_text": "2x jollof"}))
    assert state["_intent"] == "ORDER"


def test_closed_vendor_menu_still_served():
    flow = OrderFlow.__new__(OrderFlow)
    flow.vendor = _closed_vendor()

    state = asyncio.run(flow.intent_node({"customer_text": "menu"}))
    assert state["_intent"] == "MENU"
