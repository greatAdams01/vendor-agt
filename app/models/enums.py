import enum


class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    AWAITING_CUSTOMER_CONFIRM = "awaiting_customer_confirm"
    AWAITING_VENDOR_REVIEW = "awaiting_vendor_review"  # unique/custom request escalated
    AWAITING_PAYMENT = "awaiting_payment"
    PAID = "paid"
    PREPARING = "preparing"
    DISPATCHED = "dispatched"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class PaymentStatus(str, enum.Enum):
    UNPAID = "unpaid"
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"


class MenuItemStatus(str, enum.Enum):
    AVAILABLE = "available"
    SOLD_OUT = "sold_out"


class EscalationReason(str, enum.Enum):
    OFF_MENU = "off_menu"
    BULK_ORDER = "bulk_order"
    DIETARY_CHANGE = "dietary_change"
    DELIVERY_ADDRESS_UNKNOWN = "delivery_address_unknown"


class DeliveryType(str, enum.Enum):
    DELIVERY = "delivery"
    PICKUP = "pickup"