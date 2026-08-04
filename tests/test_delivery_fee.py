from decimal import Decimal

import pytest

from app.services.delivery_fee_service import (
    DEFAULT_FALLBACK_FEE,
    compute_delivery_fee,
    haversine_km,
)


class FakeVendor:
    vendor_latitude = 6.5244  # Yaba, Lagos
    vendor_longitude = 3.3792
    delivery_base_fee = Decimal("1000")
    delivery_rate_per_km = Decimal("300")


def test_haversine_zero_distance():
    assert haversine_km(6.5244, 3.3792, 6.5244, 3.3792) == 0


def test_haversine_yaba_to_ikeja():
    km = haversine_km(6.5244, 3.3792, 6.4541, 3.3947)
    assert 7.0 <= km <= 9.0


def test_compute_delivery_fee_distance_based():
    fee, km = compute_delivery_fee(
        vendor=FakeVendor(),
        dropoff_latitude=6.4541,
        dropoff_longitude=3.3947,
    )
    assert km is not None
    expected = FakeVendor.delivery_base_fee + FakeVendor.delivery_rate_per_km * Decimal(str(km))
    assert fee >= expected
    assert fee <= expected + Decimal("1")  # rounds up to the nearest whole naira


def test_compute_delivery_fee_pickup_is_zero():
    fee, km = compute_delivery_fee(
        vendor=FakeVendor(),
        dropoff_latitude=6.4541,
        dropoff_longitude=3.3947,
        fallback_fee=Decimal("0"),
    )
    assert fee > 0


def test_compute_delivery_fee_falls_back_without_coordinates():
    fee, km = compute_delivery_fee(vendor=FakeVendor(), dropoff_latitude=None, dropoff_longitude=None)
    assert km is None
    assert fee == DEFAULT_FALLBACK_FEE


def test_compute_delivery_fee_falls_back_without_vendor_origin():
    class NoOrigin(FakeVendor):
        vendor_latitude = None
        vendor_longitude = None

    fee, km = compute_delivery_fee(
        vendor=NoOrigin(), dropoff_latitude=6.4541, dropoff_longitude=3.3947
    )
    assert km is None
    assert fee == DEFAULT_FALLBACK_FEE
