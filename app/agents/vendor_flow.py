from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Customer, MenuItem, Order, Vendor
from app.models.enums import MenuItemStatus, OrderStatus
from app.services.whatsapp import WhatsAppClient

EMOJI_PHONE = "\U0001f4cd"


def _naira(total: Decimal) -> str:
    return f"\u20a6{total:,.2f}"


def _status_emoji(status: str) -> str:
    return {
        OrderStatus.AWAITING_PAYMENT.value: "\u23f3",
        OrderStatus.AWAITING_VENDOR_REVIEW.value: "\ud83d\udea8",
        OrderStatus.PAID.value: "\u2705",
        OrderStatus.PREPARING.value: "\ud83c\udf73",
        OrderStatus.DISPATCHED.value: "\ud83d\ude9a",
        OrderStatus.DELIVERED.value: "\ud83c\udf89",
    }.get(status, "")


class VendorFlow:
    """Phone-based operator interface. The vendor drives their whole business
    from WhatsApp: listing orders, updating status, editing dispatch fees,
    managing the menu, and broadcasting promos. No website or dashboard needed."""

    def __init__(self, db: Session, vendor: Vendor) -> None:
        self.db = db
        self.vendor = vendor
        self.wa = WhatsAppClient()
        self.settings = get_settings()

    # ------------------------------------------------------------------ entry
    async def handle(self, phone: str, text: str) -> None:
        cmd = text.strip()
        lowered = cmd.lower()

        if lowered in ("help", "hi", "hello", "start", "menu help"):
            return await self._reply(self._help())
        if lowered in ("orders", "order list", "new orders"):
            return await self._reply(self._list_orders())
        if lowered in ("menu", "show menu"):
            return await self._reply(self._menu_lines())
        if re.fullmatch(r"(ready|preparing)\s+#?\d+", lowered):
            return await self._set_status(lowered, OrderStatus.PREPARING)
        if re.fullmatch(r"dispatch\s+#?\d+", lowered):
            return await self._set_status(lowered, OrderStatus.DISPATCHED)
        if re.fullmatch(r"(done|delivered|deliver)\s+#?\d+", lowered):
            return await self._set_status(lowered, OrderStatus.DELIVERED)
        if (m := re.fullmatch(r"open\s+#?(\d+)", lowered)) is not None:
            return await self._open(int(m.group(1)))
        if (m := re.fullmatch(r"fee\s+#?(\d+)\s+(\d[\d.,]*)", lowered)) is not None:
            return await self._set_fee(int(m.group(1)), m.group(2))
        if lowered.startswith("add "):
            return await self._add_item(cmd[4:].strip())
        if (m := re.fullmatch(r"(soldout|onsale)\s+(.+)", lowered)) is not None:
            return await self._toggle_item(m.group(1), m.group(2).strip())
        if (m := re.fullmatch(r"pricing\s+(\d[\d.]*)\s+(\d[\d.]*)", lowered)) is not None:
            return await self._set_pricing(m.group(1), m.group(2))
        if (m := re.fullmatch(r"setloc\s+(-?\d[\d.]*)\s+(-?\d[\d.]*)", lowered)) is not None:
            return await self._set_location(m.group(1), m.group(2))
        if lowered.startswith("broadcast "):
            return await self._broadcast(cmd[len("broadcast "):].strip())

        await self._reply(
            "I didn't get that. Reply *help* to see what I can do."
        )

    # ------------------------------------------------------------ order commands
    def _list_orders(self) -> str:
        orders = (
            self.db.query(Order)
            .filter(Order.vendor_id == self.vendor.id)
            .order_by(Order.created_at.desc())
            .limit(12)
            .all()
        )
        if not orders:
            return "No orders yet. New ones will pop up here as they come in."
        lines = ["\U0001f4b0 Your recent orders:"]
        for o in orders:
            items = ", ".join(f"{i.quantity}x {i.name}" for i in o.items)
            lines.append(
                f"{_status_emoji(o.status)} #{o.id} [{o.status}]\n   {items} \u2014 {_naira(o.total)}"
            )
        lines.append("\nReply *open #<id>* to view an order.")
        return "\n".join(lines)

    async def _open(self, order_id: int) -> None:
        order = self._get_order(order_id)
        if not order:
            return await self._reply(f"No order #{order_id} found.")
        items = "\n".join(
            f"  \u2022 {i.quantity}x {i.name}" + (f" ({i.notes})" if i.notes else "")
            for i in order.items
        )
        address = (
            order.delivery_address
            or ("pickup" if order.delivery_type == "pickup" else "not provided")
        )
        distance = (
            f"{order.distance_km:.1f} km" if order.distance_km is not None else "n/a"
        )
        body = (
            f"Order #{order.id} \u2014 [{order.status} | {order.payment_status}]\n"
            f"{items}\n"
            f"Customer: {order.customer.name or '—'} ({order.customer.whatsapp_phone})\n"
            f"Deliver to: {address}\n"
            f"Distance: {distance}\n"
            f"Subtotal: {_naira(order.subtotal)}\n"
            f"Delivery fee: {_naira(order.delivery_fee)}\n"
            f"Total: {_naira(order.total)}\n"
            + (f"Notes: {order.customer_notes}\n" if order.customer_notes else "")
            + "Tip: reply *fee <id> <amount>* to change the rider fee."
        )
        # Buttons for the common status transitions.
        try:
            await self.wa.send_interactive(
                self.vendor.alert_phone,
                body,
                [
                    {"type": "reply", "reply": {"id": f"ready:{order_id}", "title": "Ready"}},
                    {"type": "reply", "reply": {"id": f"dispatch:{order_id}", "title": "Dispatch"}},
                    {"type": "reply", "reply": {"id": f"delivered:{order_id}", "title": "Delivered"}},
                ],
            )
        except Exception:
            await self._reply(body)

    # ------------------------------------------------------------- status ops
    async def _set_status(self, cmd: str, new_status: OrderStatus) -> None:
        order_id = int(re.search(r"#?\d+", cmd).group(0).lstrip("#"))
        order = self._get_order(order_id)
        if not order:
            return await self._reply(f"No order #{order_id}.")
        order.status = new_status.value
        self.db.commit()

        to = order.customer.whatsapp_phone
        if new_status == OrderStatus.DISPATCHED:
            await self.wa.send_templated_or_text(
                to,
                self.settings.whatsapp_template_on_the_way,
                f"Your order (ref #{order.id}) is on the way! \U0001f69a",
            )
        elif new_status == OrderStatus.DELIVERED:
            await self.wa.send_templated_or_text(
                to,
                self.settings.whatsapp_template_delivered,
                f"Your order (ref #{order.id}) has been delivered. Enjoy! \U0001f389",
            )
        await self._reply(
            f"Order #{order.id} marked *{new_status.value}*. Customer notified."
        )

    async def _set_fee(self, order_id: int, amount: str) -> None:
        order = self._get_order(order_id)
        if not order:
            return await self._reply(f"No order #{order_id}.")
        try:
            fee = Decimal(amount)
        except InvalidOperation:
            return await self._reply("Fee must be a number, e.g. *fee #3 1500*.")
        order.delivery_fee = fee
        order.total = order.subtotal + fee
        self.db.commit()
        await self.wa.send_templated_or_text(
            order.customer.whatsapp_phone,
            self.settings.whatsapp_template_fee_updated,
            (
                f"Ref #{order.id}: your dispatch fee is now {_naira(fee)} "
                f"(total {_naira(order.total)}). Please settle this with the rider."
            ),
        )
        await self._reply(
            f"Order #{order.id} delivery fee updated to {_naira(fee)}. "
            f"New total {_naira(order.total)}. Customer notified."
        )

    # ---------------------------------------------------------------- menu
    def _menu_lines(self) -> str:
        items = (
            self.db.query(MenuItem)
            .filter(MenuItem.vendor_id == self.vendor.id)
            .order_by(MenuItem.id)
            .all()
        )
        if not items:
            return "No menu items yet. Add one with *add <name> <price>*."
        return "\n".join(
            f"\u2022 {i.name} \u2014 {_naira(i.price)}"
            + (f" [sold out]" if i.status == MenuItemStatus.SOLD_OUT.value else "")
            for i in items
        )

    async def _add_item(self, rest: str) -> None:
        price_match = re.search(r"(\d[\d.]*)\s*$", rest)
        if not price_match:
            return await self._reply("Usage: *add <name> <price>*, e.g. *add Fried Turkey 2000*.")
        try:
            price = Decimal(price_match.group(1))
        except InvalidOperation:
            return await self._reply("Price must be a number.")
        name = rest[: price_match.start()].strip()
        if not name:
            return await self._reply("Add a name too, e.g. *add Fried Turkey 2000*.")
        item = MenuItem(
            vendor_id=self.vendor.id,
            name=name,
            price=price,
            status=MenuItemStatus.AVAILABLE.value,
        )
        self.db.add(item)
        self.db.commit()
        await self._reply(f"Added *{name}* \u2014 {_naira(price)}.")

    async def _toggle_item(self, action: str, name: str) -> None:
        item = (
            self.db.query(MenuItem)
            .filter(
                MenuItem.vendor_id == self.vendor.id,
                MenuItem.name.ilike(f"%{name}%"),
            )
            .first()
        )
        if not item:
            return await self._reply(f"No menu item matching *{name}*.")
        if action == "soldout":
            item.status = MenuItemStatus.SOLD_OUT.value
            message = f"*{item.name}* is now marked *sold out*."
        else:
            item.status = MenuItemStatus.AVAILABLE.value
            message = f"*{item.name}* is back on the menu."
        self.db.commit()
        await self._reply(message)

    # ------------------------------------------------------------- delivery config
    async def _set_pricing(self, base: str, rate: str) -> None:
        try:
            self.vendor.delivery_base_fee = Decimal(base)
            self.vendor.delivery_rate_per_km = Decimal(rate)
        except InvalidOperation:
            return await self._reply("Pricing values must be numbers.")
        self.db.commit()
        await self._reply(
            f"Dispatch pricing set: base {_naira(self.vendor.delivery_base_fee)} "
            f"+ {_naira(self.vendor.delivery_rate_per_km)}/km."
        )

    async def _set_location(self, lat: str, lng: str) -> None:
        try:
            self.vendor.vendor_latitude = float(lat)
            self.vendor.vendor_longitude = float(lng)
        except ValueError:
            return await self._reply("Location values must be numbers.")
        self.db.commit()
        await self._reply("Your shop coordinates are set \u2713")

    # ------------------------------------------------------------ broadcast
    async def _broadcast(self, message: str) -> None:
        template = self.settings.whatsapp_template_promo
        if not template:
            return await self._reply("No broadcast template configured.")
        phones = {c.whatsapp_phone for c in self.db.query(Customer).all()}
        if not phones:
            return await self._reply("No customers saved yet to broadcast to.")
        sent = 0
        for phone in phones:
            try:
                await self.wa.send_template(phone, template, [message])
                sent += 1
            except Exception:
                continue
        await self._reply(f"Broadcast to {sent}/{len(phones)} customers (template {template}).")

    # ------------------------------------------------------------------ helpers
    def _get_order(self, order_id: int) -> Order | None:
        return (
            self.db.query(Order)
            .filter(Order.id == order_id, Order.vendor_id == self.vendor.id)
            .first()
        )

    async def _reply(self, text: str) -> None:
        await self.wa.send_text(self.vendor.alert_phone, text)

    def _help(self) -> str:
        return (
            "Hi! I run your shop over WhatsApp. Commands:\n"
            "*orders* \u2014 list recent orders\n"
            "*open <id>* \u2014 view order + status buttons\n"
            "*ready / dispatch / done <id>* \u2014 advance an order\n"
            "*fee <id> <amount>* \u2014 edit the rider fee\n"
            "*menu* \u2014 show menu\n"
            "*add <name> <price>* \u2014 add a dish\n"
            "*soldout <name>* / *onsale <name>* \u2014 toggle availability\n"
            "*pricing <base> <rate>* \u2014 set delivery base + per-km fee\n"
            "*setloc <lat> <lng>* \u2014 set your shop coordinates\n"
            "*broadcast <text>* \u2014 promo message to all customers"
        )