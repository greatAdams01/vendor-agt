from __future__ import annotations

from decimal import Decimal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.agents.prompts import order_parser_system_prompt
from app.agents.schema import ParsedOrder
from app.agents.state import OrderState
from app.config import get_settings
from app.models import MenuItem, Vendor
from app.models.enums import EscalationReason
from app.services.order_service import OrderService
from app.services.paystack import PaystackClient
from app.services.vendor_alerts import VendorAlertService
from app.services.whatsapp import WhatsAppClient


def _naira(total: Decimal) -> str:
    return f"\u20a6{total:,.2f}"


class OrderFlow:
    """LangGraph orchestration of the Greeting / Order Processing / Payment agents.

    Nodes:
      intent   — deterministic routing of the raw message (menu/FAQ vs order vs pay).
      parse    — LLM-structured order into a cart, computing totals + escalation flags.
      confirm  — persist a standard order; reply with price + delivery fee.
      pay      — create the Paystack link and send it to the customer.
      escalate — hand a custom/bulk request to the vendor for review + alert.

    The LLM is used only where language understanding is genuinely needed
    (parsing free-text orders). Everything else is fast deterministic templates,
    keeping cost/latency low as the PRD requires.
    """

    def __init__(self, db: Session, vendor_id: int) -> None:
        self.db = db
        self.settings = get_settings()
        self.vendor: Vendor = db.query(Vendor).get(vendor_id)
        if self.vendor is None:
            raise ValueError(f"vendor {vendor_id} not found")
        self.order_svc = OrderService(db, paystack=PaystackClient())
        self.alerts = VendorAlertService()
        self.wa = WhatsAppClient()
        self.menu = (
            db.query(MenuItem)
            .filter(MenuItem.vendor_id == vendor_id, MenuItem.status == "available")
            .order_by(MenuItem.id)
            .all()
        )
        self._model = ChatOpenAI(
            model=self.settings.llm_model,
            temperature=0,
            api_key=self.settings.llm_api_key or None,
            base_url=self.settings.llm_base_url or None,
        )

    # ------------------------------------------------------------- graph build
    def build(self):
        builder = StateGraph(OrderState)
        builder.add_node("intent", self.intent_node)
        builder.add_node("intro", self.intro_node)
        builder.add_node("parse", self.parse_node)
        builder.add_node("confirm", self.confirm_node)
        builder.add_node("pay", self.pay_node)
        builder.add_node("escalate", self.escalate_node)
        builder.add_edge(START, "intent")
        builder.add_conditional_edges(
            "intent",
            self._route_intent,
            {"MENU": "intro", "ORDER": "parse", "CLOSED": END},
        )
        builder.add_edge("intro", END)
        builder.add_conditional_edges(
            "parse", self._route_parse, {"confirm": "confirm", "escalate": "escalate"}
        )
        builder.add_edge("confirm", "pay")
        builder.add_edge("pay", END)
        builder.add_edge("escalate", END)
        return builder.compile()

    # ------------------------------------------------------------------- nodes
    async def intro_node(self, state: OrderState) -> OrderState:
        """Greeting & Menu agent — fully autonomous, no vendor touch."""
        state["reply"] = self._greeting_message()
        return state

    async def run(self, state: OrderState) -> str:
        """Run the graph and deliver the final reply back to the customer."""
        result = await self.build().ainvoke(state)
        reply = result.get("reply", "")
        if reply:
            await self.wa.send_text(state["wa_phone"], reply)
        return reply

    async def intent_node(self, state: OrderState) -> OrderState:
        text = (state.get("customer_text") or "").strip().lower()
        if not text or any(
            word in text for word in ("hi", "hello", "hey", "menu", "what do you have")
        ):
            state["_intent"] = "MENU"
        else:
            state["_intent"] = "ORDER"
        if state["_intent"] == "ORDER" and not self.vendor.is_available:
            state["_intent"] = "CLOSED"
            state["reply"] = self._closed_message()
        return state

    @staticmethod
    def _route_intent(state: OrderState) -> str:
        return state.get("_intent", "MENU")

    def _route_parse(self, state: OrderState) -> str:
        return "escalate" if state.get("escalation") else "confirm"

    async def parse_node(self, state: OrderState) -> OrderState:
        parsed = self._parse(state["customer_text"])
        state = self._apply_parsed(state, parsed)
        state["reply"] = ""
        return state

    async def confirm_node(self, state: OrderState) -> OrderState:
        preview = self.order_svc.build_preview(
            self.vendor.id,
            state["line_items"],
            state["delivery_type"],
            state.get("dropoff_latitude"),
            state.get("dropoff_longitude"),
        )
        state.update(self._preview_to_state(preview))
        order = self.order_svc.persist_order(
            vendor_id=self.vendor.id,
            wa_phone=state["wa_phone"],
            customer_name=state.get("customer_name"),
            line_items=state["line_items"],
            preview=preview,
            delivery_address=state.get("delivery_address"),
            notes=state.get("customer_text"),
            dropoff_latitude=state.get("dropoff_latitude"),
            dropoff_longitude=state.get("dropoff_longitude"),
        )
        state["order_id"] = order.id
        state["reply"] = self._confirm_message(order, preview)
        return state

    async def pay_node(self, state: OrderState) -> OrderState:
        order = self.order_svc.get_order(state["order_id"])
        if not order:
            state["reply"] = "Something went wrong starting payment. Please try again."
            return state
        payment = await self.order_svc.initiate_payment(
            order, customer_email="chopagent@example.com"
        )
        state["payment_link"] = payment["authorization_url"]
        existing = state.get("reply") or ""
        state["reply"] = (existing + "\n\n" if existing else "") + self._payment_message(payment)
        return state

    async def escalate_node(self, state: OrderState) -> OrderState:
        order = self.order_svc.persist_order(
            vendor_id=self.vendor.id,
            wa_phone=state["wa_phone"],
            customer_name=state.get("customer_name"),
            line_items=state.get("line_items", []),
            delivery_address=state.get("delivery_address"),
            notes=state.get("customer_text"),
            escalation=state["escalation"],
            dropoff_latitude=state.get("dropoff_latitude"),
            dropoff_longitude=state.get("dropoff_longitude"),
        )
        state["order_id"] = order.id
        await self.alerts.send(
            alert_phone=self.vendor.alert_phone,
            message=(
                f"Pending custom request from {state['wa_phone']}: "
                f"{state.get('escalation_note') or state['customer_text']} (ref #{order.id}). "
                f"Please review."
            ),
        )
        state["reply"] = (
            "That's a special request — I've pinged the vendor and they'll get back "
            "to you shortly. Hang tight!"
        )
        return state

    # -------------------------------------------------------------- formatting
    def _closed_message(self) -> str:
        return (
            f"Sorry \u2014 {self.vendor.name} is currently *closed* and not taking "
            "new orders right now. I'll let you know as soon as they open again. "
            "Reply *menu* anytime to browse."
        )

    def _greeting_message(self) -> str:
        lines = [
            f"Welcome to {self.vendor.name}! \ud83c\udf5b",
            "Here's today's menu:",
        ]
        if not self.vendor.is_available:
            lines.append("\u26d4 *Shop currently closed* \u2014 no new orders right now.")
        for m in self.menu:
            lines.append(f"\u2022 {m.name} \u2014 {_naira(m.price)}")
        lines.append("\nJust tell me what you'd like, e.g. *2 x Jollof and turkey*.")
        return "\n".join(lines)

    def _confirm_message(self, order, preview: dict) -> str:
        item_lines = "\n".join(
            f"{i['quantity']}x {i['name']}" + (f" ({i['notes']})" if i.get("notes") else "")
            for i in preview["items"]
        )
        distance = preview.get("distance_km")
        distance_txt = f" ({distance:.1f} km)" if distance is not None else ""
        return (
            f"Your order (ref #{order.id}):\n{item_lines}\n"
            f"Subtotal: {_naira(preview['subtotal'])}\n"
            f"Delivery fee{distance_txt}: {_naira(preview['delivery_fee'])}\n"
            f"Total: {_naira(preview['total'])}"
        )

    def _payment_message(self, payment: dict) -> str:
        return (
            f"Tap to pay securely: {payment['authorization_url']}\n"
            "I'll notify you the moment your payment is confirmed. \u2705"
        )

    # ------------------------------------------------------------------- parse
    @staticmethod
    def _preview_to_state(preview: dict) -> dict:
        return {
            "subtotal": int(preview["subtotal"] * 100),
            "delivery_fee": int(preview["delivery_fee"] * 100),
            "total": int(preview["total"] * 100),
            "delivery_type": preview["delivery_type"],
        }

    def _parse(self, text: str) -> ParsedOrder:
        messages = [
            SystemMessage(order_parser_system_prompt(self.vendor, self.menu)),
            HumanMessage(text),
        ]
        resp = self._model.invoke(messages, response_format={"type": "json_object"})
        return ParsedOrder.model_validate_json(resp.content)

    @staticmethod
    def _apply_parsed(state: OrderState, parsed: ParsedOrder) -> OrderState:
        state["line_items"] = [
            {
                "name": li.name,
                "quantity": li.quantity,
                "notes": li.notes,
            }
            for li in parsed.line_items
        ]
        state["delivery_type"] = parsed.delivery_type or "delivery"
        if parsed.delivery_address:
            state["delivery_address"] = parsed.delivery_address
        state["escalation"] = parsed.escalation
        state["escalation_note"] = parsed.escalation_note
        state["_intent"] = "ORDER"
        return state