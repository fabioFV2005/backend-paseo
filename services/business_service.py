"""Business registration and management.

`register_business` is the answer to a question the original backend could not
answer: how does anyone ever become a SELLER? ``set_user_role`` existed but
nothing called it, so the SELLER half of the system was unreachable in
practice. This is the authorized application flow its docstring asked for.

The decision it encodes: **self-registration creates an INACTIVE business.**
A person can sign up and publish a catalog during a demo without an admin
having to pre-approve them, but an inactive business cannot accrue points
liability, cannot receive orders and does not appear in the marketplace. An
admin activates it with one call. That keeps the demo frictionless without
letting the public create financial obligations on Paseo Aranjuez's behalf.

Promoting to ADMIN is never possible here. Only ``api/admin.py`` does that.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from flask import current_app

from extensions import db
from models import (
    Business,
    Transaction,
    User,
    find_user_by_id,
    set_user_role,
)
from roles import UserRole
from services.catalog import get_business_or_404
from services.errors import ConflictError, ForbiddenError, NotFoundError, ValidationError


def get_business_for_owner(user: User) -> Optional[Business]:
    """The business owned by this user, or None if they have not registered one."""
    if user is None or user.business is None:
        return None
    return user.business


def require_business(user: User) -> Business:
    """The caller's own business, or a 403 explaining they need to register."""
    business = get_business_for_owner(user)
    if business is None:
        raise ForbiddenError(
            "This action requires a registered business",
            details={"hint": "POST /api/business/register first"},
        )
    if not business.active:
        raise ForbiddenError(
            "This business is not active yet; ask a Paseo Aranjuez admin to "
            "activate it"
        )
    return business


def _decimal_field(data: dict, field: str, *, required: bool, current=None):
    if field not in data:
        if required and current is None:
            raise ValidationError(f"'{field}' is required")
        return current
    try:
        return Decimal(str(data[field]))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError(f"'{field}' must be a number")


def register_business(user: User, data: dict) -> Business:
    """Turn a signed-in customer into a shop owner.

    Idempotent in the useful sense: a user who already has a business gets a
    409 with a pointer to it rather than a second row, because `business.user_id`
    is UNIQUE and a second INSERT would fail with a raw integrity error.
    """
    existing = get_business_for_owner(user)
    if existing is not None:
        raise ConflictError(
            "You already have a business registered",
            details={"business_id": str(existing.id)},
        )

    name = (data.get("name") or "").strip()
    if not name:
        raise ValidationError("'name' is required")

    points_per_bs = _decimal_field(data, "points_per_bs", required=False, current=None)
    if points_per_bs is None:
        points_per_bs = Decimal("1")
    if points_per_bs <= 0:
        raise ValidationError("'points_per_bs' must be greater than 0")

    if "active" in data:
        # Not merely ignored -- rejected. Silently dropping the field would let
        # a client believe it had activated its shop when it had not, and the
        # shop would sit invisible until an admin noticed.
        #
        # 403 rather than 400, matching update_business(): both are the same
        # rule ("only an admin may change the approval status") seen from two
        # endpoints, and one rule must produce one status code.
        raise ForbiddenError("Only a Paseo Aranjuez admin can change 'active'")

    offers_delivery = bool(data.get("offers_delivery", False))
    delivery_fee_bs = _decimal_field(data, "delivery_fee_bs", required=False, current=Decimal("0.00"))
    if delivery_fee_bs is not None and delivery_fee_bs < 0:
        raise ValidationError("'delivery_fee_bs' cannot be negative")

    business = Business(
        user_id=user.id,
        name=name,
        category=(data.get("category") or "").strip() or None,
        description=(data.get("description") or "").strip() or None,
        location=(data.get("location") or "").strip() or None,
        points_per_bs=points_per_bs,
        offers_delivery=offers_delivery,
        delivery_fee_bs=delivery_fee_bs if delivery_fee_bs is not None else Decimal("0.00"),
        delivery_info=(data.get("delivery_info") or "").strip() or None,
        # Self-registered shops start inactive on purpose; see module docstring.
        active=False,
    )

    # Promote the owner inside this transaction rather than through
    # set_user_role(), which commits on its own: a separate commit would leave
    # a SELLER with no shop if the INSERT below failed, and a shop with a USER
    # owner if the commit succeeded and the response never got sent. One
    # commit covers both rows or neither exists.
    user.role = UserRole.SELLER.value

    db.session.add(business)
    db.session.commit()
    return business


def update_business(business: Business, data: dict) -> Business:
    """Edit the shop profile. ``active`` is NOT editable here: only an admin
    may activate or suspend a business, otherwise any shop could re-enable
    itself after being suspended."""
    if "name" in data:
        name = (data.get("name") or "").strip()
        if not name:
            raise ValidationError("'name' cannot be empty")
        business.name = name
    if "category" in data:
        business.category = (data.get("category") or "").strip() or None
    if "description" in data:
        business.description = (data.get("description") or "").strip() or None
    if "location" in data:
        business.location = (data.get("location") or "").strip() or None
    if "points_per_bs" in data:
        rate = _decimal_field(data, "points_per_bs", required=True)
        if rate <= 0:
            raise ValidationError("'points_per_bs' must be greater than 0")
        business.points_per_bs = rate
    if "offers_delivery" in data:
        business.offers_delivery = bool(data["offers_delivery"])
    if "delivery_fee_bs" in data:
        fee = _decimal_field(data, "delivery_fee_bs", required=False)
        if fee is not None and fee < 0:
            raise ValidationError("'delivery_fee_bs' cannot be negative")
        business.delivery_fee_bs = fee if fee is not None else Decimal("0.00")
    if "delivery_info" in data:
        business.delivery_info = (data.get("delivery_info") or "").strip() or None
    if "active" in data:
        raise ForbiddenError("Only a Paseo Aranjuez admin can change 'active'")

    db.session.commit()
    return business


def admin_set_business_active(business: Business, active: bool) -> Business:
    """The admin-only switch that approves or suspends a shop."""
    business.active = active
    db.session.commit()
    return business


def list_business_transactions(
    business: Business, *, limit: int = 50, offset: int = 0
) -> list[Transaction]:
    """Movements this business caused (reto 3.6, "consultar movimientos")."""
    return list(
        db.session.execute(
            db.select(Transaction)
            .where(Transaction.business_id == business.id)
            .order_by(Transaction.created_at.desc())
            .limit(limit)
            .offset(offset)
        ).scalars()
    )


def find_recent_scan(
    customer: User, business: Business, window_seconds: int
) -> Optional[Transaction]:
    """The most recent EARN this business caused for this customer inside the
    window, or None.

    Scoped to (customer, business) rather than to the customer alone: a customer
    legitimately shopping in three different shops in one minute is normal,
    whereas one customer scanning twice at the same shop in one minute is not.

    Scoped to EARN specifically, so a REFUND or an admin ADJUST does not block
    the next real purchase.
    """
    if window_seconds <= 0:
        return None

    from enums import TransactionType

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    return db.session.execute(
        db.select(Transaction)
        .where(
            Transaction.customer_id == customer.id,
            Transaction.business_id == business.id,
            Transaction.type == TransactionType.EARN.value,
            Transaction.created_at >= cutoff,
        )
        .order_by(Transaction.created_at.desc())
        .limit(1)
    ).scalars().first()


def credit_points_by_qr(business: Business, qr_code: str, amount_bs) -> Transaction | None:
    """The core of reto 3.7: the business scans the customer's QR, types the
    amount, and the points land on the customer's account.

    Refuses a repeat of the same (customer, shop) pair inside
    ``config.SCAN_DUPLICATE_WINDOW_SECONDS``. Without that, a shop and a customer
    in collusion could scan the pair in a loop and mint an unlimited balance,
    and a buggy point-of-sale could double-post a sale by retrying the request.
    The rate limiter does not cover this: 60 scans a minute is plenty to farm.

    Returns the ledger entry, or None when the purchase is too small to earn a
    single point (see ``services.points.credit_purchase``).
    """
    from models import find_user_by_qr_code
    from services.points import credit_purchase

    customer = find_user_by_qr_code(qr_code)
    if customer is None:
        # 404, not 422: the code does not resolve to anything. Using NotFound
        # also means a near-miss code is indistinguishable from a wrong one, so
        # the endpoint cannot be used to test whether a code exists.
        raise NotFoundError(
            "No customer matches that QR code", details={"qr_code": qr_code}
        )

    window = current_app.config.get("SCAN_DUPLICATE_WINDOW_SECONDS", 60)
    recent = find_recent_scan(customer, business, window)
    if recent is not None:
        # 409, not 429: the request is well-formed and the caller is who they
        # claim to be, the request simply conflicts with recent state. 429 is
        # reserved for the rate limiter, which is a different problem.
        raise ConflictError(
            f"This customer was already credited at {business.name} within the "
            f"last {window} seconds",
            details={
                "retry_after_seconds": window,
                "previous_transaction_id": str(recent.id),
                "previous_amount_bs": str(recent.amount_bs or ""),
                "scanned_at": recent.created_at.isoformat() if recent.created_at else None,
            },
            headers={"Retry-After": window},
        )

    return credit_purchase(customer, business, amount_bs)
