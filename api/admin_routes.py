"""Paseo Aranjuez administration.

Covers the "Administrador de Paseo Aranjuez" role of reto 3.6: manage users and
their roles, register and activate businesses, administer promotions, create
rewards, set point exchange rates, review movements and read statistics.

`PATCH /api/admin/users/<id>/role` is the endpoint the original backend was
missing. `set_user_role` existed with a docstring demanding "an authorized
application flow", and this is it: without this route nobody could ever become
an ADMIN or a SELLER through the application, so half the authorization design
was unreachable.
"""

from decimal import Decimal, InvalidOperation

from flask import Blueprint, jsonify, request

from auth import require_admin
from enums import CouponStatus, OrderStatus, TransactionType
from extensions import db
from models import (
    Business,
    Coupon,
    Order,
    Product,
    Reward,
    Transaction,
    User,
    find_user_by_id,
    set_user_role,
)
from roles import UserRole
from services.business_service import admin_set_business_active
from services.catalog import get_business_or_404
from services.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from services.points import adjust_points, get_balance
from services.rewards_service import create_reward, get_reward_or_404, update_reward

admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")


def _json_body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError("Request body must be a JSON object")
    return data


def _decimal(data: dict, field: str):
    try:
        return Decimal(str(data[field]))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError(f"'{field}' must be a number")


# --- Users and roles --------------------------------------------------------


@admin_bp.get("/users")
@require_admin
def list_users():
    """All users, richest balance first (reto 3.6 "gestionar usuarios").

    ``ORDER BY points_balance`` works because the balance is a column, which is
    exactly why it is a correlated subquery rather than a Python-side sum.
    """
    limit = max(1, min(int(request.args.get("limit", 50)), 200))
    offset = max(0, int(request.args.get("offset", 0)))
    role = request.args.get("role")

    query = db.select(User)
    if role:
        if UserRole.parse(role) is None:
            raise ValidationError("Unknown role")
        query = query.where(User.role == role)

    users = list(
        db.session.execute(
            query.order_by(User.points_balance.desc(), User.created_at.asc())
            .limit(limit)
            .offset(offset)
        ).scalars()
    )
    return jsonify(
        {
            "items": [
                {**user.to_dict(include_private=True), "points": user.points_balance}
                for user in users
            ]
        }
    )


@admin_bp.patch("/users/<user_id>/role")
@require_admin
def update_user_role(user_id):
    """Assign a role. The only path to SELLER/ADMIN besides self-registration.

    Body: {"role": "SELLER"}

    Two guards prevent an admin from locking everyone out: the caller cannot
    demote themselves, and the last remaining admin cannot be demoted at all.
    """
    data = _json_body()
    raw_role = data.get("role")
    role = UserRole.parse(raw_role) if raw_role else None
    if role is None:
        raise ValidationError(
            "'role' must be one of: " + ", ".join(UserRole.values())
        )

    target = find_user_by_id(user_id)
    if target is None:
        raise NotFoundError("User not found")

    from flask import g

    if target.id == g.current_user.id and role is not UserRole.ADMIN:
        raise ConflictError("You cannot remove your own admin role")

    if target.role_enum is UserRole.ADMIN and role is not UserRole.ADMIN:
        remaining = db.session.execute(
            db.select(db.func.count(User.id)).where(
                User.role == UserRole.ADMIN.value, User.id != target.id
            )
        ).scalar_one()
        if remaining == 0:
            raise ConflictError("This is the last admin; promote someone else first")

    set_user_role(target.id, role)
    return jsonify({"user": target.to_dict()})


@admin_bp.post("/users/<user_id>/points")
@require_admin
def adjust_user_points(user_id):
    """Manual balance correction.

    Body: {"points": 500, "note": "Compensacion por compra mal registrada"}

    ``note`` is required in practice (enforced here) because an unexplained
    balance change in an append-only ledger is indistinguishable from a bug.
    """
    data = _json_body()
    raw_points = data.get("points")
    if raw_points is None:
        raise ValidationError("'points' is required")
    try:
        delta = int(raw_points)
    except (TypeError, ValueError):
        raise ValidationError("'points' must be an integer")
    if delta == 0:
        raise ValidationError("'points' cannot be zero")

    note = (data.get("note") or "").strip()
    if not note:
        raise ValidationError("'note' is required for a manual adjustment")

    target = find_user_by_id(user_id)
    if target is None:
        raise NotFoundError("User not found")

    entry = adjust_points(target, delta, note=note)
    return (
        jsonify(
            {
                "transaction": entry.to_dict(),
                "balance": get_balance(target),
                "level": target.level,
            }
        ),
        201,
    )


@admin_bp.get("/users/<user_id>")
@require_admin
def user_detail(user_id):
    """One user with their balance, level, business and recent activity."""
    user = find_user_by_id(user_id)
    if user is None:
        raise NotFoundError("User not found")
    payload = user.to_dict(include_private=True)
    payload["points"] = user.points_balance
    payload["business"] = user.business.to_dict() if user.business else None
    payload["recent_transactions"] = [
        entry.to_dict()
        for entry in db.session.execute(
            db.select(Transaction)
            .where(Transaction.customer_id == user.id)
            .order_by(Transaction.created_at.desc())
            .limit(10)
        ).scalars()
    ]
    return jsonify(payload)


# --- Businesses -------------------------------------------------------------


@admin_bp.get("/businesses")
@require_admin
def list_businesses():
    """Every business, active or not (reto 3.6 "registrar establecimientos")."""
    rows = db.session.execute(
        db.select(Business).order_by(Business.created_at.asc())
    ).scalars()
    return jsonify({"items": [row.to_dict(include_owner=True) for row in rows]})


@admin_bp.patch("/businesses/<business_id>")
@require_admin
def update_business_admin(business_id):
    """Activate, suspend or change a shop's exchange rate.

    Body: {"active": true} or {"points_per_bs": 1.5}
    """
    business = get_business_or_404(business_id)
    data = _json_body()

    if "active" in data:
        admin_set_business_active(business, bool(data["active"]))

    if "points_per_bs" in data:
        rate = _decimal(data, "points_per_bs")
        if rate <= 0:
            raise ValidationError("'points_per_bs' must be greater than 0")
        business.points_per_bs = rate

    db.session.commit()
    return jsonify({"business": business.to_dict(include_owner=True)})


# --- Rewards ----------------------------------------------------------------


@admin_bp.get("/rewards")
@require_admin
def list_rewards():
    """All rewards, including retired ones."""
    rows = db.session.execute(
        db.select(Reward).order_by(Reward.created_at.asc())
    ).scalars()
    return jsonify({"items": [row.to_dict() for row in rows]})


@admin_bp.post("/rewards")
@require_admin
def create_reward_endpoint():
    """Create a reward. Omit ``business_id`` to sponsor it from Paseo itself.

    Body:
        {"title": "10% de descuento", "discount_type": "percent",
         "discount_value": 10, "max_discount_bs": 30,
         "points_cost": 300, "valid_days": 30, "stock": 100}
    """
    data = _json_body()

    business = None
    if data.get("business_id"):
        business = get_business_or_404(data["business_id"])

    reward = create_reward(data, business=business)
    return jsonify({"reward": reward.to_dict()}), 201


@admin_bp.patch("/rewards/<reward_id>")
@require_admin
def update_reward_endpoint(reward_id):
    """Edit or retire a reward.

    Existing coupons keep their snapshotted terms, so editing a reward never
    changes what an already-issued coupon is worth.
    """
    reward = update_reward(get_reward_or_404(reward_id), _json_body())
    return jsonify({"reward": reward.to_dict()})


# --- Dashboard --------------------------------------------------------------


@admin_bp.get("/stats")
@require_admin
def stats():
    """Headline numbers for the admin dashboard (reto 3.6 "estadisticas").

    All count(*) over an index, so this stays cheap as the ledger grows.
    """
    def count(model, *where):
        query = db.select(db.func.count(model.id))
        for clause in where:
            query = query.where(clause)
        return int(db.session.execute(query).scalar_one())

    def sum_points(model, *where):
        query = db.select(db.func.coalesce(db.func.sum(model.points), 0))
        for clause in where:
            query = query.where(clause)
        return int(db.session.execute(query).scalar_one())

    top_businesses = db.session.execute(
        db.select(
            Business.id,
            Business.name,
            db.func.count(Order.id).label("orders"),
        )
        .join(Order, Order.business_id == Business.id)
        .where(Order.status == OrderStatus.DELIVERED.value)
        .group_by(Business.id, Business.name)
        .order_by(db.func.count(Order.id).desc())
        .limit(5)
    ).all()

    top_customers = db.session.execute(
        db.select(User.id, User.name, User.points_balance.label("points"))
        .order_by(User.points_balance.desc())
        .limit(5)
    ).all()

    return jsonify(
        {
            "users": count(User),
            "customers": count(User, User.role == UserRole.USER.value),
            "businesses": count(Business),
            "active_businesses": count(Business, Business.active.is_(True)),
            "products": count(Product),
            "rewards": count(Reward),
            "orders": count(Order),
            "orders_delivered": count(Order, Order.status == OrderStatus.DELIVERED.value),
            "orders_open": count(
                Order, Order.status.in_(OrderStatus.open_states())
            ),
            "coupons_issued": count(Coupon),
            "coupons_active": count(Coupon, Coupon.status == CouponStatus.ACTIVE.value),
            "points_in_circulation": sum_points(Transaction),
            "points_earned": sum_points(
                Transaction, Transaction.type == TransactionType.EARN.value
            ),
            "points_spent": sum_points(
                Transaction, Transaction.type == TransactionType.REDEEM.value
            ),
            "top_businesses": [
                {"id": str(row.id), "name": row.name, "orders": row.orders}
                for row in top_businesses
            ],
            "top_customers": [
                {"id": str(row.id), "name": row.name, "points": row.points}
                for row in top_customers
            ],
        }
    )
