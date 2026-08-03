from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.enums import EscalationReason


class ParsedLineItem(BaseModel):
    name: str = Field(description="Menu item name as the customer said it")
    quantity: int = Field(ge=1, description="Number of portions")
    notes: str | None = Field(default=None, description="Per-item changes, e.g. 'no plantain'")


class ParsedOrder(BaseModel):
    """Structured output of the Order Processing agent's LLM call."""

    line_items: list[ParsedLineItem] = Field(description="Ordered dishes and quantities")
    delivery_type: str = Field(
        default="delivery",
        description="'delivery' or 'pickup'",
    )
    delivery_address: str | None = Field(
        default=None, description="Where to deliver, if the customer gave a place"
    )
    escalation: EscalationReason | None = Field(
        default=None,
        description=(
            "Set when the request cannot be auto-fulfilled: off-menu item, bulk/catering, "
            "or a complex dietary change. Otherwise leave null."
        ),
    )
    escalation_note: str | None = Field(
        default=None, description="Human-readable detail about the escalation"
    )
    needs_confirmation: bool = Field(
        default=False,
        description="True when line_items are ambiguous and the customer must confirm the cart",
    )
    missing_info: list[str] = Field(
        default_factory=list,
        description="Things we still need, e.g. 'delivery address'",
    )