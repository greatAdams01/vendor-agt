from fastapi import APIRouter

from app.api import menu, orders, webhooks

api_router = APIRouter()
api_router.include_router(webhooks.router, tags=["webhooks"])
api_router.include_router(orders.router, prefix="/vendors/{vendor_id}/orders", tags=["orders"])
api_router.include_router(menu.router, prefix="/vendors/{vendor_id}/menu", tags=["menu"])