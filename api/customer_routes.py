"""Customer-facing endpoints: the Paseo Points screens and PaseoYa ordering.

Everything here requires a signed-in user but no particular role: a customer is
the default role. The endpoints map one-to-one onto the customer's journey in
reto 3.10:

    account created -> GET  /api/me/qr          (step 2, receive the QR)
    shop scans it   -> ...                       (steps 4-6, business side)
    browse rewards  -> GET  /api/rewards        (step 7)
    redeem          -> POST /api/rewards/<id>/redeem   (step 8)
    shop validates  -> ...                       (step 9, business side)
"""

from flask import Blueprint, g, jsonify, request

from auth import get_current_user
from models import Coupon
from services.errors import UnauthorizedError, ValidationError
from services.orders_service import (
    create_order,
    get_order_for_customer,
    list_customer_orders,
    mark_customer_arrived,
)
from services.points import get_balance, get_level_progress, list_transactions
from services.rewards_service import (
    get_coupon_for_customer,
    list_customer_coupons,
    list_rewards,
    redeem_reward,
)

customer_bp = Blueprint("customer", __name__)


def current_customer():
    user = get_current_user()
    if user is None:
        raise UnauthorizedError("Authentication required")
    return user


def _pagination_args() -> tuple[int, int]:
    """Read `limit`/`offset` from the query string, clamped to sane values.

    Unbounded limits are how one request turns into a full table dump.
    """
    try:
        limit = int(request.args.get("limit", 50))
        offset = int(request.args.get("offset", 0))
    except (TypeError, ValueError):
        raise ValidationError("'limit' and 'offset' must be integers")
    return max(1, min(limit, 200)), max(0, offset)


# --- Points -----------------------------------------------------------------


@customer_bp.get("/me/points")
def my_points():
    """Balance, tier and progress towards the next one. Reto 3.6."""
    customer = current_customer()
    balance = get_balance(customer)
    return jsonify(
        {
            "points": balance,
            "level": customer.level,
            **get_level_progress(balance),
        }
    )


@customer_bp.get("/me/transactions")
def my_transactions():
    """Movement history, newest first. Reto 3.6 "revisar movimientos"."""
    customer = current_customer()
    limit, offset = _pagination_args()
    entries = list_transactions(customer, limit=limit, offset=offset)
    return jsonify(
        {
            "items": [entry.to_dict() for entry in entries],
            "balance": get_balance(customer),
            "limit": limit,
            "offset": offset,
        }
    )


@customer_bp.get("/me/qr")
def my_qr():
    """The customer's personal QR payload (reto 3.7).

    Returns the raw code, not an image: rendering is the frontend's job, and
    returning the string keeps the endpoint reusable for a profile screen, a
    printable badge or a NFC payload.
    """
    customer = current_customer()
    return jsonify(
        {
            "qr_code": customer.qr_code,
            "customer_id": str(customer.id),
            "name": customer.name,
        }
    )


# --- Rewards / coupons ------------------------------------------------------


@customer_bp.get("/rewards")
def rewards_catalog():
    """Benefits catalog (reto 3.6 "consultar beneficios").

    Public, like the product and business catalogs: a shopper decides whether
    Paseo is worth signing up for by looking at what they could get, and
    forcing an account first is a worse answer than showing it anonymously.

    When the caller happens to be signed in, their balance is included too, so
    the UI can grey out what they cannot afford without a second round trip.
    ``points`` is ``null`` for an anonymous visitor -- present but empty, not
    absent, so the frontend has one field to read.
    """
    limit, offset = _pagination_args()
    business_id = request.args.get("business_id")
    rewards = list_rewards(
        business_id=business_id or None,
        limit=limit,
        offset=offset,
    )

    user = get_current_user()
    return jsonify(
        {
            "items": [reward.to_dict() for reward in rewards],
            "points": get_balance(user) if user is not None else None,
            "limit": limit,
            "offset": offset,
        }
    )


@customer_bp.post("/rewards/<reward_id>/redeem")
def redeem(reward_id):
    """Exchange points for a coupon. Reto 3.10 step 8.

    Returns the new coupon with its code, which is what the customer shows at
    the counter.
    """
    customer = current_customer()
    coupon = redeem_reward(customer, reward_id)
    return (
        jsonify(
            {
                "coupon": coupon.to_dict(),
                "points": get_balance(customer),
                "level": customer.level,
            }
        ),
        201,
    )


@customer_bp.get("/me/coupons")
def my_coupons():
    """Coupons the customer holds (reto 3.10 step 7)."""
    customer = current_customer()
    limit, offset = _pagination_args()
    coupons = list_customer_coupons(customer, limit=limit, offset=offset)
    return jsonify(
        {
            "items": [coupon.to_dict() for coupon in coupons],
            "limit": limit,
            "offset": offset,
        }
    )


@customer_bp.get("/me/coupons/<coupon_id>")
def my_coupon_detail(coupon_id):
    customer = current_customer()
    return jsonify(get_coupon_for_customer(customer, coupon_id).to_dict())


# --- PaseoYa orders ---------------------------------------------------------


@customer_bp.post("/orders")
def checkout():
    """Create an order from a cart.

    Body:
        {"items": [{"product_id": "...", "quantity": 2}],
         "coupon_id": "...",                 # optional
         "coupon_code": "A1B2C3D4",          # optional alternative
         "payment_method": "cash",           # optional
         "note": "sin cebolla"}              # optional

    Only ids and quantities are accepted: prices come from the database.

    A coupon may be referenced by ``coupon_id`` (what the frontend has after
    calling GET /api/me/coupons) or by ``coupon_code`` (what a customer reads
    off a printed coupon and types in). Both are accepted because the frontend
    flow and the manual flow genuinely differ; passing both, or two different
    ones, is a 400 rather than a silent pick.

    Responds 201 with ``{"order": {...}}``, matching every other create/update
    endpoint in this API.
    """
    customer = current_customer()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError("Request body must be a JSON object")

    coupon_id = data.get("coupon_id")
    coupon_code = data.get("coupon_code")
    if coupon_id and coupon_code:
        # Ambiguous: refuse instead of guessing which one the caller meant.
        raise ValidationError("Send either 'coupon_id' or 'coupon_code', not both")

    order = create_order(
        customer,
        data.get("items"),
        coupon_id=coupon_id,
        coupon_code=coupon_code,
        payment_method=data.get("payment_method"),
        note=data.get("note"),
        delivery_type=data.get("delivery_type", "pickup"),
        delivery_address=data.get("delivery_address"),
        delivery_phone=data.get("delivery_phone"),
        delivery_instructions=data.get("delivery_instructions"),
    )
    return jsonify({"order": order.to_dict()}), 201


@customer_bp.get("/me/orders")
def my_orders():
    """Order history (reto 5.7 "consultar pedidos")."""
    customer = current_customer()
    limit, offset = _pagination_args()
    orders = list_customer_orders(
        customer, status=request.args.get("status"), limit=limit, offset=offset
    )
    return jsonify(
        {
            "items": [order.to_dict(include_items=False) for order in orders],
            "limit": limit,
            "offset": offset,
        }
    )


@customer_bp.get("/me/orders/<order_id>")
def my_order_detail(order_id):
    """Order detail including the pickup code (reto 5.5)."""
    customer = current_customer()
    return jsonify(get_order_for_customer(order_id, customer).to_dict())


@customer_bp.post("/me/orders/<order_id>/arrive")
def order_arrived(order_id):
    """Tell the business the customer has reached the Paseo. Reto 5.8."""
    customer = current_customer()
    order = mark_customer_arrived(get_order_for_customer(order_id, customer), customer)
    return jsonify({"order": order.to_dict()})
