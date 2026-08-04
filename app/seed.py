"""Seed a demo vendor, menu, and customer so the MVP is runnable immediately.

Usage: python -m app.seed
"""

from decimal import Decimal

from app.db import SessionLocal, init_db
from app.models import Customer, MenuItem, Vendor


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        vendor = Vendor(
            name="Madam Grace Kitchen",
            whatsapp_business_phone="+2348000000000",
            alert_phone="+2348000000000",
            delivery_base_fee=Decimal("1000"),
            delivery_rate_per_km=Decimal("300"),
            vendor_latitude=6.5244,  # Yaba, Lagos
            vendor_longitude=3.3792,
        )
        db.add(vendor)
        db.flush()

        menu = [
            ("Jollof Rice", "Smoky party-style jollof", "2500.00"),
            ("Fried Rice", "With chicken or beef", "2500.00"),
            ("Egusi Soup", "With pounded yam or semo", "3500.00"),
            ("Asun", "Spicy grilled goat meat", "3000.00"),
            ("Grilled Turkey", "Per portion", "1500.00"),
            ("Pounded Yam", "Served with your soup", "1000.00"),
            ("Small Coke", "35cl bottle", "300.00"),
            ("Zobo", "Chilled hibiscus drink", "400.00"),
        ]
        for name, desc, price in menu:
            db.add(
                MenuItem(
                    vendor_id=vendor.id,
                    name=name,
                    description=desc,
                    price=Decimal(price),
                    status="available",
                )
            )

        db.add(
            Customer(
                whatsapp_phone="+2348012345678",
                name="Demo Eater",
                default_address="Yaba, Lagos",
            )
        )
        db.commit()
        print("Seeded vendor #1 'Madam Grace Kitchen' with 8 menu items.")
    finally:
        db.close()


if __name__ == "__main__":
    main()