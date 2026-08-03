from __future__ import annotations

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import MenuItem, Vendor
from app.models.enums import MenuItemStatus

router = APIRouter()


class MenuItemCreate(BaseModel):
    name: str
    description: str | None = None
    price: float = Field(gt=0)


class StatusToggle(BaseModel):
    status: MenuItemStatus


@router.get("/")
def list_menu(vendor_id: int, db: Session = Depends(get_db)) -> dict:
    _ensure_vendor(db, vendor_id)
    items = (
        db.query(MenuItem)
        .filter(MenuItem.vendor_id == vendor_id)
        .order_by(MenuItem.id)
        .all()
    )
    return {
        "menu": [
            {
                "id": i.id,
                "name": i.name,
                "description": i.description,
                "price": float(i.price),
                "status": i.status,
            }
            for i in items
        ]
    }


@router.post("/")
def create_item(vendor_id: int, body: MenuItemCreate, db: Session = Depends(get_db)) -> dict:
    _ensure_vendor(db, vendor_id)
    item = MenuItem(
        vendor_id=vendor_id,
        name=body.name,
        description=body.description,
        price=body.price,
        status=MenuItemStatus.AVAILABLE.value,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return {"id": item.id}


@router.patch("/{item_id}/status")
def toggle_status(
    vendor_id: int, item_id: int, body: StatusToggle, db: Session = Depends(get_db)
) -> dict:
    _ensure_vendor(db, vendor_id)
    item = (
        db.query(MenuItem)
        .filter(MenuItem.id == item_id, MenuItem.vendor_id == vendor_id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Menu item not found")
    item.status = body.status.value
    db.commit()
    return {"ok": True, "status": item.status}


def _ensure_vendor(db: Session, vendor_id: int) -> None:
    if db.query(Vendor).filter(Vendor.id == vendor_id).first() is None:
        raise HTTPException(status_code=404, detail="Vendor not found")