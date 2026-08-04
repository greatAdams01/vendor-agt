from __future__ import annotations

from decimal import ROUND_CEILING, Decimal
from math import asin, cos, radians, sin, sqrt

EARTH_RADIUS_KM = 6371.0

DEFAULT_FALLBACK_FEE = Decimal("500")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres between two lat/lng points."""
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(a))


def compute_delivery_fee(
    *,
    vendor,
    dropoff_latitude: float | None,
    dropoff_longitude: float | None,
    fallback_fee: Decimal = DEFAULT_FALLBACK_FEE,
) -> tuple[Decimal, float | None]:
    """Distance-based dispatch-rider fee.

    Fee = vendor.delivery_base_fee + (vendor.delivery_rate_per_km x km).
    Rounded up to the nearest whole naira. Falls back to a flat fee when the
    vendor or customer has no coordinates. Returns (fee, distance_km).
    """
    has_vendor_origin = vendor.vendor_latitude and vendor.vendor_longitude
    if not has_vendor_origin or dropoff_latitude is None or dropoff_longitude is None:
        return fallback_fee, None

    km = haversine_km(
        float(vendor.vendor_latitude),
        float(vendor.vendor_longitude),
        float(dropoff_latitude),
        float(dropoff_longitude),
    )
    base = Decimal(vendor.delivery_base_fee or 0)
    rate = Decimal(vendor.delivery_rate_per_km or 0)
    fee = base + (rate * Decimal(str(km)))
    fee = fee.quantize(Decimal("1"), rounding=ROUND_CEILING)
    return fee, round(km, 2)
