from __future__ import annotations

from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, relationship

from app.db import Base
from app.models.enums import DeliveryType, MenuItemStatus


class Vendor(Base):
    __tablename__ = "vendors"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    is_available = Column(Boolean, default=True, nullable=False)  # accepts orders?
    whatsapp_business_phone = Column(String(20))
    alert_phone = Column(String(20), nullable=False)  # E.164 for payment/custom alerts
    alert_webhook_url = Column(Text, nullable=True)  # optional alternative alert target
    currency = Column(String(3), default="NGN", nullable=False)

    # Dispatch-rider fee config (distance-based, editable by the vendor)
    delivery_base_fee = Column(Numeric(10, 2), default=0, nullable=False)
    delivery_rate_per_km = Column(Numeric(10, 2), default=0, nullable=False)
    vendor_latitude = Column(Float, nullable=True)
    vendor_longitude = Column(Float, nullable=True)

    menu_items = relationship("MenuItem", back_populates="vendor", cascade="all, delete-orphan")
    orders = relationship("Order", back_populates="vendor")


class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True)
    whatsapp_phone = Column(String(20), unique=True, nullable=False)
    name = Column(String(255), nullable=True)
    default_address = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    orders = relationship("Order", back_populates="customer")


class MenuItem(Base):
    __tablename__ = "menu_items"
    __table_args__ = (UniqueConstraint("vendor_id", "name", name="uq_menu_vendor_name"),)

    id = Column(Integer, primary_key=True)
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    price = Column(Numeric(10, 2), nullable=False)  # per unit
    status = Column(String(20), default=MenuItemStatus.AVAILABLE.value, nullable=False)
    available_sizes = Column(Text, nullable=True)  # JSON, e.g. small/medium/large
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    vendor = relationship("Vendor", back_populates="menu_items")


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True)
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)

    status = Column(String(30), default="pending", nullable=False)
    payment_status = Column(String(20), default="unpaid", nullable=False)

    delivery_type = Column(String(20), default=DeliveryType.DELIVERY.value, nullable=False)
    delivery_address = Column(Text, nullable=True)
    delivery_fee = Column(Numeric(10, 2), default=0, nullable=False)
    subtotal = Column(Numeric(10, 2), default=0, nullable=False)
    total = Column(Numeric(10, 2), default=0, nullable=False)
    currency = Column(String(3), default="NGN")

    # Customer drop-off point (WhatsApp location share) + computed distance
    dropoff_latitude = Column(Float, nullable=True)
    dropoff_longitude = Column(Float, nullable=True)
    distance_km = Column(Numeric(10, 2), nullable=True)

    reference = Column(String(100), unique=True, nullable=True)  # Paystack order reference
    customer_notes = Column(Text, nullable=True)  # raw message / unique request detail
    escalation_reason = Column(String(40), nullable=True)  # when escalated to vendor

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    vendor = relationship("Vendor", back_populates="orders")
    customer = relationship("Customer", back_populates="orders")
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")


class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    menu_item_id = Column(Integer, ForeignKey("menu_items.id"), nullable=True)
    name = Column(String(255), nullable=False)  # snapshot name
    quantity = Column(Integer, default=1, nullable=False)
    unit_price = Column(Numeric(10, 2), nullable=False)
    notes = Column(Text, nullable=True)  # e.g. "no plantain"

    order = relationship("Order", back_populates="items")