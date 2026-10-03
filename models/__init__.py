"""SQLAlchemy models for Paseo Aranjuez.

Importing this package registers every table on the shared metadata, which is
what lets `db.create_all()` and the relationship resolution between users,
businesses, products, orders, rewards, coupons and transactions work.
"""

from extensions import db
from models.base import TimestampMixin, new_pickup_code, new_qr_code, new_uuid
from models.business import Business
from models.orders import Order, OrderItem
from models.products import Product
from models.rewards import Coupon, Reward, new_coupon_code
from models.transactions import Transaction
from models.users import (
    GoogleAccountConflict,
    User,
    find_user_by_email,
    find_user_by_google_sub,
    find_user_by_id,
    find_user_by_qr_code,
    get_or_create_google_user,
    reset_store,
    set_user_role,
    update_user_location,
)

__all__ = [
    "db",
    "TimestampMixin",
    "new_uuid",
    "new_qr_code",
    "new_pickup_code",
    "new_coupon_code",
    "User",
    "Business",
    "Product",
    "Order",
    "OrderItem",
    "Reward",
    "Coupon",
    "Transaction",
    "find_user_by_id",
    "find_user_by_google_sub",
    "find_user_by_qr_code",
    "find_user_by_email",
    "get_or_create_google_user",
    "set_user_role",
    "update_user_location",
    "reset_store",
    "GoogleAccountConflict",
]
