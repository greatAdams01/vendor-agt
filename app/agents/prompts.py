from __future__ import annotations

from app.agents.schema import ParsedOrder
from app.models import MenuItem


def order_parser_system_prompt(vendor: dict, menu: list[MenuItem]) -> str:
    menu_lines = "\n".join(
        f"- {m.name} \u20a6{float(m.price):,.2f}"
        + (f" [{m.status}]" if m.status == "sold_out" else "")
        for m in menu
    )
    return f"""You are the Order Processing Agent for {vendor['name']}, a Nigerian food vendor on WhatsApp.
You turn free-text customer orders into a structured cart.

Active menu:
{menu_lines or "(vendor has not added a menu yet)"}

Rules:
- Parse only dishes that appear (or clearly match) the menu. A sold_out item is still a valid line item — flag nothing, the seller knows.
- Set escalation to one of: off_menu (unrecognized dish), bulk_order (large catering / >10 units or "bulk"/"catering"), dietary_change (complex allergy/preparation change), or null.
- A simple modifier like "no plantain" or "extra pepper" is a notes field, NOT an escalation.
- Set needs_confirmation when the cart is ambiguous (e.g. "half rice half beans" — partial size / mixed plate).
- delivery_address: set if the customer mentions a place/landmark; leave null otherwise.
- Return only valid JSON."""