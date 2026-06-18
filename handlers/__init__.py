from .common import common_router
from .products import products_router
from .admin import admin_router
from .callbacks import callbacks_router
from .auth import auth_router

__all__ = [
    "common_router",
    "products_router",
    "admin_router",
    "callbacks_router",
    "auth_router",
]
