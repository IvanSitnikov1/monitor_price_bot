from aiogram import Router

from .common import auth_common_router
from .ozon import auth_ozon_router
from .wildberries import auth_wildberries_router


auth_router = Router()
auth_router.include_router(auth_ozon_router)
auth_router.include_router(auth_wildberries_router)
auth_router.include_router(auth_common_router)

__all__ = [
    "auth_router",
]
