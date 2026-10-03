"""Business panel: catalog management, QR crediting, coupon validation, orders.

Covers the "Establecimiento" role of reto 3.6 and the commerce side of reto 5.7.

Every route starts from `require_business`, which resolves the caller's own
business and refuses an inactive one. Routes then assert ownership of whatever
they touch, so a seller cannot read or write another shop's data by guessing an
id: a cross-tenant id comes back as 403 or 404, never as somebody else's rows.
"""

from decimal import Decimal, InvalidOperation
from typing import Optional

from flask import Blueprint, jsonify, request

from auth import get_current_user, require_seller_or_admin
from enums import OrderStatus
from extensions import limiter
from services.business_service import (
    credit_points_by_qr,
    get_business_for_owner,
    list_business_transactions,
    register_business,
    require_business,
    update_business,
)
from services.catalog import (
    assert_owns_product,
    create_product,
    get_product,
    list_products,
    update_product,
)
from services.errors import (
    UnauthorizedError,
    ValidationError,
)
from services.orders_service import (
    advance_order_status,
    assert_business_owns,
    cancel_order,
    get_order,
    list_business_orders,
    validate_pickup,
)
from services.points import get_balance
from services.rewards_service import validate_coupon

business_bp = Blueprint("business", __name__)


def current_user_or_401():
    user = get_current_user()
    if user is None:
        raise UnauthorizedError("Authentication required")
    return user


def _json_body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError("Request body must be a JSON object")
    return data


def _pagination_args() -> tuple[int, int]:
    try:
        limit = int(request.args.get("limit", 50))
        offset = int(request.args.get("offset", 0))
    except (TypeError, ValueError):
        raise ValidationError("'limit' and 'offset' must be integers")
    return max(1, min(limit, 200)), max(0, offset)


def _amount_from(data: dict, field: str = "amount_bs") -> Decimal:
    """Parse a purchase amount. Money never passes through a float."""
    raw = data.get(field)
    if raw is None:
        raise ValidationError(f"'{field}' is required")
    try:
        amount = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError(f"'{field}' must be a number")
    if amount <= 0:
        raise ValidationError(f"'{field}' must be greater than 0")
    return amount.quantize(Decimal("0.01"))


# --- Registration and profile ----------------------------------------------


@business_bp.post("/business/register")
def register():
    """Turn the signed-in customer into a shop owner.

    This is the authorized flow that ``models.users.set_user_role`` requires:
    it is what makes the SELLER role reachable at all. The business starts
    inactive, so a Paseo admin approves it before it can accrue points.
    """
    user = current_user_or_401()
    business = register_business(user, _json_body())
    return jsonify({"business": business.to_dict(), "user": user.to_dict()}), 201


@business_bp.get("/business/me")
def my_business():
    """The caller's own shop, or null if they have not registered one."""
    user = current_user_or_401()
    business = get_business_for_owner(user)
    return jsonify({"business": business.to_dict() if business else None})


@business_bp.patch("/business/me")
@require_seller_or_admin
def update_my_business():
    """Edit the shop profile (name, category, description, location, rate)."""
    business = require_business(current_user_or_401())
    return jsonify({"business": update_business(business, _json_body()).to_dict()})


# --- Paseo Points: crediting a purchase ------------------------------------


# A shop could otherwise farm points by re-scanning the same customer over and
# over. This caps the blast radius; it does not replace the duplicate-window
# check in the service, because a burst of 30 scans stays under this limit.
#
# @post must sit above @limiter.limit, for the same reason as in auth_routes.
@business_bp.post("/business/scan")
@limiter.limit("60 per minute")
@require_seller_or_admin
def scan_qr():
    """The QR-scan flow of reto 3.7, steps 4-6.

    Body: {"qr_code": "3e128a2bb3bb47dc", "amount_bs": 100}

    The shop scans the customer's QR, types the amount, and the points are
    credited at this business's own rate. Returns 200 with
    ``points_credited: 0`` for a purchase too small to earn a point, rather
    than failing the sale.
    """
    business = require_business(current_user_or_401())
    data = _json_body()

    qr_code = (data.get("qr_code") or "").strip()
    if not qr_code:
        raise ValidationError("'qr_code' is required")

    entry = credit_points_by_qr(business, qr_code, _amount_from(data))
    if entry is None:
        return jsonify(
            {
                "points_credited": 0,
                "message": "The purchase is too small to earn a point at this rate",
            }
        )

    return (
        jsonify(
            {
                "points_credited": entry.points,
                "customer": {
                    "id": str(entry.customer.id),
                    "name": entry.customer.name,
                    "level": entry.customer.level,
                },
                "transaction": entry.to_dict(),
            }
        ),
        201,
    )


@business_bp.get("/business/transactions")
@require_seller_or_admin
def business_transactions():
    """Movements this shop caused (reto 3.6)."""
    business = require_business(current_user_or_401())
    limit, offset = _pagination_args()
    entries = list_business_transactions(business, limit=limit, offset=offset)
    return jsonify({"items": [entry.to_dict() for entry in entries]})


@business_bp.post("/business/coupons/validate")
@require_seller_or_admin
def validate_shop_coupon():
    """Validate a customer's coupon at the counter. Reto 3.10 step 9.

    Body: {"code": "A1B2C3D4"}
    """
    business = require_business(current_user_or_401())
    code = (_json_body().get("code") or "").strip()
    if not code:
        raise ValidationError("'code' is required")
    coupon = validate_coupon(code, business)
    return jsonify({"coupon": coupon.to_dict(), "valid": True})


# --- PaseoYa: catalog management -------------------------------------------


@business_bp.get("/business/products")
@require_seller_or_admin
def business_products():
    """This shop's own catalog, including deactivated items."""
    business = require_business(current_user_or_401())
    limit, offset = _pagination_args()
    rows = list_products(
        business_id=business.id,
        include_inactive=request.args.get("include_inactive", "").lower()
        in {"1", "true", "yes"},
        limit=limit,
        offset=offset,
    )
    return jsonify({"items": [row.to_dict() for row in rows]})


@business_bp.post("/business/products")
@require_seller_or_admin
def business_create_product():
    """Publish a product (reto 5.7 "registrar productos")."""
    business = require_business(current_user_or_401())
    return jsonify({"product": create_product(business, _json_body()).to_dict()}), 201


@business_bp.patch("/business/products/<product_id>")
@require_seller_or_admin
def business_update_product(product_id):
    """Edit price, stock or details (reto 5.7 "administrar inventario")."""
    business = require_business(current_user_or_401())
    product = get_product(product_id)
    assert_owns_product(business, product)
    return jsonify({"product": update_product(product, _json_body()).to_dict()})


# --- PaseoYa: orders --------------------------------------------------------


@business_bp.get("/business/orders")
@require_seller_or_admin
def list_orders():
    """Incoming orders, oldest first: the queue for the day (reto 5.7)."""
    business = require_business(current_user_or_401())
    limit, offset = _pagination_args()
    orders = list_business_orders(
        business.id, status=request.args.get("status"), limit=limit, offset=offset
    )
    return jsonify({"items": [order.to_dict() for order in orders]})


@business_bp.get("/business/orders/<order_id>")
@require_seller_or_admin
def order_detail(order_id):
    business = require_business(current_user_or_401())
    order = get_order(order_id)
    assert_business_owns(business, order)
    return jsonify(order.to_dict())


@business_bp.patch("/business/orders/<order_id>/status")
@require_seller_or_admin
def update_order_status(order_id):
    """Advance an order along the lifecycle of reto 5.8.

    Body: {"status": "preparing"}

    Moving to ``delivered`` is what credits the customer's loyalty points.
    """
    business = require_business(current_user_or_401())
    order = get_order(order_id)
    new_status = _json_body().get("status")
    if not new_status:
        raise ValidationError("'status' is required")
    advance_order_status(order, new_status, business)
    return jsonify({"order": order.to_dict()})


@business_bp.post("/business/orders/<order_id>/pickup")
@require_seller_or_admin
def redeem_pickup(order_id):
    """Validate the pickup code and hand the order over. Reto 5.9.

    Body: {"code": "482913"}

    Delivering credits the customer's points via the same ledger entry the QR
    scan produces, linked to this order.
    """
    business = require_business(current_user_or_401())
    order = get_order(order_id)
    code = _json_body().get("code")
    if not code:
        raise ValidationError("'code' is required")
    validate_pickup(order, str(code), business)
    return jsonify({"order": order.to_dict(), "points_credited": order.points_earned})


@business_bp.post("/business/orders/<order_id>/cancel")
@require_seller_or_admin
def cancel_business_order(order_id):
    """Cancel an order; the stock goes back on the shelf."""
    business = require_business(current_user_or_401())
    order = get_order(order_id)
    cancel_order(order, reason=_json_body().get("reason"), business=business)
    return jsonify({"order": order.to_dict()})
