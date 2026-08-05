from __future__ import annotations

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Vendor

router = APIRouter()


class AvailabilityUpdate(BaseModel):
    is_available: bool


@router.get("/{vendor_id}")
def get_vendor(vendor_id: int, db: Session = Depends(get_db)) -> dict:
    vendor = _get_vendor(db, vendor_id)
    return {
        "id": vendor.id,
        "name": vendor.name,
        "is_available": vendor.is_available,
    }


@router.patch("/{vendor_id}/availability")
def set_availability(
    vendor_id: int, body: AvailabilityUpdate, db: Session = Depends(get_db)
) -> dict:
    vendor = _get_vendor(db, vendor_id)
    vendor.is_available = body.is_available
    db.commit()
    return {"ok": True, "is_available": vendor.is_available}


def _get_vendor(db: Session, vendor_id: int) -> Vendor:
    vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if vendor is None:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return vendor
